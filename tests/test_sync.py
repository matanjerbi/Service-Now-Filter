import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.fake_servicenow import PASSWORD, USERNAME, FakeServiceNow, make_records
from ticket_radar.config import load_settings
from ticket_radar.storage import Database, TicketRepository
from ticket_radar.sync import SyncMode, SyncRequest, SyncService


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "t.db")
        self.db.initialize()
        self.repo = TicketRepository(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def service(self, sn: FakeServiceNow, **env: str) -> SyncService:
        base = {"SN_INSTANCE": sn.url, "SN_USERNAME": USERNAME, "SN_PASSWORD": PASSWORD,
                "SN_TABLES": "incident,sc_req_item", "SN_PAGE_SIZE": "200"}
        settings = load_settings(base | env)
        return SyncService(settings.servicenow, self.repo)

    def test_full_sync_pages_through_everything(self):
        with FakeServiceNow() as sn:
            ok = self.service(sn).run(SyncRequest(date_from=date(2026, 1, 1), date_to=date(2026, 11, 30)))
            offsets = [int(p["sysparm_offset"]) for t, p in sn.requests if t == "incident"]
        self.assertTrue(ok)
        self.assertEqual(self.repo.summary().count, 1234 + 321)
        self.assertEqual(offsets, list(range(0, 1234, 200)))
        self.assertIsNotNone(self.repo.summary().last_sync)

    def test_query_contains_date_range_and_order(self):
        with FakeServiceNow() as sn:
            self.service(sn).run(SyncRequest(date_from=date(2026, 1, 1), date_to=date(2026, 2, 1)))
            query = next(p["sysparm_query"] for t, p in sn.requests if t == "incident")
        self.assertIn("sys_created_onBETWEENjavascript:gs.dateGenerate('2026-01-01','00:00:00')", query)
        self.assertTrue(query.endswith("ORDERBYsys_created_on^ORDERBYsys_id"))

    def test_plain_date_style(self):
        with FakeServiceNow() as sn:
            self.service(sn, SN_DATE_QUERY_STYLE="plain").run(SyncRequest(date_from=date(2026, 1, 1)))
            query = next(p["sysparm_query"] for t, p in sn.requests if t == "incident")
        self.assertTrue(query.startswith("sys_created_on>=2026-01-01 00:00:00"))

    def test_resync_does_not_duplicate(self):
        with FakeServiceNow() as sn:
            svc = self.service(sn)
            svc.run(SyncRequest())
            svc.run(SyncRequest(mode=SyncMode.INCREMENTAL))
            incremental_query = [p["sysparm_query"] for t, p in sn.requests if t == "incident"][-1]
        self.assertEqual(self.repo.summary().count, 1234 + 321)
        self.assertIn("sys_updated_on>=javascript:gs.dateGenerate('2026-11-19'", incremental_query)

    def test_incremental_requires_existing_data(self):
        with FakeServiceNow() as sn:
            svc = self.service(sn)
            self.assertFalse(svc.run(SyncRequest(mode=SyncMode.INCREMENTAL)))
        self.assertIn("אין עדיין נתונים", svc.tracker.snapshot()["error"])

    def test_my_groups_with_scope_user(self):
        with FakeServiceNow() as sn:
            ok = self.service(sn, SN_SCOPE="my_groups", SN_SCOPE_USER="matan").run(SyncRequest())
        self.assertTrue(ok)
        with self.db.connect() as conn:
            groups = {r[0] for r in conn.execute("SELECT DISTINCT group_name FROM tickets")}
        self.assertEqual(groups, {"DBA - Redis", "Network"})

    def test_my_groups_without_membership_fails_clearly(self):
        with FakeServiceNow() as sn:
            svc = self.service(sn, SN_SCOPE="my_groups")
            self.assertFalse(svc.run(SyncRequest()))
        self.assertIn("SN_SCOPE_USER", svc.tracker.snapshot()["error"])

    def test_forbidden_table_is_skipped(self):
        with FakeServiceNow(forbidden={"sc_req_item"}) as sn:
            svc = self.service(sn)
            self.assertTrue(svc.run(SyncRequest()))
        self.assertEqual(self.repo.summary().count, 1234)
        self.assertTrue(any("דילוג על sc_req_item" in line for line in svc.tracker.snapshot()["log"]))

    def test_bad_password(self):
        with FakeServiceNow() as sn:
            svc = self.service(sn, SN_PASSWORD="wrong")
            self.assertFalse(svc.run(SyncRequest()))
        self.assertIn("401", svc.tracker.snapshot()["error"])

    def test_check_connection(self):
        with FakeServiceNow(tables={"incident": make_records("incident", "INC", 7), "sc_req_item": []},
                            forbidden={"sc_req_item"}) as sn:
            results = self.service(sn).check_connection()
        self.assertEqual(results[0]["visible_records"], 7)
        self.assertFalse(results[1]["ok"])


if __name__ == "__main__":
    unittest.main()
