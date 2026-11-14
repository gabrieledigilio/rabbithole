"""Every question the pages ask of MariaDB.

Each function takes a db.Session, which logs the SQL it runs; the page shows that log in its SQL
drawer. Three kinds of time travel appear here:

  SET system_versioning_asof = '2012-06-01'      the whole session sees that day (joins, CTEs, views)
  link FOR SYSTEM_TIME AS OF TIMESTAMP '...'     one table as of that day, next to today's tables
  link FOR SYSTEM_TIME ALL                       every period a row ever had: the life of a link
"""
import difflib
import json
import math
import re
from datetime import datetime, timezone

from . import config

LAST_DAY = "2026-10-01"                 # the day of the dump dataset M was cut from
DATEISH = re.compile(r"^(\d{1,2}\. \w+kuuta|\d{1,4}|\d{1,4}-luku|\w+kuu)$")       # '30. marraskuuta', '1939', '1930-luku'
YEAR_MS = 365.25 * 86400 * 1000


def ms(moment):
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000) if moment else None


def stamp(when):
    """'2012', '2012-06', '2012-06-01' or a full timestamp -> 'YYYY-MM-DD HH:MM:SS' (UTC), not after the data ends."""
    when = str(when or LAST_DAY).strip().replace("T", " ")
    if re.fullmatch(r"\d{4}", when):
        when += "-12-31 23:59:59"                         # a year means its last moment, as in year_stat
    elif re.fullmatch(r"\d{4}-\d{2}", when):
        when += "-01"
    if len(when) == 10:
        when += " 00:00:00"
    when = when[:19]
    datetime.strptime(when, "%Y-%m-%d %H:%M:%S")          # raises ValueError on nonsense
    return min(when, LAST_DAY + " 00:00:00")


def marks(n):
    return ", ".join(["%s"] * n)


# ---------------------------------------------------------------------------------------------
# Home: the whole graph, year by year
# ---------------------------------------------------------------------------------------------

def overview(s):
    stat = {r["name"]: json.loads(r["value"]) for r in s.q("SELECT name, value FROM stat", quiet=True)}
    years = s.q("SELECT yr, pages, links, links_inside, edits, editors, reverts FROM year_stat ORDER BY yr", quiet=True)
    hubs = s.q("""SELECT h.yr, h.place, h.page_id, h.title, ps.title_en, h.links_in
                  FROM hub_year h LEFT JOIN page_stat ps USING (page_id) WHERE h.place <= 10 ORDER BY h.yr, h.place""", quiet=True)
    return {"stat": stat, "years": years, "hubs": hubs, "stories": disputes(s, limit=6, quiet=True)}


HUBS_SQL = """
SELECT p.page_id, p.title, COUNT(*) AS links_in
FROM link l JOIN page p ON p.page_id = l.dst
WHERE l.dst <> l.src
GROUP BY p.page_id ORDER BY links_in DESC, p.page_id LIMIT 10"""


def hubs(s, year):
    """The most linked-to articles at the end of a year, asked live: one setting, then plain SQL."""
    when = stamp(year)
    s.asof(when)
    size = s.one("SELECT (SELECT COUNT(*) FROM page) AS pages, COUNT(*) AS links FROM link",
                 note="the whole graph as of that day")
    top = s.q(HUBS_SQL, note="versioned link joined to versioned page: no time clause in the statement")
    s.asof(None)
    return {"as_of": when[:10], "pages": size["pages"], "links": size["links"], "hubs": top}


# ---------------------------------------------------------------------------------------------
# Finding an article: titles, keywords, meaning
# ---------------------------------------------------------------------------------------------

def suggest(s, text, limit=8):
    like = text.strip().replace("%", "").replace("_", " ") + "%"
    return s.q("""SELECT page_id, title, title_en, links_in FROM page_stat
                  WHERE title LIKE %s OR title_en LIKE %s ORDER BY links_in DESC LIMIT %s""", (like, like, limit), quiet=True)


