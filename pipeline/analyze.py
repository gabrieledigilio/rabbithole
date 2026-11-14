"""Derive the small tables the pages read from: per-article numbers, the graph year by year,
contested connections, articles that were linked before they were written.

    python pipeline/analyze.py

Everything here is SQL over the system-versioned tables: FOR SYSTEM_TIME AS OF for the yearly
snapshots, FOR SYSTEM_TIME ALL for whole lives of links. Safe to run again: tables are replaced.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import db  # noqa: E402

LAST_DAY = "2026-10-01 00:00:00"        # the day of the Wikimedia dump dataset M was cut from
con = db.connect()
cur = con.cursor()


def run(label, *statements):
    t0 = time.time()
    for sql in statements:
        cur.execute(sql)
    print(f"  {label}: {time.time() - t0:.1f} s", flush=True)


def scalar(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def page_stat():
    run("page_stat", """
CREATE OR REPLACE TABLE page_stat (
  page_id       INT UNSIGNED NOT NULL PRIMARY KEY,
  title         VARCHAR(255) NOT NULL,
  title_en      VARCHAR(255),              -- the English and Swedish articles on the same topic (dataset S)
  title_sv      VARCHAR(255),
  wikidata_id   VARCHAR(20),
  in_s          BOOL NOT NULL DEFAULT 0,   -- has today's text and a vector in dataset S
  created       DATETIME NOT NULL,
  last_edit     DATETIME,
  versions      INT NOT NULL DEFAULT 0,
  reverts       INT NOT NULL DEFAULT 0,
  editors       INT NOT NULL DEFAULT 0,
  bytes         INT,
  lead_versions INT NOT NULL DEFAULT 0,    -- how many times the opening paragraphs were rewritten
  links_out     INT NOT NULL DEFAULT 0,    -- links in the article today
  links_in      INT NOT NULL DEFAULT 0,    -- articles of the set that link to it today
  links_ever    INT NOT NULL DEFAULT 0,    -- distinct links it has ever had
  KEY (title), KEY (title_en), KEY (links_in)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_uca1400_ai_ci""",
        "INSERT INTO page_stat (page_id, title, created) SELECT page_id, title, row_start FROM page",
        """UPDATE page_stat s JOIN (SELECT page_id, COUNT(*) v, SUM(is_revert) r, COUNT(DISTINCT user) e, MAX(ts) last
                                     FROM revision GROUP BY page_id) x USING (page_id)
           SET s.versions = x.v, s.reverts = x.r, s.editors = x.e, s.last_edit = x.last""",
        "UPDATE page_stat s JOIN revision r ON r.page_id = s.page_id AND r.ts = s.last_edit SET s.bytes = r.bytes",
        """UPDATE page_stat s JOIN article fi ON fi.lang = 'fi' AND fi.page_id = s.page_id
           LEFT JOIN (SELECT wikidata_id, MIN(title) t FROM article WHERE lang = 'en' GROUP BY wikidata_id) en ON en.wikidata_id = fi.wikidata_id
           LEFT JOIN (SELECT wikidata_id, MIN(title) t FROM article WHERE lang = 'sv' GROUP BY wikidata_id) sv ON sv.wikidata_id = fi.wikidata_id
           SET s.in_s = 1, s.wikidata_id = fi.wikidata_id, s.title_en = en.t, s.title_sv = sv.t""",
        "UPDATE page_stat s JOIN (SELECT src, COUNT(*) n FROM link GROUP BY src) x ON x.src = s.page_id SET s.links_out = x.n",
        """UPDATE page_stat s JOIN (SELECT dst, COUNT(*) n FROM link WHERE dst IS NOT NULL AND dst <> src GROUP BY dst) x
           ON x.dst = s.page_id SET s.links_in = x.n""",
        """UPDATE page_stat s JOIN (SELECT src, COUNT(DISTINCT target) n FROM link FOR SYSTEM_TIME ALL GROUP BY src) x
           ON x.src = s.page_id SET s.links_ever = x.n""",
        """UPDATE page_stat s JOIN (SELECT page_id, COUNT(*) n FROM lead FOR SYSTEM_TIME ALL GROUP BY page_id) x
           USING (page_id) SET s.lead_versions = x.n""")


def years():
    """The graph at the end of every year: one FOR SYSTEM_TIME AS OF per year."""
    t0 = time.time()
    cur.execute("""CREATE OR REPLACE TABLE year_stat (yr SMALLINT PRIMARY KEY, as_of DATETIME, pages INT, links INT,
                   links_inside INT, edits INT, editors INT, reverts INT)""")
    cur.execute("""CREATE OR REPLACE TABLE hub_year (yr SMALLINT, place TINYINT, page_id INT UNSIGNED, title VARCHAR(255),
                   links_in INT, PRIMARY KEY (yr, place)) CHARACTER SET utf8mb4""")
    for yr in range(2003, 2027):
        day = LAST_DAY if yr == 2026 else f"{yr}-12-31 23:59:59"
        # One setting, and every SELECT below sees the database as it was that day. (It has to be
        # a plain SELECT: INSERT ... SELECT and CREATE TABLE ... AS SELECT ignore the setting and
        # read today's rows, so the rows are fetched first and inserted after.)
        cur.execute("SET system_versioning_asof = %s", (day,))
        cur.execute("SELECT (SELECT COUNT(*) FROM page), COUNT(*), COUNT(l.dst) FROM link l")
        snapshot = cur.fetchone()
        # The most linked-to articles that day: versioned link joined to versioned page, no time clause.
        cur.execute("""SELECT ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, p.page_id), p.page_id, p.title, COUNT(*)
                       FROM link l JOIN page p ON p.page_id = l.dst WHERE l.dst <> l.src
                       GROUP BY p.page_id ORDER BY COUNT(*) DESC, p.page_id LIMIT 15""")
        hubs = cur.fetchall()
        cur.execute("SET system_versioning_asof = DEFAULT")
        cur.execute("INSERT INTO year_stat (yr, as_of, pages, links, links_inside) VALUES (%s, %s, %s, %s, %s)", (yr, day) + snapshot)
        cur.executemany("INSERT INTO hub_year (yr, place, page_id, title, links_in) VALUES (%s, %s, %s, %s, %s)", [(yr,) + h for h in hubs])
    cur.execute("""UPDATE year_stat y JOIN (SELECT YEAR(ts) yr, COUNT(*) e, COUNT(DISTINCT user) u, SUM(is_revert) r
                                           FROM revision GROUP BY YEAR(ts)) x USING (yr)
                   SET y.edits = x.e, y.editors = x.u, y.reverts = x.r""")
    print(f"  year_stat, hub_year: {time.time() - t0:.1f} s", flush=True)


def contested():
    """A contested connection: a link several registered editors added and several removed, in edits
    that were about that link (they changed at most three links). Blanking and its reverts change
    hundreds of links at once and drop out; so does a lone vandal against the patrollers."""
    run("contested", r"""
