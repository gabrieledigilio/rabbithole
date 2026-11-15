"""The snapshot: the app answering from recorded answers, where there is no database.

    python -m unittest tests.test_replay

Needs no MariaDB: that is the point. It reads the snapshot kept in rabbithole/snapshot/ (made by
pipeline/snapshot.py) and is skipped if there is none.
"""
import os
import sys
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rabbithole import app as web, replay  # noqa: E402


class NoDatabase:
    def __init__(self, *a, **kw):
        raise AssertionError("a replayed answer must not open a database session")


@unittest.skipUnless(replay.recorded(), "no snapshot recorded: python pipeline/snapshot.py")
class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.was, cls.session = os.environ.get("SNAPSHOT"), web.Session
        os.environ["SNAPSHOT"] = "1"
        web.Session = NoDatabase
        cls.client, cls.index = web.app.test_client(), replay.index()
        cls.start = cls.index["start"]

    @classmethod
    def tearDownClass(cls):
        web.Session = cls.session
        os.environ.pop("SNAPSHOT") if cls.was is None else os.environ.update(SNAPSHOT=cls.was)

    def get(self, path, **query):
        r = self.client.get(path, query_string=query)
        return r.status_code, r.get_json()

    def depth(self, which):
        return next(int(pid) for pid, entry in self.index["pages"].items() if entry[3] == which)

    def test_the_lists_are_whole(self):
        code, overview = self.get("/api/overview")
        self.assertEqual((code, overview["stat"]["pages"]), (200, self.index["counts"]["articles"]))
        for year in overview["years"]:
            code, hubs = self.get("/api/hubs", year=year["yr"])
            self.assertEqual((code, len(hubs["hubs"])), (200, 10), year["yr"])
            self.assertTrue(hubs["sql"])                       # the SQL and its timings came along
        self.assertEqual(self.get("/api/disputes")[0], 200)
        for kind in ("sv", "en", "waited", "wanted", "orphans"):
            code, gaps = self.get("/api/gaps", kind=kind)
            self.assertEqual((code, gaps["kind"]), (200, kind))
        self.assertEqual(self.get("/api/gaps", kind="nonsense")[0], 400)
        wanted = self.get("/api/gaps", kind="wanted")[1]["rows"][0]["target"]
        self.assertTrue(self.get("/api/wanted", target=wanted)[1]["mentions"])

    def test_an_article_kept_month_by_month(self):
        code, page = self.get(f"/api/page/{self.start}")
        self.assertEqual((code, page["snapshot"]["depth"]), (200, "full"))
        self.assertTrue(self.get(f"/api/page/{self.start}/graph")[1]["nodes"])
        self.assertTrue(self.get(f"/api/page/{self.start}/leads")[1]["shown"])
        code, today = self.get(f"/api/page/{self.start}/asof")
        self.assertEqual((code, today["as_of"]), (200, self.index["last_day"]))
        self.assertNotIn("snapshot", today)

    def test_a_day_between_two_kept_days_gets_the_one_before_it(self):
        for what in ("asof", "neighbours"):
            code, kept = self.get(f"/api/page/{self.start}/{what}", t="2014-06-01")
            self.assertEqual((code, kept["as_of"]), (200, "2014-06-01"))
            self.assertNotIn("snapshot", kept)
            code, between = self.get(f"/api/page/{self.start}/{what}", t="2014-06-17")
            self.assertEqual(between["snapshot"], {"asked": "2014-06-17", "kept": "2014-06-01", "depth": "full"})
            self.assertEqual({k: v for k, v in between.items() if k != "snapshot"}, kept)
        code, year_end = self.get(f"/api/page/{self.depth('year')}/asof", t="2015-07-01")
        self.assertEqual(year_end["snapshot"]["kept"], "2014-12-31")

    def test_an_article_kept_as_it_stands_today(self):
        pid = self.depth("today")
        self.assertEqual(self.get(f"/api/page/{pid}")[1]["snapshot"]["depth"], "today")
        self.assertEqual(self.get(f"/api/page/{pid}/asof")[1]["as_of"], self.index["last_day"])
        code, past = self.get(f"/api/page/{pid}/asof", t="2010-01-01")
        self.assertEqual((code, past["snapshot"]["kept"]), (200, None))       # said, not guessed

    def test_a_link_story_names_no_editor(self):
        story = self.get("/api/overview")[1]["stories"][0]
        code, edge = self.get("/api/edge", src=story["src"], target=story["target"])
        self.assertEqual((code, edge["times_added"], edge["editors_named"]), (200, story["adds"], False))
        self.assertTrue(all(p["added_by"] is None and p["removed_by"] is None for p in edge["periods"]))
        self.assertGreater(edge["registered_editors"], 1)                   # how many there were is still told

    def test_what_is_not_in_it_says_so(self):
        code, missing = self.get("/api/page/2")
        self.assertEqual((code, missing["snapshot"]), (404, "missing"))
        self.assertEqual(self.get("/api/edge", src=self.start, target="No such target")[1]["snapshot"], "missing")
        self.assertEqual(self.get("/api/search", q="why did the war start")[1]["snapshot"], "off")
        self.assertEqual(self.get("/api/path", **{"from": self.start, "to": 2})[1]["snapshot"], "off")
        said = self.client.post("/api/explain", json={"kind": "change", "page_id": self.start}).get_data(as_text=True)
        self.assertTrue(said.startswith("⚠"))

    def test_the_title_box_offers_what_was_recorded(self):
        code, rows = self.get("/api/suggest", q="talvis")
        self.assertEqual((code, rows[0]["page_id"]), (200, self.start))
        self.assertTrue(all(str(r["page_id"]) in self.index["pages"] for r in rows if "note" not in r))
        self.assertEqual(self.get("/api/suggest", q="t")[1], [])

    def test_the_page_is_told_it_shows_a_snapshot(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("window.SNAPSHOT = {", html)
        self.assertIn("/static/app.js?v=", html)

    def test_every_article_has_its_files(self):
        for pid, entry in self.index["pages"].items():
            with zipfile.ZipFile(os.path.join(replay.DIR, "p", f"{pid}.zip")) as z:
                names = set(z.namelist())
            self.assertLessEqual({"page.json", "graph.json", "days.json", "days/00.json"}, names, pid)
            self.assertEqual("leads.json" in names, bool(entry[6]), pid)


if __name__ == "__main__":
    unittest.main()
