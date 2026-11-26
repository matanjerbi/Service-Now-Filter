"""In-process fake of the ServiceNow Table API, for tests."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USERNAME, PASSWORD = "svc", "pw"
GROUPS = {"g1": "DBA - Redis", "g2": "Network", "g3": "Linux Infra"}
MEMBERSHIPS = {"svc": [], "matan": ["g1", "g2"]}


def _field(value: str, display: str | None = None) -> dict[str, str]:
    return {"value": value, "display_value": display if display is not None else value}


def make_records(table: str, prefix: str, count: int) -> list[dict]:
    rows = []
    group_ids = list(GROUPS)
    for i in range(count):
        gid = group_ids[i % len(group_ids)]
        rows.append({
            "sys_id": _field(f"{table}-{i:05d}"),
            "number": _field(f"{prefix}{i:07d}"),
            "short_description": _field(f"Redis timeout on shard {i % 9}"),
            "description": _field("בדיקה של תקלה בשרתים"),
            "state": _field("2", "In Progress"),
            "priority": _field("3", "3 - Moderate"),
            "assignment_group": _field(gid, GROUPS[gid]),
            "active": _field("true" if i % 4 == 0 else "false"),
            "sys_created_on": _field(f"2026-{(i % 11) + 1:02d}-10 08:00:00"),
            "sys_updated_on": _field("2026-11-20 08:00:00"),
            "sys_class_name": _field(table),
        })
    return rows


class FakeServiceNow:
    """Start with `with FakeServiceNow() as sn: ...`; `sn.url` is the instance URL."""

    def __init__(self, tables: dict[str, list[dict]] | None = None, forbidden: set[str] | None = None) -> None:
        self.tables = tables if tables is not None else {
            "incident": make_records("incident", "INC", 1234),
            "sc_req_item": make_records("sc_req_item", "RITM", 321),
        }
        self.forbidden = forbidden or set()
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> FakeServiceNow:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self
        expected_auth = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _send(self, status: int, body: object, headers: dict[str, str] | None = None) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                url = urlparse(self.path)
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                table = url.path.rsplit("/", 1)[-1]
                fake.requests.append((table, params))
                if self.headers.get("Authorization") != expected_auth:
                    return self._send(401, {"error": "unauthorized"})
                if table in fake.forbidden:
                    return self._send(403, {"error": "forbidden"})
                query = params.get("sysparm_query", "")
                if table == "sys_user_grmember":
                    user = query.split("user.user_name=")[-1] if "user.user_name=" in query else USERNAME
                    rows = [{"group": _field(g, GROUPS[g])} for g in MEMBERSHIPS.get(user, [])]
                    return self._send(200, {"result": rows})
                if table == "sys_user_group":
                    names = query.removeprefix("nameIN").split(",")
                    rows = [{"sys_id": g, "name": n} for g, n in GROUPS.items() if n in names]
                    return self._send(200, {"result": rows})
                rows = fake.tables.get(table, [])
                if "assignment_groupIN" in query:
                    allowed = query.split("assignment_groupIN")[1].split("^")[0].split(",")
                    rows = [r for r in rows if r["assignment_group"]["value"] in allowed]
                offset = int(params.get("sysparm_offset", 0))
                limit = int(params.get("sysparm_limit", 10_000))
                self._send(200, {"result": rows[offset:offset + limit]}, {"X-Total-Count": str(len(rows))})

        return Handler