CREATE OR REPLACE TABLE contested CHARACTER SET utf8mb4 COLLATE utf8mb4_bin AS
SELECT x.*, p.title AS src_title, x.alive_until IS NULL AS alive FROM (
  WITH ev AS (
    SELECT l.src, l.target, l.dst, l.row_start AS at, 1 AS is_add, r.user, r.anonymous, r.n_added + r.n_removed AS span,
           IF(l.end_rev IS NULL, NULL, l.row_end) AS ended
    FROM link FOR SYSTEM_TIME ALL l JOIN revision r ON r.rev_id = l.start_rev
    UNION ALL
    SELECT l.src, l.target, l.dst, l.row_end, 0, r.user, r.anonymous, r.n_added + r.n_removed, l.row_end
    FROM link FOR SYSTEM_TIME ALL l JOIN revision r ON r.rev_id = l.end_rev)
  SELECT src, target, MAX(dst) AS dst, SUM(is_add) AS adds, SUM(1 - is_add) AS removals, SUM(span <= 3) AS targeted,
         COUNT(DISTINCT CASE WHEN anonymous = 0 AND is_add = 1 THEN user END) AS adders,
         COUNT(DISTINCT CASE WHEN anonymous = 0 AND is_add = 0 THEN user END) AS removers,
         MIN(at) AS first_at, MAX(at) AS last_at,
         IF(SUM(is_add = 1 AND ended IS NULL) > 0, NULL, MAX(ended)) AS alive_until
  FROM ev
  WHERE target NOT REGEXP '^([0-9]{1,2}\\. [a-zäö]+kuuta|[0-9]{1,4}|[0-9]{1,4}-luku|[A-ZÄÖa-zäö]+kuu)$'   -- dates and years
  GROUP BY src, target
  HAVING adds >= 3 AND targeted >= 4) x
JOIN page p ON p.page_id = x.src
WHERE x.adders >= 2 AND x.removers >= 2""",
        "ALTER TABLE contested ADD PRIMARY KEY (src, target), ADD KEY (adders, removers, targeted)")


def waited():
    """Articles that others linked to before anyone wrote them: red links that turned blue."""
    run("waited", """
