import tempfile
import unittest
from pathlib import Path

from ticket_radar import demo
from ticket_radar.search.engine import MatchMode, SearchEngine, SearchRequest, Sort
from ticket_radar.search.filters import NO_GROUP, Filters, Status
from ticket_radar.servicenow import Ticket
from ticket_radar.storage import Database, TicketRepository


class SearchTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls._tmp.name) / "t.db")
        cls.db.initialize()
        cls.repo = TicketRepository(cls.db)
        demo.generate(cls.repo)
        orphan = Ticket(sys_id="x1", table="incident", sys_class="incident", number="INC9999999",
                        short_description="Orphan ticket without group", created_on="2026-01-01 00:00:00")
        cls.repo.upsert_many([orphan])
        cls.engine = SearchEngine(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def search(self, text="", **kw):
        return self.engine.search(SearchRequest(text=text, **kw))


class SearchTests(SearchTestCase):
    def test_fts_available(self):
        self.assertTrue(self.db.fts_enabled)

    def test_all_words_match_with_highlight(self):
        r = self.search("redis memory")
        self.assertEqual(r["mode_used"], "all")
        self.assertGreater(r["total"], 0)
        self.assertIn("\x01", r["results"][0]["title_hl"])

    def test_smart_mode_falls_back_to_similar(self):
        r = self.search("maxmemory OOM nonexistentword")
        self.assertEqual(r["mode_used"], "similar")
        self.assertIn("maxmemory", r["results"][0]["snippet"].replace("\x01", "").replace("\x02", ""))

    def test_all_mode_does_not_fall_back(self):
        r = self.search("maxmemory nonexistentword", mode=MatchMode.ALL)
        self.assertEqual((r["mode_used"], r["total"]), ("all", 0))

    def test_exclusion(self):
        with_role = self.search('"too many connections"')["total"]
        self.assertGreater(with_role, 0)
        self.assertEqual(self.search('"too many connections" -role')["total"], 0)

    def test_hebrew_prefix_insensitive(self):
        r = self.search("שרת")
        self.assertGreater(r["total"], 0)
        self.assertTrue(any("לשרתי" in x["short_description"] for x in r["results"]))

    def test_ticket_number_lookup(self):
        r = self.search("inc9999999")
        self.assertEqual(r["mode_used"], "number")
        self.assertEqual([x["number"] for x in r["results"]], ["INC9999999"])

    def test_status_and_group_filters(self):
        f = Filters(status=Status.OPEN, groups=("DBA - Redis",))
        r = self.search(filters=f, limit=500)
        self.assertTrue(all(x["active"] == 1 and x["group_name"] == "DBA - Redis" for x in r["results"]))
        # The group facet ignores the group filter, so other teams remain selectable.
        self.assertGreater(len(r["facets"]["groups"]), 1)
        self.assertEqual(r["facets"]["status"]["open"], r["total"])

    def test_no_group_filter(self):
        r = self.search(filters=Filters(groups=(NO_GROUP,)))
        self.assertIn("INC9999999", [x["number"] for x in r["results"]])

    def test_date_filter(self):
        r = self.search(filters=Filters(created_from="2026-01-01 00:00:00", created_to="2026-01-01 23:59:59"))
        self.assertTrue(all(x["created_on"].startswith("2026-01-01") for x in r["results"]))

    def test_paging_and_sort(self):
        first = self.search(sort=Sort.NEWEST, limit=5)["results"]
        second = self.search(sort=Sort.NEWEST, limit=5, offset=5)["results"]
        self.assertEqual(len({x["id"] for x in first + second}), 10)
        self.assertGreaterEqual(first[-1]["created_on"], second[0]["created_on"])

    def test_timeline_buckets(self):
        timeline = self.search()["facets"]["timeline"]
        self.assertEqual(timeline["unit"], "month")
        self.assertEqual(sum(p[1] for p in timeline["points"]), self.search()["total"])

    def test_similar_excludes_source(self):
        r = self.engine.similar(1)
        self.assertGreater(r["total"], 0)
        self.assertNotIn(1, [x["id"] for x in r["results"]])

    def test_export_returns_all_rows(self):
        rows = list(self.engine.export(SearchRequest(text="redis")))
        self.assertEqual(len(rows), self.search("redis")["total"])


class LikeFallbackTests(SearchTestCase):
    def test_like_mode_when_fts_unavailable(self):
        self.db.fts_enabled = False
        try:
            r = self.search("redis memory")
        finally:
            self.db.fts_enabled = True
        self.assertEqual(r["mode_used"], "like")
        self.assertGreater(r["total"], 0)


class RepositoryTests(SearchTestCase):
    def test_upsert_updates_existing_row(self):
        t = Ticket(sys_id="u1", table="incident", sys_class="incident", number="INC1", short_description="zebrafoo")
        self.repo.upsert_many([t])
        t.short_description, t.active = "quuxbar", True
        self.repo.upsert_many([t])
        r = self.search("quuxbar")
        self.assertEqual([x["number"] for x in r["results"]], ["INC1"])
        self.assertEqual(self.search("zebrafoo", mode=MatchMode.ALL)["total"], 0)


if __name__ == "__main__":
    unittest.main()
