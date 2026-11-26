import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ticket_radar.app import build_context
from ticket_radar.config import load_settings
from ticket_radar.web import build_router, create_server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        settings = load_settings({}, base_dir=Path(cls._tmp.name))
        ctx = build_context(settings, demo=True, data_dir=Path(cls._tmp.name))
        cls.server = create_server(build_router(ctx), "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # Bypass any proxy configured in the environment.
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def json(self, path, **kw):
        status, _, body = self.request(path, **kw)
        return status, json.loads(body)

    def test_status(self):
        status, data = self.json("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["demo"])
        self.assertEqual(data["db"]["count"], 1800)

    def test_search_and_ticket(self):
        _, data = self.json("/api/search?q=redis&status=open&limit=3")
        self.assertEqual(len(data["results"]), 3)
        self.assertIn("facets", data)
        _, ticket = self.json(f"/api/ticket?id={data['results'][0]['id']}")
        self.assertEqual(ticket["number"], data["results"][0]["number"])
        self.assertNotIn("heb_forms", ticket)

    def test_bad_parameters(self):
        self.assertEqual(self.json("/api/search?status=nope")[0], 400)
        self.assertEqual(self.json("/api/search?limit=abc")[0], 400)
        self.assertEqual(self.json("/api/ticket?id=999999")[0], 404)

    def test_export_csv_has_bom_and_header(self):
        status, headers, body = self.request("/api/export.csv?q=redis")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("﻿".encode()))
        self.assertIn("מספר", body.decode("utf-8-sig").splitlines()[0])
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_sync_disabled_in_demo(self):
        self.assertEqual(self.json("/api/sync", method="POST", body={})[0], 400)

    def test_cross_origin_post_rejected(self):
        status, _ = self.json("/api/sync", method="POST", body={}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_static_files_and_traversal(self):
        status, headers, _ = self.request("/js/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertEqual(self.request("/../../run.py")[0], 404)
        self.assertEqual(self.request("/%2e%2e/run.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