CREATE OR REPLACE TABLE waited CHARACTER SET utf8mb4 COLLATE utf8mb4_bin AS
SELECT p.page_id, p.title, COUNT(DISTINCT l.src) AS linked_from, MIN(l.row_start) AS first_wanted, p.row_start AS written,
       ROUND(TIMESTAMPDIFF(DAY, MIN(l.row_start), p.row_start) / 365.25, 1) AS years_waited
FROM page p JOIN link FOR SYSTEM_TIME ALL l ON l.dst = p.page_id AND l.row_start < p.row_start AND l.src <> p.page_id
GROUP BY p.page_id HAVING linked_from >= 2""",
        "ALTER TABLE waited ADD PRIMARY KEY (page_id), ADD KEY (linked_from)")


def wanted():
    """Articles nobody has written: linked from several of ours today, and no page with that title on
    Finnish Wikipedia. Best with the full title list, redirects included (download.py --titles).
    Dataset L alone (download.py --l) has articles only, so redirects and disambiguation pages look
    missing; the page says so when the list was built that way."""
    cur.execute("""CREATE OR REPLACE TABLE wanted (target VARCHAR(255) NOT NULL PRIMARY KEY, linked_from INT NOT NULL,
                   since DATETIME NOT NULL, KEY (linked_from)) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin""")
    if not scalar("SELECT EXISTS (SELECT 1 FROM fiwiki_title)"):
        return print("  wanted: left empty - needs a title list (python pipeline/download.py --titles, then load.py titles)")
    run("wanted", r"""
INSERT INTO wanted
SELECT l.target, COUNT(*), MIN(l.row_start) FROM link l LEFT JOIN fiwiki_title t ON t.title = l.target
WHERE l.dst IS NULL AND t.title IS NULL
  AND l.target NOT REGEXP '^([0-9]{1,2}\\. [a-zäö]+kuuta|[0-9]{1,4}|[0-9]{1,4}-luku|[A-ZÄÖa-zäö]+kuu)$'
GROUP BY l.target HAVING COUNT(*) >= 2""")


def stat():
    numbers = {
        "versions": scalar("SELECT COUNT(*) FROM revision"),
        "pages": scalar("SELECT COUNT(*) FROM page"),
        "editors": scalar("SELECT COUNT(DISTINCT user) FROM revision"),
        "reverts": int(scalar("SELECT SUM(is_revert) FROM revision")),
        "links_now": scalar("SELECT COUNT(*) FROM link"),
        "link_periods": scalar("SELECT COUNT(*) FROM link FOR SYSTEM_TIME ALL"),
        "links_ever": scalar("SELECT COUNT(*) FROM (SELECT 1 FROM link FOR SYSTEM_TIME ALL GROUP BY src, target) x"),
        "links_gone": scalar("SELECT COUNT(*) FROM (SELECT 1 FROM link FOR SYSTEM_TIME ALL GROUP BY src, target HAVING SUM(end_rev IS NULL) = 0) x"),
        "lead_versions": scalar("SELECT COUNT(*) FROM lead FOR SYSTEM_TIME ALL"),
        "contexts": scalar("SELECT COUNT(*) FROM link_context"),
        "articles": scalar("SELECT COUNT(*) FROM article"),
        "vectors": scalar("SELECT COUNT(*) FROM article_vector"),
        "contested": scalar("SELECT COUNT(*) FROM contested"),
        "waited": scalar("SELECT COUNT(*) FROM waited"),
        "wanted": scalar("SELECT COUNT(*) FROM wanted"),
        "titles": scalar("SELECT COUNT(*) FROM fiwiki_title"),
        "first_edit": str(scalar("SELECT MIN(ts) FROM revision"))[:10],
        "last_edit": str(scalar("SELECT MAX(ts) FROM revision"))[:10],
        "mariadb": scalar("SELECT VERSION()"),
    }
    cur.execute("CREATE OR REPLACE TABLE stat (name VARCHAR(40) PRIMARY KEY, value TEXT) CHARACTER SET utf8mb4")
    cur.executemany("INSERT INTO stat VALUES (%s, %s)", [(k, json.dumps(v)) for k, v in numbers.items()])
    return numbers


if __name__ == "__main__":
    t0 = time.time()
    cur.execute("SET SESSION max_recursive_iterations = 100000")
    page_stat()
    years()
    contested()
    waited()
    wanted()
    numbers = stat()
    con.commit()
    print(f"Done in {time.time() - t0:.0f} s.")
    for name in ("versions", "pages", "editors", "link_periods", "links_ever", "links_now", "links_gone", "lead_versions",
                 "contested", "waited", "wanted"):
        print(f"  {name:14} {numbers[name]:>10,}")
