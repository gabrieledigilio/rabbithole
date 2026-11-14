"""Dataset M (every version of every article) -> the raw material of the graph, as Parquet:

  intervals-NN.parquet   one row per link and period: (src, target, start, end, the versions that
                         added and removed it, the sentence around it when it was added)
  revisions-NN.parquet   the edit log: who saved each version, when, the summary, links added/removed
  leads-NN.parquet       every version of every article's opening paragraphs, as plain text
  articles-NN.parquet    one row per article

    python pipeline/extract.py data/m data/graph        # all 16 files, one process per core (six at most)

A link that is removed and put back within 48 hours (blanking, vandalism, a quick revert) keeps
its interval; raw_adds counts how many times it was really added.
"""
import glob
import multiprocessing
import os
import re
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import wikitext as w  # noqa: E402

MERGE_MS = 48 * 3600 * 1000
COLUMNS = ["page_id", "title", "rev_id", "timestamp", "user", "anonymous", "comment", "minor", "sha1", "bytes", "text"]


def table(columns):
    return {name: [] for name in columns}


class Output:
    def __init__(self):
        self.interval = table(["src", "target", "start_ms", "end_ms", "start_rev", "end_rev", "raw_adds", "context"])
        self.revision = table(["page_id", "rev_id", "ts_ms", "user", "anonymous", "minor", "comment", "bytes",
                               "n_links", "n_added", "n_removed", "is_revert"])
        self.lead = table(["page_id", "rev_id", "start_ms", "end_ms", "text"])
        self.article = table(["page_id", "title", "first_ms", "last_ms", "n_versions", "n_reverts", "bytes"])

    @staticmethod
    def add(tbl, *values):
        for column, value in zip(tbl.values(), values):
            column.append(value)

    def write(self, out_dir, no):
        for name in ("interval", "revision", "lead", "article"):
            pq.write_table(pa.table(getattr(self, name)), f"{out_dir}/{name}s-{no}.parquet", compression="zstd")


class Article:
    """One article's versions, oldest first, turned into intervals."""

    def __init__(self, out, page_id, title, ts):
        self.out, self.page_id, self.title = out, page_id, title
        self.first = self.last = ts
        self.n = self.reverts = self.bytes = 0
        self.open = {}        # target -> [start_ms, start_rev, raw_adds, context]
        self.pending = {}     # target -> (start_ms, end_ms, start_rev, end_rev, raw_adds, context): just removed
        self.by_sha = {}      # sha1 -> link set, so a revert costs nothing
        self.links, self.sha = frozenset(), None
        self.lead_raw, self.lead = None, None       # lead: [text, start_ms, rev_id]

    def version(self, rev, ts, user, anonymous, comment, minor, sha, nbytes, text):
        out, text = self.out, text or ""
        self.n += 1
        self.last, self.bytes = ts, nbytes
        is_revert = sha is not None and sha in self.by_sha and sha != self.sha
        self.reverts += is_revert
        links = self.by_sha.get(sha) if sha is not None else None
        if links is None:
            links = w.link_set(text)
            if sha is not None:
                self.by_sha[sha] = links
        added = removed = ()
        if links is not self.links and links != self.links:
            added, removed = links - self.links, self.links - links
            for target in removed:
                start, start_rev, adds, ctx = self.open.pop(target)
                old = self.pending.get(target)
                if old is not None:                      # an older removal of the same link: now final
                    out.add(out.interval, self.page_id, target, *old)
                self.pending[target] = (start, ts, start_rev, rev, adds, ctx)
            need = set()
            for target in added:
                old = self.pending.pop(target, None)
                if old is not None and ts - old[1] < MERGE_MS:
                    self.open[target] = [old[0], old[2], old[4] + 1, old[5]]   # undone quickly: same interval
                else:
                    if old is not None:
                        out.add(out.interval, self.page_id, target, *old)
                    self.open[target] = [ts, rev, 1, None]
                    need.add(target)
            if need:                                     # the sentence around each newly added link
                for m in w.LINK.finditer(text):
                    target = w.normalize_title(m.group(1))
                    if target in need:
                        self.open[target][3] = w.context(text, m)
                        need.discard(target)
                        if not need:
                            break
        self.links, self.sha = links, sha
        out.add(out.revision, self.page_id, rev, ts, user, anonymous, minor, comment, nbytes,
                len(links), len(added), len(removed), is_revert)

        raw = w.lead_source(text)
        if raw != self.lead_raw:
            self.lead_raw = raw
            lead = w.lead(raw)
            if self.lead is None or lead != self.lead[0]:
                if self.lead is not None and ts > self.lead[1]:
                    out.add(out.lead, self.page_id, self.lead[2], self.lead[1], ts, self.lead[0])
                self.lead = [lead, ts, rev]

    def close(self):
        out = self.out
        for target, (start, start_rev, adds, ctx) in self.open.items():
            out.add(out.interval, self.page_id, target, start, None, start_rev, None, adds, ctx)
        for target, old in self.pending.items():
            out.add(out.interval, self.page_id, target, *old)
        if self.lead is not None:
            out.add(out.lead, self.page_id, self.lead[2], self.lead[1], None, self.lead[0])
        out.add(out.article, self.page_id, self.title, self.first, self.last, self.n, self.reverts, self.bytes)


def extract(job):
    path, out_dir = job
    no = re.search(r"versions-(\d+)\.parquet", path).group(1)
    t0, out, article = time.time(), Output(), None
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128, columns=COLUMNS):
        cols = [batch.column(i) for i in range(len(COLUMNS))]
        cols[3] = cols[3].cast(pa.int64())               # timestamp[ms] -> milliseconds since 1970
        for page_id, title, rev, ts, user, anonymous, comment, minor, sha, nbytes, text in zip(*(c.to_pylist() for c in cols)):
            if article is None or page_id != article.page_id:
                if article is not None:
                    article.close()
                article = Article(out, page_id, title, ts)
            article.title = title
            article.version(rev, ts, user, bool(anonymous), comment, bool(minor), sha, nbytes, text)
    if article is not None:
        article.close()
    out.write(out_dir, no)
    return (f"versions-{no}: {len(out.article['page_id'])} articles, {len(out.revision['rev_id'])} versions, "
            f"{len(out.interval['src'])} link intervals, {len(out.lead['page_id'])} lead versions, {time.time() - t0:.0f} s")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    m_dir, out_dir = sys.argv[1], sys.argv[2]
    paths = sorted(glob.glob(f"{m_dir}/versions-*.parquet"), key=os.path.getsize, reverse=True)
    if not paths:
        sys.exit(f"No versions-*.parquet in {m_dir}. Run pipeline/download.py first.")
    os.makedirs(out_dir, exist_ok=True)
    done = os.path.join(out_dir, ".done")          # written last: setup.sh skips this step only when it is there
    if os.path.exists(done):
        os.remove(done)
    t0 = time.time()
    # One process per core, six at most: a process needs up to 0.7 GB for the largest file.
    with multiprocessing.Pool(min(len(paths), os.cpu_count() or 2, 6)) as pool:
        for line in pool.imap_unordered(extract, [(p, out_dir) for p in paths]):
            print(line, flush=True)
    with open(done, "w") as f:
        f.write(f"{len(paths)} files\n")
    print(f"Done in {time.time() - t0:.0f} s -> {out_dir}")


if __name__ == "__main__":
    main()
