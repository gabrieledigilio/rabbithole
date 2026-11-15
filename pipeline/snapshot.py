#!/usr/bin/env python3
"""Record a snapshot of the app's answers, so the site can be shown where there is no database.

    python pipeline/snapshot.py                    # needs MariaDB loaded and analysed, like the app
    SNAPSHOT=1 python -m rabbithole                # then: the app, replaying what was recorded

Nothing here is made up or recomputed. The recorder asks the app's own API (through Flask's test
client) and keeps what MariaDB answered, the SQL and its timings included. rabbithole/replay.py
serves those answers back. What is kept, in rabbithole/snapshot/:

  index.json      when it was made, and which articles are in it
  fixed.zip       the answers that do not depend on an article: big picture, disputes, gaps, titles
  p/<id>.zip      one article: page.json, graph.json, leads.json, days.json (which days are kept),
                  days/<nn>.json (the article on those days, twelve to a file, each with its
                  neighbours in meaning), edges.json (the life of its links)

Three depths, because every day of every article would be the database itself:

  full    the first of every month of the article's life and every year's end; the story of every
          link in its picture and in its "since that day" lists
  year    the last day of every year; the story of every link in its picture
  today   the article as the data ends; the stories of the links people fought over

Editors are not named in a snapshot: it is a public copy, and the answers of /api/edge carry user
names and the IP addresses of unregistered editors. Names are dropped from the answers and replaced
by [editor] inside edit summaries; each edit still links to Wikipedia, where its author is credited.
The language-model features (questions, explanations) and routes are not recorded: they are worked
out per request, and a replay cannot do that.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["SNAPSHOT"] = "0"                       # ask the database, never an older snapshot

from rabbithole import db                          # noqa: E402
from rabbithole import queries as Q                # noqa: E402
from rabbithole.app import app                     # noqa: E402

START = 1256                                       # Talvisota: where a first visit begins
ISOVIHA = 11803
ALSO = [6021]                                      # and Mannerheim, whom a visitor is likely to look up
SOURCE = "https://github.com/gabrieledigilio/rabbithole"
CHIPS = 60                                         # the page shows this many links of a "since that day" list
CHUNK = 12                                         # days to a file: neighbours in time share most of their text
_local = threading.local()
asked = [0]


def ask(path, **query):
    """One answer of the app, as the page would get it. None for a 404."""
    if not hasattr(_local, "client"):
        _local.client = app.test_client()
    r = _local.client.get(path, query_string=query)
    asked[0] += 1
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"{path} {query}: {r.status_code} {r.get_data(as_text=True)[:300]}")
    return r.get_json()


def pack(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def day_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()


def span(graph):
    """First and last day of the article's timeline, as the page works them out."""
    firsts = [p[0] for nd in graph["nodes"] for p in (nd.get("out") or []) + (nd.get("in") or [])]
    return day_of(min([graph["center"]["created"]] + firsts)), day_of(graph["end"])


def grid(first, last, depth):
    days = {last}
    if depth != "today":
        days |= {first, min(last, first + datetime.timedelta(days=1))}
        for year in range(first.year, last.year + 1):
            days.add(datetime.date(year, 12, 31))
            if depth == "full":
                days |= {datetime.date(year, month, 1) for month in range(1, 13)}
    return sorted(d for d in days if first <= d <= last)


# ---------------------------------------------------------------------------------- editors' names
USERLINK = re.compile(r"\[\[[^\]|]*?(?:Special|Toiminnot)\s*:\s*(?:Contributions|Contribs|Muokkaukset)\s*/[^\]]*\]\]"
                      r"|\[\[\s*:?(?:\w+:)*?\s*(?:User|Käyttäjä|User[ _]talk|Keskustelu[ _]käyttäjästä)\s*:[^\]]*\]\]", re.I)
ADDRESS = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"                            # an unregistered editor is known by an IP address
                     r"|(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f])|(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){2,6}:[0-9A-Fa-f:]*")