# Vector search and full-text search in one statement, merged by Reciprocal Rank Fusion: an article's
# score is 1/(60 + its place) in each list, added up. The vector part stands alone in its own CTE,
# ORDER BY distance LIMIT n on article_vector only: the shape in which MariaDB uses the vector index.
HYBRID_SQL = """
WITH meaning AS (
  SELECT lang, page_id, VEC_DISTANCE_COSINE(embedding, %s) AS distance
  FROM article_vector ORDER BY distance LIMIT 40),
words AS (
  SELECT lang, page_id, MATCH(title, text) AGAINST (%s) AS relevance
  FROM article WHERE MATCH(title, text) AGAINST (%s) ORDER BY relevance DESC LIMIT 40),
ranked AS (
  SELECT lang, page_id, 1 / (60 + ROW_NUMBER() OVER (ORDER BY distance)) AS rrf, 1 AS by_meaning, 0 AS by_words FROM meaning
  UNION ALL
  SELECT lang, page_id, 1 / (60 + ROW_NUMBER() OVER (ORDER BY relevance DESC)), 0, 1 FROM words)
SELECT a.lang, a.page_id, a.wikidata_id, a.title, SUM(r.rrf) AS score,
       MAX(r.by_meaning) AS by_meaning, MAX(r.by_words) AS by_words
FROM ranked r JOIN article a USING (lang, page_id)
GROUP BY a.lang, a.page_id ORDER BY score DESC LIMIT 30"""

WORDS_SQL = """
SELECT a.lang, a.page_id, a.wikidata_id, a.title, MATCH(a.title, a.text) AGAINST (%s) AS score, 0 AS by_meaning, 1 AS by_words
FROM article a WHERE MATCH(a.title, a.text) AGAINST (%s) ORDER BY score DESC LIMIT 30"""

# The vector half alone. The app does not use it; pipeline/evaluate.py does, to show what each half is worth.
MEANING_SQL = """
SELECT a.lang, a.page_id, a.wikidata_id, a.title, 1 / (60 + ROW_NUMBER() OVER (ORDER BY v.distance)) AS score, 1 AS by_meaning, 0 AS by_words
FROM (SELECT lang, page_id, VEC_DISTANCE_COSINE(embedding, %s) AS distance FROM article_vector ORDER BY distance LIMIT 40) v
JOIN article a USING (lang, page_id) ORDER BY v.distance LIMIT 30"""


def search(s, text, vector=None, limit=8, only=None):
    """Topics that answer the text. An article in three languages is one topic (same wikidata_id);
    it is reported under its Finnish page, the one the graph is about.
    only: 'words' or 'meaning' to use one half of the search (for measuring)."""
    if only == "meaning":
        rows = s.q(MEANING_SQL, (vector,), note="vector search only")
    elif vector is not None and only != "words":
        rows = s.q(HYBRID_SQL, (vector, text, text), note="vector search + full-text search, fused by rank (RRF), one statement")
    else:
        rows = s.q(WORDS_SQL, (text, text), note="keyword search only: no embedding model is running")
    topics, order = {}, []
    for r in rows:
        key = r["wikidata_id"] or f"{r['lang']}:{r['page_id']}"
        if key not in topics:
            topics[key] = {"wikidata_id": r["wikidata_id"], "score": 0.0, "by_meaning": 0, "by_words": 0, "titles": {}, "page_id": None}
            order.append(key)
        t = topics[key]
        t["score"] += float(r["score"])
        t["by_meaning"] |= int(r["by_meaning"])
        t["by_words"] |= int(r["by_words"])
        t["titles"][r["lang"]] = r["title"]
        if r["lang"] == "fi":
            t["page_id"] = r["page_id"]
    order.sort(key=lambda k: -topics[k]["score"])
    order = order[:limit]
    missing = [topics[k]["wikidata_id"] for k in order if topics[k]["wikidata_id"] and len(topics[k]["titles"]) < 3]
    if missing:        # the other language versions of the same topics
        for r in s.q(f"SELECT wikidata_id, lang, page_id, title FROM article WHERE wikidata_id IN ({marks(len(missing))})", missing, quiet=True):
            t = topics[r["wikidata_id"]]
            t["titles"].setdefault(r["lang"], r["title"])
            if r["lang"] == "fi":
                t["page_id"] = r["page_id"]
    ids = [topics[k]["page_id"] for k in order if topics[k]["page_id"]]
    known, leads = {}, {}
    if ids:
        known = {r["page_id"]: r for r in s.q(f"SELECT page_id, links_in, versions FROM page_stat WHERE page_id IN ({marks(len(ids))})", ids, quiet=True)}
        leads = {r["page_id"]: r["snippet"] for r in s.q(f"SELECT page_id, LEFT(text, 300) AS snippet FROM lead WHERE page_id IN ({marks(len(ids))})", ids, quiet=True)}
    out = []
    for k in order:
        t = topics[k]
        pid = t["page_id"]
        out.append({"page_id": pid, "title": t["titles"].get("fi") or t["titles"].get("en") or t["titles"].get("sv"),
                    "title_en": t["titles"].get("en"), "title_sv": t["titles"].get("sv"), "languages": sorted(t["titles"]),
                    "by_meaning": bool(t["by_meaning"]), "by_words": bool(t["by_words"]), "score": round(t["score"], 4),
                    "in_graph": pid in known, "links_in": known.get(pid, {}).get("links_in"), "snippet": leads.get(pid)})
    return out


