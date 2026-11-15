"""Replay: the API answered from a recorded snapshot, for a site that has no database behind it.

pipeline/snapshot.py records what MariaDB answered; this serves it back, SQL and timings included.
Nothing is recomputed here: a reply is either an answer that was recorded or a plain statement
that it is not in the snapshot. Two things are worked out on the spot, because they are not
questions to the database's history: the title box (a prefix match over the recorded titles) and
which recorded day stands in for a day that was not recorded (the last one kept before it; the
reply says so).

On when SNAPSHOT=1, or on Vercel when no DB_HOST is set: a host that runs the web app and nothing else.
"""
import bisect
import functools
import hashlib
import json
import os
import re
import unicodedata
import zipfile

from flask import Response, request

from . import config
from . import queries as Q

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot")
DEPTHS = ("full", "year", "today")
NO_MODEL = ("This is the snapshot: answers recorded from the database. Questions and explanations are written by a "
            "language model next to the live database, one request at a time, so they are not part of it.")
NO_ROUTE = "Routes between articles are worked out by the live database for each pair and each day. They are not in the snapshot."
NO_SEARCH = ("Questions are answered by the live app: the question becomes a vector, MariaDB finds the articles, "
             "a language model writes the answer. None of that can be recorded ahead of time, so it is not in the snapshot.")


def on():
    flag = config.env("SNAPSHOT").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    hosted = any(config.env(name) for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "AWS_LAMBDA_FUNCTION_NAME"))
    return hosted and not config.env("DB_HOST") and recorded()


def recorded():
    return os.path.exists(os.path.join(DIR, "index.json"))


@functools.lru_cache(maxsize=None)
def index():
    with open(os.path.join(DIR, "index.json"), encoding="utf-8") as f:
        return json.load(f)


def member(archive, name):
    """One recorded file as text, or None if there is no such file."""
    try:
        with zipfile.ZipFile(os.path.join(DIR, archive)) as z:
            return z.read(name).decode("utf-8")
    except (KeyError, FileNotFoundError):
        return None


@functools.lru_cache(maxsize=16)
def parsed(archive, name):
    """The same, parsed. Kept for the next request: a visitor dragging the handle asks for one file many times.
    What comes out of here is shared, so nobody changes it."""
    text = member(archive, name)
    return None if text is None else json.loads(text)