NAME = r"((?:(?!käyttäjä).){1,60}?)"                # a user name: short, and not running on into the next phrase
PHRASES = [re.compile(p, re.I) for p in (            # the stock phrases of undo, rollback and review on Finnish Wikipedia
    rf"(käyttäjän ){NAME}( muokkaukset kumottiin)", rf"(käyttäjän ){NAME}( tekemään versioon)",
    rf"(viimeisimpään ){NAME}(:n tekemään versioon)", rf"(viimeiseen ){NAME}(:n tekemään muutokseen)",
    rf"(\(tehnyt ){NAME}(\))", rf"(jonka (?:on )?(?:tehnyt|teki) ){NAME}( \(|[.,;:]|$)",
    rf"(käyttäjältä ){NAME}(\)|[:;,]|$)", r"((?:Käyttäjä|User):)([^\s/|\]]+)()",
    rf"(edits by ){NAME}( \(|\s+to last)", rf"((?:version|revision) by ){NAME}([.,;]|$)", r"(\bby )(\S+)( using\b|$)",
    r"(eston\s?kiertoa? \()([^)]{1,60})(\))")]
# and where a phrase ran on too long to be sure where the name ends: the word after it
AFTER = re.compile(r"((?:käyttäj(?:ä|än|älle|ältä)|jonka (?:on )?(?:tehnyt|teki)|\(tehnyt) )(?!\[editor\])([^\s,;:()]+)", re.I)
KNOWN = {"WPK"}                                      # a nickname no rule covers; found by reading what the rules left
TOKEN = re.compile(r"[\w+~.\-]+")
WHO = "[editor]"


def scrub(text, names, editors=(), plain=()):
    """An edit summary with the editors it names replaced by [editor].

    names: the editors of this link's own story. editors: everyone registered who edited the article;
    one of them is replaced where the word is not also an ordinary word (lower case, or part of a title)."""
    if not text:
        return text
    out = USERLINK.sub(WHO, text)
    for name in sorted(set(names) | KNOWN, key=len, reverse=True):
        if len(name) >= 2:
            out = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", WHO, out)
    out = ADDRESS.sub(WHO, out)
    for phrase in PHRASES:
        out = phrase.sub(lambda m: m.group(1) + WHO + m.group(3), out)

    def known(m):
        for word in (m.group(0), m.group(0).rstrip(".-")):
            if len(word) >= 3 and word in editors and not word.islower() and word not in plain and word != "editor":
                return WHO + m.group(0)[len(word):]
        return m.group(0)
    if editors:                                              # not inside [[a link]] or a /* section name */: those are about the subject
        parts = re.split(r"(\[\[[^\]]*\]\]|/\*.*?\*/)", out)
        out = "".join(part if i % 2 else TOKEN.sub(known, part) for i, part in enumerate(parts))
        for name in editors:
            if " " in name and name in out:
                out = out.replace(name, WHO)
    out = AFTER.sub(lambda m: m.group(1) + WHO, out)
    return re.sub(r"\[editor\](?:\s*\(\s*(?:\[editor\]|keskustelu|talk)\s*\))+", WHO, out)


def unnamed(edge, editors=(), plain=()):
    names = {p[k] for p in edge["periods"] for k in ("added_by", "removed_by") if p[k]}
    names |= {n.split("~")[0] for n in names}                # 'Kotov~fiwiki' is written 'Kotov' in old summaries
    for p in edge["periods"]:
        for field in ("add_comment", "remove_comment"):
            p[field] = scrub(p[field], names, editors, plain)
        p["added_by"] = p["removed_by"] = None
    edge["editors_named"] = False
    return edge