def follow_links(s, page_ids, limit=2):
    """Where do the found articles point? An article that several results link to is often the
    answer itself (ask about a presidency, find the elections; they all link to the president)."""
    if len(page_ids) < 3:
        return []
    ids = list(page_ids)
    return s.q(f"""SELECT l.dst AS page_id, ps.title, ps.title_en, COUNT(*) AS votes
                   FROM link l JOIN page_stat ps ON ps.page_id = l.dst
                   WHERE l.src IN ({marks(len(ids))}) AND l.dst NOT IN ({marks(len(ids))})
                   GROUP BY l.dst HAVING votes >= 3 ORDER BY votes DESC, ps.links_in LIMIT %s""",
               ids + ids + [limit], note="follow the links: what do the found articles point to together?")


def sources(s, page_ids, when=None, length=2200):
    """The text an answer is written from. Today: the article (dataset S). As of a date: the article's
    opening paragraphs as they stood that day, from the system-versioned lead table."""
    if not page_ids:
        return []
    ids = list(page_ids)
    if when:
        s.asof(when)
        rows = s.q(f"SELECT l.page_id, p.title, l.rev_id, l.text, l.row_start AS since FROM lead l JOIN page p USING (page_id) WHERE l.page_id IN ({marks(len(ids))})",
                   ids, note="the opening paragraphs as they stood that day")
        s.asof(None)
        found = {r["page_id"]: {"page_id": r["page_id"], "title": r["title"], "text": r["text"], "rev_id": r["rev_id"],
                                "version_of": str(r["since"])[:10]} for r in rows if r["text"]}
    else:
        rows = s.q(f"SELECT page_id, title, rev_id, LEFT(text, %s) AS text, date_modified FROM article WHERE lang = 'fi' AND page_id IN ({marks(len(ids))})",
                   [length] + ids, quiet=True)
        found = {r["page_id"]: {"page_id": r["page_id"], "title": r["title"], "text": r["text"], "rev_id": r["rev_id"],
                                "version_of": str(r["date_modified"])[:10]} for r in rows}
    return [found[i] for i in ids if i in found]


# ---------------------------------------------------------------------------------------------
# One article: its numbers, its timeline, its connections through time
# ---------------------------------------------------------------------------------------------

