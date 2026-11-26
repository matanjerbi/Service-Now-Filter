"""Minimal HTTP layer: routing, JSON helpers and static files (standard library only)."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Explicit MIME types: on Windows `mimetypes` may map .js to text/plain, which breaks ES modules.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    body: bytes

    def arg(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def int_arg(self, name: str, default: int) -> int:
        raw = self.arg(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise HttpError(HTTPStatus.BAD_REQUEST, f"פרמטר {name} חייב להיות מספר") from None

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except ValueError:
            raise HttpError(HTTPStatus.BAD_REQUEST, "גוף הבקשה אינו JSON תקין") from None
        if not isinstance(data, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "גוף הבקשה צריך להיות אובייקט JSON")
        return data


@dataclass(slots=True)
class Response:
    status: HTTPStatus = HTTPStatus.OK
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] | None = None

    @classmethod
    def json(cls, data: Any, status: HTTPStatus = HTTPStatus.OK) -> Response:
        return cls(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def error(cls, status: HTTPStatus, message: str) -> Response:
        return cls.json({"error": message}, status)


Handler = Callable[[Request], Response]


class Router:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Handler] = {}

    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            self._routes[(method, path)] = fn
            return fn
        return register

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self.route("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self.route("POST", path)

    def resolve(self, method: str, path: str) -> Handler | None:
        return self._routes.get((method, path))

    def paths(self) -> Iterable[str]:
        return {p for _, p in self._routes}


_SAFE_STATIC = re.compile(r"^[\w\-./]+$")


def serve_static(path: str) -> Response:
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    if not _SAFE_STATIC.match(rel) or ".." in rel:
        return Response.error(HTTPStatus.NOT_FOUND, "not found")
    file = (STATIC_DIR / rel).resolve()
    if STATIC_DIR.resolve() not in file.parents or not file.is_file():
        return Response.error(HTTPStatus.NOT_FOUND, "not found")
    ctype = CONTENT_TYPES.get(file.suffix, "application/octet-stream")
    return Response(HTTPStatus.OK, file.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def make_handler_class(router: Router) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "TicketRadar"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if "/api/sync/status" not in self.path:
                log.debug("%s %s", self.address_string(), fmt % args)

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            request = Request(method, url.path, parse_qs(url.query), self.headers,
                              self.rfile.read(length) if length else b"")
            try:
                if method == "POST" and not self._same_origin():
                    raise HttpError(HTTPStatus.FORBIDDEN, "forbidden origin")
                handler = router.resolve(method, url.path)
                if handler is not None:
                    response = handler(request)
                elif method == "GET" and not url.path.startswith("/api/"):
                    response = serve_static(url.path)
                else:
                    response = Response.error(HTTPStatus.NOT_FOUND, "not found")
            except HttpError as exc:
                response = Response.error(exc.status, exc.message)
            except Exception as exc:  # keep the server alive; report to the UI
                log.exception("unhandled error on %s %s", method, url.path)
                response = Response.error(HTTPStatus.INTERNAL_SERVER_ERROR, f"שגיאה פנימית: {exc}")
            self._write(response)

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin.split("://", 1)[-1] == (self.headers.get("Host") or "")

        def _write(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (response.headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

    return RequestHandler


def create_server(router: Router, host: str, port: int, port_attempts: int = 20) -> ThreadingHTTPServer:
    """Bind to `port`, or the next free one."""
    handler = make_handler_class(router)
    last: OSError | None = None
    for candidate in range(port, port + port_attempts):
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
            server.daemon_threads = True
            return server
        except OSError as exc:
            last = exc
    raise OSError(f"לא נמצא פורט פנוי בטווח {port}-{port + port_attempts - 1}") from last