def fold(text):
    """As MariaDB's default collation compares titles: no case, no accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


@functools.lru_cache(maxsize=None)
def titles():
    """Every article of the data set, most linked-to first: (folded title, folded English title, id, title, English title, links in)."""
    return [(fold(title), fold(en or ""), pid, title, en, links_in) for pid, title, en, links_in in parsed("fixed.zip", "titles.json")]


@functools.lru_cache(maxsize=None)
def by_title():
    return {entry[0]: int(pid) for pid, entry in index()["pages"].items()}


def about():
    """What the page is told about the snapshot (and /api/status reports)."""
    idx = index()
    return {"made": idx["made"], "mariadb": idx["mariadb"], "last_day": idx["last_day"], "source": idx["source"], "start": idx["start"],
            "counts": idx["counts"], "full": idx["full"], "ids": [int(pid) for pid in idx["pages"]]}


# ---------------------------------------------------------------------------------- replies
def send(answer, status=200):
    body = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    reply = Response(body, status=status, mimetype="application/json")
    reply.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400" if status == 200 else "no-store"
    return reply


def miss(what, why="missing"):
    """Not in the snapshot. why: 'missing' (not recorded) or 'off' (a feature a replay cannot have)."""
    return send({"error": what, "snapshot": why}, 404)


def answer():
    """The reply to the /api request being handled."""
    if not recorded():
        return send({"error": "SNAPSHOT is on, but no snapshot has been recorded. Run: python pipeline/snapshot.py"}, 503)
    path, args = request.path, request.args
    if request.method == "POST":                                  # /api/answer, /api/explain
        return Response("⚠ " + NO_MODEL, mimetype="text/plain; charset=utf-8")
    m = re.fullmatch(r"/api/page/(\d+)(?:/(graph|asof|neighbours|leads))?", path)
    if m:
        return article(int(m.group(1)), m.group(2), args)
    if path == "/api/status":
        idx = index()
        return send({"mariadb": idx["mariadb"], "embed": False, "embed_model": config.EMBED_MODEL, "llm": None,
                     "last_day": idx["last_day"], "snapshot": about()})
    if path == "/api/overview":
        return send(member("fixed.zip", "overview.json"))
    if path == "/api/disputes":
        return send(member("fixed.zip", "disputes.json"))
    if path == "/api/hubs":
        text = member("fixed.zip", f"hubs/{args.get('year', '2026')}.json")
        return send(text) if text else miss("That year is not in the snapshot.")
    if path == "/api/gaps":
        text = member("fixed.zip", f"gaps/{args.get('kind', 'sv')}.json")
        if not text:
            raise ValueError("kind is one of sv, en, waited, wanted, orphans")
        return send(text)
    if path == "/api/wanted":
        found = parsed("fixed.zip", "wanted.json").get(args["target"])
        return send(found) if found else miss("What is said about this title is not in the snapshot.")
    if path == "/api/suggest":
        return send(suggest(args.get("q", "")))
    if path == "/api/edge":
        return edge(int(args["src"]), args["target"])
    if path == "/api/search":
        return miss(NO_SEARCH, "off")
    if path == "/api/path":
        return miss(NO_ROUTE, "off")
    return miss("No such question.")


def article(pid, what, args):
    entry = index()["pages"].get(str(pid))
    if not entry:
        known = next((t[3] for t in titles() if t[2] == pid), None)
        n = len(index()["pages"])
        return miss(f"“{known}” is one of the {index()['counts']['articles']:,} articles in the data, but not one of the {n:,} in this snapshot."
                    if known else "No such article in the graph.")
    archive, depth = f"p/{pid}.zip", entry[3]
    if what is None:
        page = json.loads(member(archive, "page.json"))
        page["snapshot"] = {"depth": depth, "days": entry[4], "stories": entry[5], "leads": bool(entry[6])}
        return send(page)
    if what == "graph":
        return send(member(archive, "graph.json"))
    if what == "leads":
        text = member(archive, "leads.json")
        return send(text) if text else miss("How this article opened over the years is not in the snapshot.")
    # the article on one day: that day if it was recorded, otherwise the last recorded day before it
    asked, days = Q.stamp(args.get("t"))[:10], parsed(archive, "days.json")
    at = max(bisect.bisect_right(days, asked) - 1, 0)
    kept = days[at]
    if depth == "today" and kept != asked:
        return send({"as_of": asked, "neighbours": [], "snapshot": {"asked": asked, "kept": None, "depth": depth}})
    found = parsed(archive, f"days/{at // index()['chunk']:02d}.json")[kept]["asof" if what == "asof" else "near"]
    return send(found if kept == asked else {**found, "snapshot": {"asked": asked, "kept": kept, "depth": depth}})


def edge(src, target):
    """One link's story: kept with the article it starts from, or with the one it points at."""
    pages, story = index()["pages"], None
    if str(src) in pages:
        story = (parsed(f"p/{src}.zip", "edges.json") or {"out": {}})["out"].get(target)
    if story is None and target in by_title():
        story = (parsed(f"p/{by_title()[target]}.zip", "edges.json") or {"in": {}})["in"].get(str(src))
    return send(story) if story else miss("The story of this link is not in the snapshot.")


def suggest(text, limit=8):
    """Titles as you type: the recorded articles first asked for, then how many more the live app would offer."""
    text = text.strip()
    if len(text) < 2:
        return []
    key, pages, found, more = fold(text.replace("%", "").replace("_", " ")), index()["pages"], [], 0
    for title, en, pid, shown, shown_en, links_in in titles():
        if title.startswith(key) or en.startswith(key):
            if str(pid) not in pages:
                more += 1
            elif len(found) < limit:
                found.append({"page_id": pid, "title": shown, "title_en": shown_en, "links_in": links_in})
    if more:
        found.append({"note": f"{more:,} more {'title matches' if more == 1 else 'titles match'} in the full data, not in this snapshot"})
    return found


# ---------------------------------------------------------------------------------- the page
def shell(static):
    """index.html, told that it is showing a snapshot. The scripts get a version in their address, so a
    browser that has seen an older deployment fetches the new ones."""
    with open(os.path.join(static, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for name in ("style.css", "app.js"):
        with open(os.path.join(static, name), "rb") as f:
            html = html.replace(f"/static/{name}", f"/static/{name}?v={hashlib.md5(f.read()).hexdigest()[:8]}")
    told = json.dumps(about(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("<script src=", f"<script>window.SNAPSHOT = {told};</script>\n<script src=", 1)
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})