def page(s, page_id):
    info = s.one("SELECT * FROM page_stat WHERE page_id = %s", (page_id,), quiet=True)
    if not info:
        return None
    months = {}

    def add(rows, field):
        for r in rows:
            months.setdefault(int(r["ym"]), {"edits": 0, "reverts": 0, "added": 0, "removed": 0})[field] = int(r["n"])

    add(s.q("SELECT EXTRACT(YEAR_MONTH FROM ts) AS ym, COUNT(*) AS n FROM revision WHERE page_id = %s GROUP BY ym", (page_id,), quiet=True), "edits")
    add(s.q("SELECT EXTRACT(YEAR_MONTH FROM ts) AS ym, SUM(is_revert) AS n FROM revision WHERE page_id = %s GROUP BY ym", (page_id,), quiet=True), "reverts")
    add(s.q("SELECT EXTRACT(YEAR_MONTH FROM row_start) AS ym, COUNT(*) AS n FROM link FOR SYSTEM_TIME ALL WHERE src = %s GROUP BY ym",
            (page_id,), note="links added per month: every period of every link this article ever had"), "added")
    add(s.q("SELECT EXTRACT(YEAR_MONTH FROM row_end) AS ym, COUNT(*) AS n FROM link FOR SYSTEM_TIME ALL WHERE src = %s AND end_rev IS NOT NULL GROUP BY ym",
            (page_id,), quiet=True), "removed")
    info["timeline"] = [dict(ym=ym, **months[ym]) for ym in sorted(months)]
    info["created_ms"], info["last_ms"] = ms(info["created"]), ms(info["last_edit"])
    info["url"] = f"{config.WIKI}/?curid={page_id}"
    info["contested"] = s.q("""SELECT src, src_title, target, dst, adds, removals, adders, removers FROM contested
                               WHERE src = %s OR dst = %s ORDER BY LEAST(adders, removers) DESC, targeted DESC LIMIT 12""",
                            (page_id, page_id), quiet=True)
    return info


def graph(s, page_id, n_out=105, n_in=45, cross_per_node=3):
    """The article's connections over its whole life, for the growth-ring picture.

    One history query per direction: FOR SYSTEM_TIME ALL returns every period of every link. The
    page then shows any moment without asking again. Busy articles have had thousands of links, so
    the picture keeps a share of every year's new links (the weightiest and longest-lived first);
    the numbers beside the picture stay exact.
    """
    center = s.one("SELECT page_id, title, title_en, created, links_in FROM page_stat WHERE page_id = %s", (page_id,), quiet=True)
    if not center:
        return None
    out_rows = s.q("""SELECT l.target, l.dst, ps.title_en, ps.links_in, ps.created,
                             l.row_start AS a, IF(l.end_rev IS NULL, NULL, l.row_end) AS b
                      FROM link FOR SYSTEM_TIME ALL l LEFT JOIN page_stat ps ON ps.page_id = l.dst
                      WHERE l.src = %s AND (l.dst IS NULL OR l.dst <> l.src)""",
                   (page_id,), note="every link this article ever had, with the period it existed")
    in_rows = s.q("""SELECT l.src, ps.title, ps.title_en, ps.links_in, ps.created,
                            l.row_start AS a, IF(l.end_rev IS NULL, NULL, l.row_end) AS b
                     FROM link FOR SYSTEM_TIME ALL l JOIN page_stat ps ON ps.page_id = l.src
                     WHERE l.dst = %s AND l.src <> l.dst""",
                  (page_id,), note="every article that ever linked here")
    end = ms(datetime.strptime(LAST_DAY, "%Y-%m-%d"))
    found = {}                                   # node id -> node; an id is a page_id, or "t:<title>" outside the set

    def node(key, title, row, direction):
        nd = found.get(key)
        if nd is None:
            nd = found[key] = {"id": key, "title": title}
            if isinstance(key, int):
                nd.update(en=row["title_en"], weight=row["links_in"] or 0, created=ms(row["created"]))
            else:
                nd["outside"] = True
        nd.setdefault(direction, []).append([ms(row["a"]), ms(row["b"])])

    for r in out_rows:
        if r["dst"]:
            node(r["dst"], r["target"], r, "out")
        elif not DATEISH.match(r["target"]):
            node("t:" + r["target"], r["target"], r, "out")
    n_targets = len(found)
    for r in in_rows:
        node(r["src"], r["title"], r, "in")
    n_sources = sum(1 for nd in found.values() if "in" in nd)

    def life(periods):
        return sum(((b or end) - a) for a, b in periods) / YEAR_MS

    def score(nd, direction):
        periods = nd[direction]
        return 2 * math.log2(2 + nd.get("weight", 0)) + 0.5 * min(life(periods), 12) + 3 * (len(periods) - 1)

    def pick(direction, budget, per_year, skip=()):
        """A share of every year's newcomers first, then the best of the rest."""
        pool = sorted((nd for nd in found.values() if direction in nd and nd["id"] not in skip), key=lambda nd: -score(nd, direction))
        cohorts, keep = {}, []
        for nd in pool:
            year = datetime.fromtimestamp(min(p[0] for p in nd[direction]) / 1000, tz=timezone.utc).year
            if cohorts.get(year, 0) < per_year:
                cohorts[year] = cohorts.get(year, 0) + 1
                keep.append(nd["id"])
        keep = keep[:budget]
        chosen = set(keep)
        keep += [nd["id"] for nd in pool if nd["id"] not in chosen][:max(0, budget - len(keep))]
        return set(keep)

    keep = pick("out", n_out, 6)
    keep |= pick("in", n_in, 2, skip=keep)
    nodes = [found[k] for k in keep]
    ids = [k for k in keep if isinstance(k, int)]
    cross = []
    if len(ids) > 1:
        rows = s.q(f"""SELECT l.src, l.dst, l.row_start AS a, IF(l.end_rev IS NULL, NULL, l.row_end) AS b
                       FROM link FOR SYSTEM_TIME ALL l
                       WHERE l.src IN ({marks(len(ids))}) AND l.dst IN ({marks(len(ids))}) AND l.src <> l.dst""",
                   ids + ids, note="links among the neighbours themselves, over time: what holds the picture together")
        pairs = {}
        for r in rows:
            pairs.setdefault((r["src"], r["dst"]), []).append([ms(r["a"]), ms(r["b"])])
        per_node = {}
        for (a, b), periods in sorted(pairs.items(), key=lambda kv: -life(kv[1])):
            if per_node.get(a, 0) < cross_per_node:
                per_node[a] = per_node.get(a, 0) + 1
                cross.append({"s": a, "d": b, "p": periods})
    return {"center": {"id": center["page_id"], "title": center["title"], "en": center["title_en"], "created": ms(center["created"]),
                       "weight": center["links_in"]},
            "nodes": nodes, "cross": cross, "end": end,
            "totals": {"out_ever": n_targets, "in_ever": n_sources, "shown": len(nodes)}}


