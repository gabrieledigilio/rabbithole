"""Create the tables and load everything into MariaDB. Safe to run again: a table that already has
rows is skipped.

    python pipeline/load.py                  # everything
    python pipeline/load.py graph leads      # only these steps
    python pipeline/load.py --reset          # drop all tables first

Steps: articles, vectors (dataset S) · graph, revisions, leads (from pipeline/extract.py) · titles.
The graph and the leads go into system-versioned tables with Wikipedia's real times:
system_versioning_insert_history lets the loader write row_start and row_end itself.
"""
import glob
import gzip
import os
import struct
import sys
import time
from datetime import datetime, timezone

import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import config, db  # noqa: E402

STEPS = ["articles", "vectors", "graph", "revisions", "leads", "titles"]
TABLES = ["page", "link", "lead", "link_context", "revision", "article", "article_vector", "fiwiki_title"]
GRAPH = os.path.join(config.DATA, "graph")
con = db.connect()
con.autocommit(False)
cur = con.cursor()


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def insert(sql, rows, batch=4000):
    for i in range(0, len(rows), batch):
        cur.executemany(sql, rows[i:i + batch])
    con.commit()


def filled(table):
    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
    return cur.fetchone() is not None


def files(pattern, hint):
    found = sorted(glob.glob(pattern))
    if not found:
        sys.exit(f"Nothing matches {pattern}. {hint}")
    return found


def schema(reset):
    if reset:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("SHOW TABLES")
        for (table,) in cur.fetchall():
            cur.execute(f"DROP TABLE `{table}`")
    for statement in open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8").read().split(";\n"):
        if any(line.strip() and not line.strip().startswith("--") for line in statement.splitlines()):
            cur.execute(statement)
    con.commit()


