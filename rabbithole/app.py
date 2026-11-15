"""The web app: a small JSON API over MariaDB and one page that draws it.

    python -m rabbithole          # http://localhost:8000

Every answer carries the SQL that produced it ("sql": [...]); the page shows it in its SQL drawer.

Where there is no database (a static host), the same page replays answers recorded from one: replay.py.
"""
import datetime
import decimal
import os
import re
import time

from flask import Flask, Response, jsonify, request, send_from_directory
from flask.json.provider import DefaultJSONProvider

from . import config, db, llm, replay
from . import queries as Q
from .db import Session

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Json(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        if isinstance(o, (datetime.datetime, datetime.date)):
            return str(o)[:19]
        if isinstance(o, (bytes, bytearray)):
            return None
        return DefaultJSONProvider.default(o)


app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.json = Json(app)
app.json.ensure_ascii = False
_overview = {}


def reply(session, **data):
    data["sql"] = session.log
    return jsonify(data)


def missing(what="No such article in the graph"):
    return jsonify({"error": what}), 404


@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify({"error": f"Bad request: {e}"}), 400


@app.errorhandler(Exception)
def failed(e):
    if hasattr(e, "code") and isinstance(getattr(e, "code"), int) and e.code < 500:      # Flask's own 404s etc.
        return jsonify({"error": str(e)}), e.code
    app.logger.exception("request failed")
    return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.before_request
def from_snapshot():
    """Replaying a snapshot: every question is answered from what was recorded, and none reaches a database."""
    if request.path.startswith("/api/") and replay.on():
        return replay.answer()


@app.after_request
def keep(reply):
    if request.path.startswith("/static/") and replay.on():       # the snapshot's page asks for its files by version
        reply.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return reply


@app.get("/")
def index():
    return replay.shell(STATIC) if replay.on() else send_from_directory(STATIC, "index.html")


@app.get("/api/status")
def status():
    with Session() as s:
        version = s.one("SELECT VERSION() AS v", quiet=True)["v"]
    return jsonify({"mariadb": version, **llm.status(), "last_day": Q.LAST_DAY})


@app.get("/api/overview")
def overview():
    if not _overview:
        with Session() as s:
            _overview.update(Q.overview(s))
    return jsonify(_overview)


@app.get("/api/hubs")
def hubs():
    with Session() as s:
        return reply(s, **Q.hubs(s, request.args.get("year", "2026")))


@app.get("/api/suggest")
def suggest():
    text = request.args.get("q", "").strip()
    if len(text) < 2:
        return jsonify([])
    with Session() as s:
        return jsonify(Q.suggest(s, text))


@app.get("/api/search")
def search():
    """Find the articles for a question, and the text an answer would be written from."""
    text = request.args.get("q", "").strip()
    if not text:
        raise ValueError("q is empty")
    year = request.args.get("year") or None
    when = Q.stamp(year) if year else None
    t0 = time.perf_counter()
    vectors = llm.embed([text])
    embed_ms = round((time.perf_counter() - t0) * 1000)
    with Session() as s:
        topics = Q.search(s, text, vectors[0] if vectors else None)
        found = [t["page_id"] for t in topics if t["in_graph"]][:5]
        followed = Q.follow_links(s, found)
        src = Q.sources(s, found + [f["page_id"] for f in followed], when)
        for x in src:            # for the page: a short plain excerpt (the article text is Markdown)
            plain = " ".join(re.sub(r"(?m)^#+ .*$|\*\*|__", "", x.pop("text")).split())
            x["excerpt"], x["url"] = plain[:320], f"{config.WIKI}/?oldid={x['rev_id']}"
        return reply(s, q=text, topics=topics, followed=followed, sources=src, as_of=when[:10] if when else None,
                     mode="meaning + words" if vectors else "words", embed_ms=embed_ms if vectors else None)


ANSWER = """You answer questions about the history of Finland using only the Wikipedia excerpts you are given. \
The excerpts are mostly in Finnish. Answer in the language of the question, in two to five sentences. \
After each sentence, cite the excerpts it rests on as [1], [2]. If the excerpts do not contain the answer, say so plainly \
and say what they do cover. Use nothing from outside the excerpts."""

ANSWER_THEN = """ The excerpts are the opening paragraphs of each article exactly as they stood on {day}. \
Answer as Wikipedia would have answered on that day, and say that this is the Wikipedia of {day}."""

CHANGE = """You compare two versions of the opening of one Finnish Wikipedia article. In English, in three or four sentences, \
say what changed in how the article introduces its subject: facts added or dropped, shifts of emphasis or wording. \
Quote a short Finnish phrase with an English gloss when the wording matters. Then add one sentence about the links \
that were added and removed. Stay neutral: describe the change, do not judge it."""

DISPUTE = """Editors of Finnish Wikipedia disagreed over one link in one article. You are given every time the link was added \
and removed, the sentence around it, and the editors' own summaries (in Finnish). In English, in three to five sentences: \
what the sentence claimed, what reasons each side gave, and how it stands today. Translate short quotes. \
Do not take a side and do not say who is right. Do not name the editors."""


def stream(pieces):
    return Response(pieces, mimetype="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/answer")
def answer():
    body = request.get_json(force=True)
    text, ids = str(body.get("q", "")).strip(), [int(i) for i in body.get("page_ids", [])][:8]
    when = Q.stamp(body["year"]) if body.get("year") else None
    if not text or not ids:
        raise ValueError("q and page_ids are needed")
    with Session() as s:
        src = Q.sources(s, ids, when)
    excerpts = "\n\n".join(f"[{i}] {x['title']} (version of {x['version_of']})\n{x['text']}" for i, x in enumerate(src, 1))
    system = ANSWER + (ANSWER_THEN.format(day=when[:10]) if when else "")
    return stream(llm.chat(system, f"Question: {text}\n\nExcerpts:\n\n{excerpts}"))


@app.post("/api/explain")
def explain():
    body = request.get_json(force=True)
    with Session() as s:
        if body.get("kind") == "edge":
            e = Q.edge(s, int(body["src"]), str(body["target"]))
            if not e:
                return missing("No such link")
            lines = [f"Article: {e['src']['title']}. Link to: {e['target']}. Added {e['times_added']} times, removed {e['times_removed']} times. "
                     f"Today the link is {'present' if e['alive'] else 'absent'}."]
            for p in e["periods"]:
                lines.append(f"- Added {str(p['added'])[:10]}. Summary: {p['add_comment'] or '(none)'}. Sentence: {p['context'] or '(not recorded)'}")
                if p["removed"]:
                    lines.append(f"  Removed {str(p['removed'])[:10]}. Summary: {p['remove_comment'] or '(none)'}")
            return stream(llm.chat(DISPUTE, "\n".join(lines)))
        a = Q.asof(s, int(body["page_id"]), body.get("when"))
        title = s.one("SELECT title FROM page_stat WHERE page_id = %s", (int(body["page_id"]),), quiet=True)["title"]
    names = lambda rows: ", ".join(r["target"] for r in rows[:40]) or "(none)"      # noqa: E731
    prompt = (f"Article: {title}\n\nOpening on {a['as_of']}:\n{a['lead'] or '(the article did not exist yet)'}\n\n"
              f"Opening today:\n{a['lead_now']}\n\nLinks added since: {names(a['new'])}\nLinks removed since: {names(a['gone'])}")
    return stream(llm.chat(CHANGE, prompt))


@app.get("/api/page/<int:page_id>")
def page(page_id):
    with Session() as s:
        info = Q.page(s, page_id)
        return reply(s, **info) if info else missing()


@app.get("/api/page/<int:page_id>/graph")
def graph(page_id):
    with Session() as s:
        g = Q.graph(s, page_id)
        return reply(s, **g) if g else missing()


@app.get("/api/page/<int:page_id>/asof")
def asof(page_id):
    with Session() as s:
        return reply(s, **Q.asof(s, page_id, request.args.get("t")))


@app.get("/api/page/<int:page_id>/neighbours")
def neighbours(page_id):
    with Session() as s:
        return reply(s, **Q.neighbours(s, page_id, request.args.get("t")))


@app.get("/api/page/<int:page_id>/leads")
def leads(page_id):
    with Session() as s:
        return reply(s, **Q.lead_history(s, page_id))


@app.get("/api/edge")
def edge():
    with Session() as s:
        e = Q.edge(s, int(request.args["src"]), request.args["target"])
        return reply(s, **e) if e else missing("No such link")


@app.get("/api/disputes")
def disputes():
    with Session() as s:
        return reply(s, connections=Q.disputes(s), rewritten=Q.rewritten(s))


@app.get("/api/gaps")
def gaps():
    kind = request.args.get("kind", "sv")
    with Session() as s:
        rows = Q.gaps(s, kind)
        if rows is None:
            raise ValueError("kind is one of sv, en, waited, wanted, orphans")
        return reply(s, kind=kind, rows=rows, counts=Q.gap_counts(s))


@app.get("/api/wanted")
def wanted():
    with Session() as s:
        return reply(s, target=request.args["target"], mentions=Q.wanted_context(s, request.args["target"]))


@app.get("/api/path")
def path():
    with Session() as s:
        p = Q.path(s, int(request.args["from"]), int(request.args["to"]), request.args.get("t"))
        return reply(s, **p) if p else missing()


def main():
    if replay.on():
        note = None if replay.recorded() else "SNAPSHOT is on, but nothing has been recorded: run python pipeline/snapshot.py"
        print(f"Rabbit Hole: http://localhost:{config.PORT}   (replaying a snapshot: no database is used)")
    else:
        print(f"Rabbit Hole: http://localhost:{config.PORT}   (MariaDB {config.DB['host']}:{config.DB['port']})")
        try:
            note = db.small_memory()
        except Exception as e:                   # MariaDB is not up: the page will say so too
            note = f"MariaDB cannot be reached yet: {e.args[-1] if e.args else e}"
    if note:
        print("\n" + note + "\n")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=config.PORT, threaded=True, debug=False, load_dotenv=False)