def word_diff(old, new):
    """[["=", text], ["-", removed], ["+", added], ...] between two short texts, word by word."""
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["=", " ".join(a[i1:i2])])
        else:
            if i2 > i1:
                out.append(["-", " ".join(a[i1:i2])])
            if j2 > j1:
                out.append(["+", " ".join(b[j1:j2])])
    return out


def asof(s, page_id, when):
    """The article on one day: how it opened, how many links it had, and what has changed since."""
    when = stamp(when)
    s.asof(when)
    lead = s.one("SELECT rev_id, text, row_start AS since FROM lead WHERE page_id = %s", (page_id,),
                 note="the opening paragraphs that day (system-versioned table, plain SELECT)")
    counts = s.one("""SELECT (SELECT COUNT(*) FROM link WHERE src = %s) AS links_out,
                             (SELECT COUNT(*) FROM link WHERE dst = %s AND src <> dst) AS links_in""", (page_id, page_id),
                   note="its links that day")
    s.asof(None)
    gone = s.q("""SELECT old.target, old.dst
                  FROM link FOR SYSTEM_TIME AS OF TIMESTAMP %s AS old
                  LEFT JOIN link AS now ON now.src = old.src AND now.target = old.target
                  WHERE old.src = %s AND now.src IS NULL ORDER BY old.dst IS NULL, old.target""", (when, page_id),
               note="links it had then and does not have now: the table joined to its own past")
    new = s.q("""SELECT now.target, now.dst
                 FROM link AS now
                 LEFT JOIN link FOR SYSTEM_TIME AS OF TIMESTAMP %s AS old ON old.src = now.src AND old.target = now.target
                 WHERE now.src = %s AND old.src IS NULL ORDER BY now.dst IS NULL, now.target""", (when, page_id),
              note="links it has now and did not have then")
    today = s.one("SELECT rev_id, text FROM lead WHERE page_id = %s", (page_id,), quiet=True)
    versions = s.one("SELECT COUNT(*) AS n FROM revision WHERE page_id = %s AND ts <= %s", (page_id, when), quiet=True)["n"]
    then_text, now_text = (lead or {}).get("text") or "", (today or {}).get("text") or ""
    return {"as_of": when[:10], "exists": lead is not None,
            "lead": then_text, "lead_rev": (lead or {}).get("rev_id"), "lead_since": str(lead["since"])[:10] if lead else None,
            "lead_url": f"{config.WIKI}/?oldid={lead['rev_id']}" if lead else None,
            "lead_now": now_text, "lead_diff": word_diff(then_text, now_text) if then_text and then_text != now_text else None,
            "links_out": counts["links_out"], "links_in": counts["links_in"], "versions": versions,
            "gone": [r for r in gone if not DATEISH.match(r["target"])], "new": [r for r in new if not DATEISH.match(r["target"])]}