def articles():
    for path in files(f"{config.DATA}/s/articles-*.parquet", "Run pipeline/download.py"):
        rows = [(r["lang"], r["page_id"], r["rev_id"], r["wikidata_id"], r["title"], r["url"],
                 (r["date_modified"] or "").replace("T", " ").rstrip("Z") or None, r["text"])
                for r in pq.read_table(path).to_pylist()]
        insert("INSERT INTO article (lang, page_id, rev_id, wikidata_id, title, url, date_modified, text) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows, batch=500)
        print(f"  {os.path.basename(path)}: {len(rows):,} articles", flush=True)


def vectors():
    for path in files(f"{config.DATA}/s/vectors-*.parquet", "Run pipeline/download.py"):
        t0 = time.time()
        # MariaDB takes a VECTOR as raw bytes: 1024 float32 numbers, little-endian.
        rows = [(r["lang"], r["page_id"], r["model"], r["truncated"], struct.pack(f"<{len(r['embedding'])}f", *r["embedding"]))
                for r in pq.read_table(path).to_pylist()]
        insert("INSERT INTO article_vector (lang, page_id, model, truncated, embedding) VALUES (%s, %s, %s, %s, %s)", rows, batch=200)
        print(f"  {os.path.basename(path)}: {len(rows):,} vectors, {time.time() - t0:.0f} s (the vector index is built as they arrive)", flush=True)


def graph():
    cur.execute("SET system_versioning_insert_history = 1")
    title_to_id, rows = {}, []
    for path in files(f"{GRAPH}/articles-*.parquet", "Run pipeline/extract.py"):
        for r in pq.read_table(path).to_pylist():
            title_to_id.setdefault(r["title"], r["page_id"])
            rows.append((r["page_id"], r["title"], ts(r["first_ms"])))
    insert("INSERT INTO page (page_id, title, row_start) VALUES (%s, %s, %s)", rows)
    print(f"  page: {len(rows):,} articles, each from the moment it was created", flush=True)

    n_hist = n_now = n_ctx = 0
    for path in files(f"{GRAPH}/intervals-*.parquet", "Run pipeline/extract.py"):
        t = pq.read_table(path)
        hist, now, ctx = [], [], []
        for src, target, a, b, srev, erev, context in zip(*(t.column(c).to_pylist() for c in
                                                             ("src", "target", "start_ms", "end_ms", "start_rev", "end_rev", "context"))):
            dst = title_to_id.get(target)
            if b is None:            # still linked today: only a start; MariaDB gives it the "still current" end
                now.append((src, target, dst, srev, ts(a)))
            elif b > a:              # a link that was removed: both ends
                hist.append((src, target, dst, srev, erev, ts(a), ts(b)))
            else:
                continue
            if context:
                ctx.append((src, target, srev, context[:700]))
        insert("INSERT INTO link (src, target, dst, start_rev, end_rev, row_start, row_end) VALUES (%s, %s, %s, %s, %s, %s, %s)", hist)
        insert("INSERT INTO link (src, target, dst, start_rev, row_start) VALUES (%s, %s, %s, %s, %s)", now)
        insert("INSERT IGNORE INTO link_context (src, target, start_rev, context) VALUES (%s, %s, %s, %s)", ctx)
        n_hist, n_now, n_ctx = n_hist + len(hist), n_now + len(now), n_ctx + len(ctx)
    cur.execute("SET system_versioning_insert_history = 0")
    print(f"  link: {n_now:,} links of today + {n_hist:,} history rows; {n_ctx:,} contexts", flush=True)


def revisions():
    n = 0
    for path in files(f"{GRAPH}/revisions-*.parquet", "Run pipeline/extract.py"):
        rows = [(r["rev_id"], r["page_id"], ts(r["ts_ms"]), (r["user"] or "")[:255] or None, r["anonymous"], r["minor"],
                 (r["comment"] or "")[:600] or None, r["bytes"], min(r["n_links"], 65535), min(r["n_added"], 65535),
                 min(r["n_removed"], 65535), r["is_revert"]) for r in pq.read_table(path).to_pylist()]
        insert("INSERT INTO revision (rev_id, page_id, ts, user, anonymous, minor, comment, bytes, n_links, n_added, n_removed, is_revert) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
        n += len(rows)
    print(f"  revision: {n:,} saved versions", flush=True)


def leads():
    cur.execute("SET system_versioning_insert_history = 1")
    n_hist = n_now = 0
    for path in files(f"{GRAPH}/leads-*.parquet", "Run pipeline/extract.py"):
        hist, now = [], []
        for r in pq.read_table(path).to_pylist():
            if r["end_ms"] is None:
                now.append((r["page_id"], r["rev_id"], r["text"], ts(r["start_ms"])))
            elif r["end_ms"] > r["start_ms"]:
                hist.append((r["page_id"], r["rev_id"], r["text"], ts(r["start_ms"]), ts(r["end_ms"])))
        insert("INSERT INTO lead (page_id, rev_id, text, row_start, row_end) VALUES (%s, %s, %s, %s, %s)", hist, batch=1000)
        insert("INSERT INTO lead (page_id, rev_id, text, row_start) VALUES (%s, %s, %s, %s)", now, batch=1000)
        n_hist, n_now = n_hist + len(hist), n_now + len(now)
    cur.execute("SET system_versioning_insert_history = 0")
    print(f"  lead: {n_now:,} leads of today + {n_hist:,} earlier versions", flush=True)


def titles():
    """Optional. Every title of Finnish Wikipedia, to tell a red link from a link out of our topic."""
    all_titles = os.path.join(config.DATA, "titles", "all-titles-in-ns0.gz")
    l_articles = os.path.join(config.DATA, "l", "articles-fi.parquet")
    if os.path.exists(all_titles):
        with gzip.open(all_titles, "rt", encoding="utf-8") as f:
            rows = [(line.rstrip("\n").replace("_", " ")[:255], 0) for line in f if line.strip() and not line.startswith("page_title")]
        insert("INSERT IGNORE INTO fiwiki_title (title, is_article) VALUES (%s, %s)", rows, batch=10000)
        print(f"  fiwiki_title: {len(rows):,} titles from Wikimedia's all-titles file (articles and redirects)", flush=True)
    if os.path.exists(l_articles):
        rows = [(t[:255], 1) for t in pq.read_table(l_articles, columns=["title"]).column("title").to_pylist()]
        insert("INSERT INTO fiwiki_title (title, is_article) VALUES (%s, %s) ON DUPLICATE KEY UPDATE is_article = 1", rows, batch=10000)
        print(f"  fiwiki_title: {len(rows):,} article titles from dataset L", flush=True)
    if not os.path.exists(all_titles) and not os.path.exists(l_articles):
        print("  fiwiki_title: skipped (python pipeline/download.py --titles gets the list; without it the "
              "'wanted articles' list is left empty)", flush=True)


def main():
    args = sys.argv[1:]
    schema(reset="--reset" in args)
    steps = [a for a in args if a in STEPS] or STEPS
    first_table = {"articles": "article", "vectors": "article_vector", "graph": "page", "revisions": "revision",
                   "leads": "lead", "titles": "fiwiki_title"}
    t_all = time.time()
    for step in steps:
        if filled(first_table[step]):
            print(f"{step}: already loaded")
            continue
        t0 = time.time()
        print(f"{step} ...", flush=True)
        globals()[step]()
        print(f"{step}: {time.time() - t0:.0f} s", flush=True)
    for table in TABLES:
        cur.execute(f"ANALYZE TABLE {table}")
        cur.fetchall()
    cur.execute("SELECT VERSION()")
    print(f"Loaded into MariaDB {cur.fetchone()[0]} in {time.time() - t_all:.0f} s. Next: python pipeline/analyze.py")


if __name__ == "__main__":
    main()
