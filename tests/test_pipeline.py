"""The whole way through, on a Wikipedia of four articles whose history we know by heart
(tests/fixture.py): extract -> load -> analyze -> the queries and the JSON API.

    python -m unittest tests.test_pipeline

Needs a running MariaDB (docker compose up -d --wait). It works in a database of its own,
rabbithole_test, and never touches the real one; if MariaDB cannot be reached the tests are
skipped. No embedding model and no language model are used: the app must cope without them.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402
from fixture import MANNERHEIM, SAKSA, SUOMI, TALVISOTA  # noqa: E402
from rabbithole import app as web, config, db, llm  # noqa: E402

TEST_DB = os.environ.get("TEST_DB_NAME", "rabbithole_test")
NOWHERE = "http://127.0.0.1:9"          # nothing listens here: stands for "no model installed"


def ensure_database():
    """Make sure the test database exists and the app's user may use it; skip the tests otherwise."""
    where = {k: config.DB[k] for k in ("host", "port")}
    try:
        pymysql.connect(**dict(config.DB, database=TEST_DB)).close()
        return
    except pymysql.MySQLError:
        pass
    for user, password in ((config.DB["user"], config.DB["password"]), ("root", os.environ.get("DB_ROOT_PASSWORD", "rabbithole"))):
        try:
            con = pymysql.connect(user=user, password=password, autocommit=True, **where)
            cur = con.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB}` CHARACTER SET utf8mb4")
            cur.execute(f"GRANT ALL ON `{TEST_DB}`.* TO %s@'%%'", (config.DB["user"],))
            con.close()
            return
        except pymysql.MySQLError:
            continue
    raise unittest.SkipTest(f"No MariaDB to test against at {where['host']}:{where['port']} (docker compose up -d --wait), "
                            f"or no right to create the database {TEST_DB}.")


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert TEST_DB.endswith("_test"), "the tests drop every table: they only run in a database named *_test"
        ensure_database()
        cls.folder = fixture.build(tempfile.mkdtemp(prefix="rabbithole-test-"))
        env = dict(os.environ, DB_NAME=TEST_DB, DATA_DIR=cls.folder, OLLAMA_URL=NOWHERE, LLM_BASE_URL=NOWHERE + "/v1")
        for step in (["pipeline/extract.py", os.path.join(cls.folder, "m"), os.path.join(cls.folder, "graph")],
                     ["pipeline/load.py", "--reset"], ["pipeline/analyze.py"]):
            done = subprocess.run([sys.executable] + step, cwd=ROOT, env=env, capture_output=True, text=True)
            if done.returncode:
                raise AssertionError(f"{step[0]} failed:\n{done.stdout}\n{done.stderr}")
        # point this process at the same database, and at no models
        cls.saved = (config.DB, config.OLLAMA_URL, config.LLM_BASE_URL, config.LLM_MODEL)
        config.DB = dict(config.DB, database=TEST_DB)
        config.OLLAMA_URL, config.LLM_BASE_URL, config.LLM_MODEL = NOWHERE, NOWHERE + "/v1", ""
        cls.fresh()
        cls.client = web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        config.DB, config.OLLAMA_URL, config.LLM_BASE_URL, config.LLM_MODEL = cls.saved
        cls.fresh()
        shutil.rmtree(cls.folder, ignore_errors=True)

    @staticmethod
    def fresh():
        db._local.con = None
        llm._seen.clear()
        web._overview.clear()

    # ---- helpers
    def rows(self, sql, params=()):
        with db.Session() as s:
            return s.q(sql, params)

    @staticmethod
    def as_of(day):
        """The clause for a table as it was on a day: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (UTC)."""
        return f"FOR SYSTEM_TIME AS OF TIMESTAMP '{day if len(day) > 10 else day + ' 00:00:00'}'"

    def links(self, src, when=None):
        return {r["target"] for r in self.rows(f"SELECT target FROM link {self.as_of(when) if when else ''} WHERE src = %s", (src,))}

    def get(self, url, status=200):
        r = self.client.get(url)
        self.assertEqual(r.status_code, status, r.get_data(as_text=True)[:300])
        return r.get_json()

    # ---- the tables
    def test_what_was_loaded(self):
        stat = {r["name"]: r["value"] for r in self.rows("SELECT name, value FROM stat")}
        self.assertEqual([stat[k] for k in ("pages", "versions", "links_now", "link_periods", "lead_versions")],
                         ["4", "13", "13", "15", "11"])

    def test_links_as_of_a_day(self):
        self.assertEqual(self.links(TALVISOTA, "2005-06-01"), {"Suomi", "Neuvostoliitto", "Mannerheim-linja"})
        self.assertIn("Saksa", self.links(TALVISOTA, "2007-01-01"))
        self.assertNotIn("Saksa", self.links(TALVISOTA, "2011-01-01"))       # removed 5 May 2010
        self.assertIn("Saksa", self.links(TALVISOTA, "2013-01-01"))          # back 7 Jul 2012
        self.assertNotIn("Saksa", self.links(TALVISOTA, "2015-01-01"))       # removed 1 Jan 2014
        self.assertIn("Saksa", self.links(TALVISOTA))                        # back 2 Feb 2016, there today

    def test_a_link_has_one_row_per_period(self):
        periods = self.rows("""SELECT start_rev, end_rev, row_start, row_end FROM link FOR SYSTEM_TIME ALL
                               WHERE src = %s AND target = 'Saksa' ORDER BY row_start""", (TALVISOTA,))
        self.assertEqual([(p["start_rev"], p["end_rev"]) for p in periods], [(102, 105), (106, 107), (108, None)])
        self.assertEqual(str(periods[0]["row_start"])[:16], "2006-03-01 00:00")       # Wikipedia's time, not load time
        self.assertEqual(str(periods[0]["row_end"])[:16], "2010-05-05 10:00")

    def test_blanking_and_its_revert_do_not_break_a_period(self):
        # blanked at 05:00, restored at 06:00 on 1 March 2006
        self.assertEqual(self.links(TALVISOTA, "2006-03-01 05:30:00"), {"Suomi", "Neuvostoliitto", "Mannerheim-linja", "Saksa"})
        once = self.rows("SELECT COUNT(*) AS n FROM link FOR SYSTEM_TIME ALL WHERE src = %s AND target = 'Suomi'", (TALVISOTA,))
        self.assertEqual(once[0]["n"], 1)
        log = {r["rev_id"]: r for r in self.rows("SELECT rev_id, is_revert, n_removed, anonymous FROM revision WHERE page_id = %s", (TALVISOTA,))}
        self.assertEqual((log[103]["n_removed"], log[103]["anonymous"], log[104]["is_revert"]), (4, 1, 1))   # the edit log still has it

    def test_an_article_does_not_exist_before_it_was_written(self):
        then = self.rows(f"SELECT title FROM page {self.as_of('2005-06-01')} ORDER BY title")
        self.assertEqual([r["title"] for r in then], ["Saksa", "Suomi", "Talvisota"])
        self.assertEqual(len(self.rows("SELECT 1 FROM page")), 4)

    def test_one_setting_moves_a_whole_session_and_does_not_leak(self):
        join = "SELECT p.title, COUNT(*) AS n FROM link l JOIN page p ON p.page_id = l.dst WHERE l.dst <> l.src GROUP BY p.page_id ORDER BY p.title"
        with db.Session() as s:
            s.asof("2005-06-01")
            then = {r["title"]: r["n"] for r in s.q(join)}
        self.assertEqual(then, {"Suomi": 1, "Talvisota": 1})                 # Mannerheim-linja: linked to, not yet a page
        with db.Session() as s:                                              # the next request on the same connection
            now = {r["title"]: r["n"] for r in s.q(join)}
        self.assertEqual(now, {"Mannerheim-linja": 2, "Saksa": 1, "Suomi": 1, "Talvisota": 3})

    def test_opening_paragraphs_as_of_a_day(self):
        def lead(when):
            return self.rows(f"SELECT rev_id, text FROM lead {self.as_of(when)} WHERE page_id = %s", (TALVISOTA,))[0]
        self.assertEqual(lead("2005-06-01")["rev_id"], 101)
        self.assertIn("Saksa tuki Neuvostoliittoa", lead("2007-01-01")["text"])
        self.assertEqual(lead("2006-03-01 05:30:00")["text"], "")            # the blanked hour is history too
        self.assertNotIn("Saksa", lead("2011-01-01")["text"])
        self.assertNotIn("[[", lead("2007-01-01")["text"])                   # plain text, no wiki markup

    def test_vector_search(self):
        near = self.rows("""SELECT a.title FROM (
                              SELECT lang, page_id, VEC_DISTANCE_COSINE(embedding,
                                       (SELECT embedding FROM article_vector WHERE lang = 'fi' AND page_id = %s)) AS distance
                              FROM article_vector ORDER BY distance LIMIT 3) v JOIN article a USING (lang, page_id)
                            ORDER BY v.distance""", (TALVISOTA,))
        self.assertEqual([r["title"] for r in near], ["Talvisota", "Winter War", "Mannerheim-linja"])

    # ---- the API
    def test_meaning_and_links_in_one_statement(self):
        seen = {n["title"]: n for n in self.get(f"/api/page/{TALVISOTA}/neighbours?t=2011-01-01")["neighbours"]}
        self.assertEqual(list(seen)[0], "Mannerheim-linja")                  # the nearest in meaning
        saksa = seen["Saksa"]                                                # linked today and once before, not in 2011
        self.assertEqual((saksa["linked_now"], saksa["linked_then"], saksa["linked_ever"]), (1, 0, 1))

    def test_article_as_of_a_day(self):
        then = self.get(f"/api/page/{TALVISOTA}/asof?t=2011-01-01")
        self.assertEqual((then["links_out"], then["links_in"], then["versions"], then["lead_rev"]), (3, 2, 5, 105))
        self.assertEqual([x["target"] for x in then["new"]], ["Saksa"])
        self.assertEqual(then["gone"], [])
        self.assertIn(["+", "Saksa oli solminut Neuvostoliiton kanssa hyökkäämättömyyssopimuksen."], then["lead_diff"])

    def test_graph_of_an_article(self):
        graph = self.get(f"/api/page/{TALVISOTA}/graph")
        nodes = {n["title"]: n for n in graph["nodes"]}
        self.assertEqual(set(nodes), {"Saksa", "Suomi", "Mannerheim-linja", "Neuvostoliitto"})
        self.assertEqual(len(nodes["Saksa"]["out"]), 3)                      # three periods
        self.assertTrue(nodes["Neuvostoliitto"].get("outside"))              # exists, but not one of our articles
        line = nodes["Mannerheim-linja"]
        self.assertLess(line["out"][0][0], line["created"])                  # linked years before it was written

    def test_story_of_a_disputed_link(self):
        edge = self.get(f"/api/edge?src={TALVISOTA}&target=Saksa")
        self.assertEqual([(p["added_by"], p.get("removed_by")) for p in edge["periods"]],
                         [("Bertil", "Cecilia"), ("Bertil", "David"), ("Eeva", None)])
        self.assertTrue(all("⟦Saksa⟧" in p["context"] for p in edge["periods"]))
        self.assertEqual(edge["periods"][0]["remove_comment"], "ei lähdettä")
        self.assertTrue(edge["alive"])

    def test_disputes_page(self):
        found = self.get("/api/disputes")["connections"]
        self.assertEqual([(c["src_title"], c["target"], c["adds"], c["removals"], c["adders"], c["removers"]) for c in found],
                         [("Talvisota", "Saksa", 3, 2, 2, 2)])

    def test_gaps(self):
        waited = self.get("/api/gaps?kind=waited")["rows"]
        self.assertEqual([(w["title"], w["linked_from"], w["years_waited"]) for w in waited], [("Mannerheim-linja", 2, 5.6)])
        wanted = self.get("/api/gaps?kind=wanted")
        self.assertEqual([(w["target"], w["linked_from"]) for w in wanted["rows"]], [("Tuntematon aihe", 2)])   # Eurooppa exists: not wanted
        self.assertTrue(wanted["counts"]["all_titles"])                      # built from the full title list: no caveat on the page
        mentions = self.get("/api/wanted?target=Tuntematon aihe")["mentions"]
        self.assertEqual({m["title"] for m in mentions}, {"Suomi", "Saksa"})
        self.assertEqual({r["title"] for r in self.get("/api/gaps?kind=en")["rows"]}, {"Saksa", "Mannerheim-linja"})

    def test_path_today_and_in_the_past(self):
        today = self.get(f"/api/path?from={SAKSA}&to={MANNERHEIM}")
        self.assertEqual((today["clicks"], [p["title"] for p in today["paths"][0]]), (2, ["Saksa", "Talvisota", "Mannerheim-linja"]))
        self.assertEqual(self.get(f"/api/path?from={SAKSA}&to={MANNERHEIM}&t=2008-01-01")["paths"], [])   # Saksa linked nowhere near yet

    def test_the_graph_of_a_past_year(self):
        y2010 = self.get("/api/hubs?year=2010")
        self.assertEqual((y2010["pages"], y2010["links"]), (4, 10))
        self.assertEqual({h["title"]: h["links_in"] for h in y2010["hubs"]}, {"Talvisota": 2, "Mannerheim-linja": 2, "Suomi": 1})

    def test_search_without_an_embedding_model_falls_back_to_words(self):
        found = self.get("/api/search?q=Talvisota")
        self.assertEqual((found["mode"], found["topics"][0]["page_id"]), ("words", TALVISOTA))
        then = self.get("/api/search?q=Talvisota&year=2010")                # the sources are the text as it stood then
        self.assertEqual((then["as_of"], then["sources"][0]["rev_id"]), ("2010-12-31", 105))

    def test_answer_without_a_language_model_says_so(self):
        r = self.client.post("/api/answer", json={"q": "Mikä oli talvisota?", "page_ids": [TALVISOTA]})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_data(as_text=True).startswith("⚠ No language model"))

    def test_every_answer_carries_its_sql(self):
        for url in (f"/api/page/{TALVISOTA}", f"/api/page/{TALVISOTA}/graph", f"/api/page/{TALVISOTA}/asof?t=2011-01-01",
                    f"/api/edge?src={TALVISOTA}&target=Saksa", "/api/disputes", "/api/gaps?kind=waited", "/api/search?q=Suomi"):
            log = self.get(url)["sql"]
            self.assertTrue(log and all(entry["sql"] and "ms" in entry for entry in log), url)
        asof = self.get(f"/api/page/{TALVISOTA}/asof?t=2011-01-01")["sql"]
        self.assertTrue(any("system_versioning_asof" in e["sql"] or "FOR SYSTEM_TIME" in e["sql"] for e in asof))

    def test_wrong_requests_get_a_plain_answer(self):
        self.assertIn("error", self.get("/api/page/999", status=404))
        self.assertIn("error", self.get(f"/api/page/{TALVISOTA}/asof?t=nonsense", status=400))

    def test_the_page_itself(self):
        for url, expect in (("/", "Rabbit Hole"), ("/static/app.js", "class Rings"), ("/static/vendor/d3.min.js", "d3js.org")):
            with self.client.get(url) as r:
                self.assertEqual(r.status_code, 200, url)
                self.assertIn(expect, r.get_data(as_text=True), url)
        self.assertEqual(self.get("/api/status")["embed"], False)
        self.assertEqual(self.get(f"/api/page/{SUOMI}")["title_en"], "Finland")


if __name__ == "__main__":
    unittest.main()