# Both features in one statement. The innermost SELECT is the vector search (it uses the vector
# index); around it, the same table of links is joined twice: as it is today, and as it was.
NEIGHBOURS_SQL = """
SELECT v.page_id, ps.title, ps.title_en, ROUND(v.distance, 3) AS distance,
       now.src IS NOT NULL AS linked_now,
       past.src IS NOT NULL AS linked_then,
       EXISTS (SELECT 1 FROM link FOR SYSTEM_TIME ALL ever WHERE ever.src = %s AND ever.dst = v.page_id) AS linked_ever
FROM (SELECT page_id, VEC_DISTANCE_COSINE(embedding, (SELECT embedding FROM article_vector WHERE lang = 'fi' AND page_id = %s)) AS distance
      FROM article_vector WHERE lang = 'fi' ORDER BY distance LIMIT 25) AS v
JOIN page_stat ps ON ps.page_id = v.page_id
LEFT JOIN link AS now ON now.src = %s AND now.dst = v.page_id
LEFT JOIN link FOR SYSTEM_TIME AS OF TIMESTAMP %s AS past ON past.src = %s AND past.dst = v.page_id
WHERE v.page_id <> %s
ORDER BY v.distance"""


def neighbours(s, page_id, when=None):
    """The articles closest in meaning (vector index), and whether this article links to each:
    today, on the chosen day, ever."""
    if not s.one("SELECT 1 AS x FROM article_vector WHERE lang = 'fi' AND page_id = %s", (page_id,), quiet=True):
        return {"as_of": None, "neighbours": []}
    when = stamp(when)
    rows = s.q(NEIGHBOURS_SQL, (page_id, page_id, page_id, when, page_id, page_id),
               note="closest in meaning (vector index) x linked today x linked that day (system versioning), one statement")
    return {"as_of": when[:10], "neighbours": rows}


# ---------------------------------------------------------------------------------------------
# One connection: its whole life
# ---------------------------------------------------------------------------------------------

EDGE_SQL = """
SELECT l.row_start AS added, IF(l.end_rev IS NULL, NULL, l.row_end) AS removed, l.start_rev, l.end_rev,
       ra.user AS added_by, ra.anonymous AS added_anonymous, ra.comment AS add_comment, ra.is_revert AS add_is_revert,
       rr.user AS removed_by, rr.anonymous AS removed_anonymous, rr.comment AS remove_comment, rr.is_revert AS remove_is_revert,
       c.context
FROM link FOR SYSTEM_TIME ALL l
LEFT JOIN revision ra ON ra.rev_id = l.start_rev
LEFT JOIN revision rr ON rr.rev_id = l.end_rev
LEFT JOIN link_context c ON c.src = l.src AND c.target = l.target AND c.start_rev = l.start_rev
WHERE l.src = %s AND l.target = %s
ORDER BY l.row_start"""