# ---------------------------------------------------------------------------------- one article
class Recorder:
    def __init__(self, out, workers):
        self.out, self.pool = out, ThreadPoolExecutor(workers)
        self.edges, self.lock = {}, threading.Lock()         # (src, target) -> the story, packed
        self.pages = {}                                      # id -> [title, title_en, links_in, depth, days, link stories, leads?]
        self.leads = set()                                   # articles kept as they stand today whose openings are wanted too
        self.plain, self.who = set(), {}                     # words of titles; article -> its registered editors
        os.makedirs(os.path.join(out, "p"), exist_ok=True)

    def edge(self, src, target):
        key = (int(src), target)
        with self.lock:
            if key in self.edges:
                return
            self.edges[key] = None
        e = ask("/api/edge", src=key[0], target=target)
        if e and key[0] not in self.who:
            with db.Session() as s:
                rows = s.q("SELECT DISTINCT user FROM revision WHERE page_id = %s AND anonymous = 0 AND user IS NOT NULL", (key[0],), quiet=True)
            self.who[key[0]] = {r["user"] for r in rows}
        with self.lock:
            if e:
                self.edges[key] = (pack(unnamed(e, self.who[key[0]], self.plain)), (e.get("dst") or {}).get("page_id"))
            else:
                del self.edges[key]

    def one_day(self, pid, day):
        d = str(day)
        return d, ask(f"/api/page/{pid}/asof", t=d), ask(f"/api/page/{pid}/neighbours", t=d)

    def article(self, pid, depth, threaded=False, picture=False):
        """Record one article. Returns the ids of articles it leads to that should be in the snapshot too:
        the other end of its contested links, its neighbours in meaning (full), its picture (if asked)."""
        page, graph = ask(f"/api/page/{pid}"), ask(f"/api/page/{pid}/graph")
        if not page or not graph:
            return set()
        run = self.pool.map if threaded else map             # the pool is not re-entered from its own workers
        first, last = span(graph)
        days = list(run(lambda d: self.one_day(pid, d), grid(first, last, depth)))
        seen = {c["src"] for c in page["contested"]}
        wanted = {(c["src"], c["target"]) for c in page["contested"]}        # the link stories to keep
        if depth != "today":
            for nd in graph["nodes"]:
                if nd.get("out"):
                    wanted.add((pid, nd["title"]))
                if nd.get("in") and isinstance(nd["id"], int):
                    wanted.add((nd["id"], page["title"]))
        if depth == "full":
            for _, a, _ in days:
                wanted |= {(pid, r["target"]) for r in a["new"][:CHIPS] + a["gone"][:CHIPS] if not r["dst"]}
            seen |= {r["page_id"] for r in days[-1][2]["neighbours"]}
        if picture:
            seen |= {nd["id"] for nd in graph["nodes"] if isinstance(nd["id"], int)}
        list(run(lambda k: self.edge(*k), sorted(wanted)))
        members = {"page.json": pack(page), "graph.json": pack(graph), "days.json": pack([d for d, _, _ in days])}
        for i in range(0, len(days), CHUNK):
            members[f"days/{i // CHUNK:02d}.json"] = pack({d: {"asof": a, "near": near} for d, a, near in days[i:i + CHUNK]})
        leads = ask(f"/api/page/{pid}/leads") if depth != "today" or pid in self.leads else None
        if leads:
            members["leads.json"] = pack(leads)
        write_zip(os.path.join(self.out, "p", f"{pid}.zip"), members)
        with self.lock:
            self.pages[pid] = [page["title"], page["title_en"], page["links_in"], depth, len(days), 0, int(bool(leads))]
        return seen - {pid}

    def finish(self):
        """Put every link story into the file of the article it starts from (or, failing that, ends at)."""
        per = {}
        for (src, target), (story, dst) in self.edges.items():
            if src in self.pages:
                per.setdefault(src, {"out": {}, "in": {}})["out"][target] = story
            elif dst in self.pages:
                per.setdefault(dst, {"out": {}, "in": {}})["in"][str(src)] = story
        for pid, both in per.items():
            text = "{" + ",".join(f'{json.dumps(side)}:{{' + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{v}" for k, v in sorted(both[side].items())) + "}"
                                  for side in ("out", "in")) + "}"
            write_zip(os.path.join(self.out, "p", f"{pid}.zip"), {"edges.json": text}, mode="a")
            self.pages[pid][5] = len(both["out"]) + len(both["in"])


def write_zip(path, members, mode="w"):
    with zipfile.ZipFile(path, mode, zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))     # the same bytes for the same answers
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, members[name], compresslevel=9)