def edge(s, src, target):
    source = s.one("SELECT page_id, title, title_en FROM page_stat WHERE page_id = %s", (src,), quiet=True)
    if not source:
        return None
    periods = s.q(EDGE_SQL, (src, target), note="one link's whole life: every time it was added and removed, by whom, and the sentence around it")
    if not periods:
        return None
    for p in periods:
        p["added_ms"], p["removed_ms"] = ms(p["added"]), ms(p["removed"])
        p["add_url"] = f"{config.WIKI}/w/index.php?diff=prev&oldid={p['start_rev']}"
        p["remove_url"] = f"{config.WIKI}/w/index.php?diff=prev&oldid={p['end_rev']}" if p["end_rev"] else None
    dest = s.one("SELECT page_id, title, title_en FROM page_stat WHERE title = %s", (target,), quiet=True)
    people = {p["added_by"] for p in periods if p["added_by"] and not p["added_anonymous"]} | \
             {p["removed_by"] for p in periods if p["removed_by"] and not p["removed_anonymous"]}
    return {"src": source, "target": target, "dst": dest, "periods": periods, "alive": periods[-1]["removed"] is None,
            "times_added": len(periods), "times_removed": sum(1 for p in periods if p["removed"]), "registered_editors": len(people),
            "contested": s.one("SELECT adds, removals, targeted, adders, removers FROM contested WHERE src = %s AND target = %s", (src, target), quiet=True)}


def disputes(s, limit=80, quiet=False):
    return s.q("""SELECT c.src, c.src_title, ps.title_en AS src_en, c.target, c.dst, pd.title_en AS target_en,
                         c.adds, c.removals, c.adders, c.removers, c.targeted, c.first_at, c.last_at, c.alive
                  FROM contested c
                  LEFT JOIN page_stat ps ON ps.page_id = c.src
                  LEFT JOIN page_stat pd ON pd.page_id = c.dst
                  ORDER BY LEAST(c.adders, c.removers) DESC, c.targeted DESC, c.src LIMIT %s""", (limit,), quiet=quiet,
               note="contested connections (built by pipeline/analyze.py from FOR SYSTEM_TIME ALL and the edit log)")


def rewritten(s, limit=60):
    return s.q("""SELECT page_id, title, title_en, lead_versions, versions, reverts, editors FROM page_stat
                  WHERE lead_versions > 1 ORDER BY lead_versions DESC LIMIT %s""", (limit,),
               note="articles whose opening paragraphs were rewritten most often")


def lead_history(s, page_id, min_days=30):
    """The versions of the opening that stood for a while: the article's framing through time."""
    rows = s.q("""SELECT rev_id, row_start AS since, IF(row_end > NOW(), NULL, row_end) AS until, text,
                         TIMESTAMPDIFF(DAY, row_start, LEAST(row_end, NOW())) AS days
                  FROM lead FOR SYSTEM_TIME ALL WHERE page_id = %s ORDER BY row_start""", (page_id,),
               note="every version of the opening paragraphs, with the time each one stood")
    kept = [r for r in rows if r["text"] and (r["days"] >= min_days or r["until"] is None)]
    for r in kept:
        r["url"] = f"{config.WIKI}/?oldid={r['rev_id']}"
        r["since_ms"], r["until_ms"] = ms(r["since"]), ms(r["until"])
    return {"versions": len(rows), "shown": kept}


# ---------------------------------------------------------------------------------------------
# Gaps: what is missing
# ---------------------------------------------------------------------------------------------

def gaps(s, kind, limit=100):
    if kind in ("sv", "en"):
        other = "en" if kind == "sv" else "sv"
        return s.q(f"""SELECT page_id, title, title_{other} AS other_title, wikidata_id, links_in, bytes, versions
                       FROM page_stat WHERE in_s = 1 AND wikidata_id IS NOT NULL AND title_{kind} IS NULL
                       ORDER BY links_in DESC LIMIT %s""", (limit,),
                   note=f"central in the Finnish graph, no article in '{kind}' in dataset S (snapshot of August 2025)")
    if kind == "waited":
        return s.q("""SELECT w.page_id, w.title, ps.title_en, w.linked_from, w.first_wanted, w.written, w.years_waited
                      FROM waited w JOIN page_stat ps USING (page_id) ORDER BY w.linked_from DESC LIMIT %s""", (limit,),
                   note="articles that were linked to before they were written")
    if kind == "wanted":
        return s.q("SELECT target, linked_from, since FROM wanted ORDER BY linked_from DESC, target LIMIT %s", (limit,),
                   note="linked from several articles today, and no page with that title on Finnish Wikipedia")
    if kind == "orphans":
        return s.q("""SELECT page_id, title, title_en, bytes, versions, created FROM page_stat
                      WHERE links_in = 0 ORDER BY bytes DESC LIMIT %s""", (limit,),
                   note="articles of the set that no other article of the set links to")
    return None


def gap_counts(s):
    return s.one("""SELECT (SELECT COUNT(*) FROM page_stat WHERE in_s = 1 AND wikidata_id IS NOT NULL AND title_sv IS NULL) AS sv,
                           (SELECT COUNT(*) FROM page_stat WHERE in_s = 1 AND wikidata_id IS NOT NULL AND title_en IS NULL) AS en,
                           (SELECT COUNT(*) FROM waited) AS waited, (SELECT COUNT(*) FROM wanted) AS wanted,
                           (SELECT COUNT(*) FROM page_stat WHERE links_in = 0) AS orphans,
                           (SELECT COUNT(*) FROM page_stat WHERE in_s = 1) AS in_s,
                           (SELECT EXISTS (SELECT 1 FROM fiwiki_title WHERE is_article = 0)) AS all_titles""", quiet=True)


def wanted_context(s, target, limit=12):
    """What the articles that link to a missing page say about it: the encyclopedia's own notes on it."""
    return s.q("""SELECT l.src AS page_id, p.title, c.context, l.row_start AS since
                  FROM link l JOIN page p ON p.page_id = l.src
                  LEFT JOIN link_context c ON c.src = l.src AND c.target = l.target AND c.start_rev = l.start_rev
                  WHERE l.target = %s ORDER BY l.row_start LIMIT %s""", (target, limit),
               note="every sentence that links to this title today")


# ---------------------------------------------------------------------------------------------
# Paths: how do you get from one article to another, on a given day?
# ---------------------------------------------------------------------------------------------

def path(s, a, b, when=None):
    """The shortest way from a to b by following links, at most three clicks, as of a day.
    The statements carry no time clause: the session setting moves the whole graph."""
    when = stamp(when)
    names = {r["page_id"]: r for r in s.q("SELECT page_id, title, title_en FROM page_stat WHERE page_id IN (%s, %s)", (a, b), quiet=True)}
    if a not in names or b not in names:
        return None
    s.asof(when)
    hops = []
    if s.one("SELECT 1 AS x FROM link WHERE src = %s AND dst = %s LIMIT 1", (a, b), note="one click?"):
        hops = [[]]
    if not hops:
        rows = s.q("""SELECT l1.dst AS via1 FROM link l1
                      JOIN page p1 ON p1.page_id = l1.dst
                      JOIN link l2 ON l2.src = l1.dst AND l2.dst = %s
                      WHERE l1.src = %s AND l1.dst <> %s LIMIT 4""", (b, a, a), note="two clicks?")
        hops = [[r["via1"]] for r in rows]
    if not hops:
        rows = s.q("""SELECT l1.dst AS via1, l2.dst AS via2 FROM link l1
                      JOIN page p1 ON p1.page_id = l1.dst
                      JOIN link l2 ON l2.src = l1.dst
                      JOIN page p2 ON p2.page_id = l2.dst
                      JOIN link l3 ON l3.src = l2.dst AND l3.dst = %s
                      WHERE l1.src = %s AND l2.dst NOT IN (%s, %s) LIMIT 4""", (b, a, a, b), note="three clicks?")
        hops = [[r["via1"], r["via2"]] for r in rows]
    s.asof(None)
    ids = {i for h in hops for i in h}
    if ids:
        names.update({r["page_id"]: r for r in s.q(f"SELECT page_id, title, title_en FROM page_stat WHERE page_id IN ({marks(len(ids))})", list(ids), quiet=True)})
    return {"as_of": when[:10], "from": names[a], "to": names[b], "clicks": len(hops[0]) + 1 if hops else None,
            "paths": [[names[a]] + [names[i] for i in h if i in names] + [names[b]] for h in hops]}