# ---------------------------------------------------------------------------------- everything
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "rabbithole", "snapshot"))
    ap.add_argument("--full", type=int, nargs="*", help="articles recorded in full (default: the start page, the front-page stories, "
                                                        "today's ten most linked-to articles and the ones in ALSO)")
    ap.add_argument("--top", type=int, default=400, help="also keep the N most linked-to articles as they stand today")
    ap.add_argument("--deep", type=int, default=60, help="of those, keep the first N year by year")
    ap.add_argument("--limit", type=int, default=0, help="for a quick trial: at most N articles per depth")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    began = time.time()
    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    rec = Recorder(args.out, args.workers)

    # 1. the answers that do not depend on an article
    overview = ask("/api/overview")
    fixed = {"overview.json": pack(overview)}
    for y in overview["years"]:
        fixed[f"hubs/{y['yr']}.json"] = pack(ask("/api/hubs", year=y["yr"]))
    disputes = ask("/api/disputes")
    fixed["disputes.json"] = pack(disputes)
    gaps = {kind: ask("/api/gaps", kind=kind) for kind in ("sv", "en", "waited", "wanted", "orphans")}
    for kind, answer in gaps.items():
        fixed[f"gaps/{kind}.json"] = pack(answer)
    fixed["wanted.json"] = pack(dict(rec.pool.map(lambda r: (r["target"], ask("/api/wanted", target=r["target"])), gaps["wanted"]["rows"])))
    with db.Session() as s:
        titles = s.q("SELECT page_id, title, title_en, links_in FROM page_stat ORDER BY links_in DESC, page_id", quiet=True)
    fixed["titles.json"] = pack([[t["page_id"], t["title"], t["title_en"], t["links_in"]] for t in titles])
    rec.plain = {word for t in titles for word in re.findall(r"[\w-]+", t["title"])}
    rec.leads = {r["page_id"] for r in disputes["rewritten"]}
    write_zip(os.path.join(args.out, "fixed.zip"), fixed)
    print(f"fixed answers: {len(fixed)} ({asked[0]} asked), {time.time() - began:.0f} s", flush=True)

    # 2. which articles, how deep
    last_year = overview["years"][-1]["yr"]
    stories = [START, ISOVIHA] + [c["src"] for c in overview["stories"]]
    full = args.full or stories + [h["page_id"] for h in overview["hubs"] if h["yr"] == last_year] + ALSO
    full = list(dict.fromkeys(full))
    year = [h["page_id"] for h in overview["hubs"]] + [c["src"] for c in disputes["connections"]] + [r["page_id"] for r in disputes["rewritten"]]
    year += [t["page_id"] for t in titles[:args.deep]]
    year = [p for p in dict.fromkeys(year) if p not in full]
    today = [r["page_id"] for kind in ("sv", "en", "waited", "orphans") for r in gaps[kind]["rows"]]
    today += [c["dst"] for c in disputes["connections"] if c["dst"]] + [t["page_id"] for t in titles[:args.top]]
    if args.limit:
        full, year, today = full[:args.limit], year[:args.limit], today[:args.limit]

    # 3. record: the full ones one at a time (their days and links go through the pool), the rest side by side
    linked = set()
    for pid in full:
        t = time.time()
        linked |= rec.article(pid, "full", threaded=True, picture=pid in stories[:2])
        print(f"full  {pid:>8} {rec.pages.get(pid, ['?'])[0]}: {time.time() - t:.0f} s", flush=True)
    for found in rec.pool.map(lambda p: rec.article(p, "year"), year):
        linked |= found
    print(f"year  {len(year)} articles, {time.time() - began:.0f} s", flush=True)
    today = [p for p in dict.fromkeys(today + sorted(linked)) if p not in rec.pages]
    if args.limit:
        today = today[:args.limit]
    list(rec.pool.map(lambda p: rec.article(p, "today"), today))
    print(f"today {len(today)} articles, {time.time() - began:.0f} s", flush=True)
    list(rec.pool.map(lambda c: rec.edge(c["src"], c["target"]), disputes["connections"] + overview["stories"]))
    rec.finish()

    count = lambda depth: sum(1 for p in rec.pages.values() if p[3] == depth)      # noqa: E731
    index = {
        "made": str(datetime.date.today()), "mariadb": overview["stat"]["mariadb"], "last_day": Q.LAST_DAY, "source": SOURCE, "start": START,
        "chunk": CHUNK,
        "counts": {"full": count("full"), "year": count("year"), "today": count("today"), "articles": len(titles),
                   "days": sum(p[4] for p in rec.pages.values()), "stories": sum(p[5] for p in rec.pages.values()), "answers": asked[0]},
        "full": [{"page_id": p, "title": rec.pages[p][0], "title_en": rec.pages[p][1]} for p in full if p in rec.pages],
        "pages": {str(p): v for p, v in sorted(rec.pages.items())},
    }
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        f.write(pack(index))
    size = sum(os.path.getsize(os.path.join(d, n)) for d, _, names in os.walk(args.out) for n in names)
    print(f"snapshot: {index['counts']}, {size / 1e6:.1f} MB in {args.out}, {time.time() - began:.0f} s")


if __name__ == "__main__":
    main()
