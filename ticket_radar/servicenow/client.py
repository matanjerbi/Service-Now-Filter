"""ServiceNow Table API client (standard library only).

Handles authentication, paging, retries on 429/5xx, custom CA bundles and proxies
(via the standard HTTPS_PROXY / NO_PROXY environment variables).
"""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import ServiceNowSettings
from .auth import AuthProvider, build_auth
from .errors import (
    AuthenticationError,
    ConnectionFailedError,
    PermissionDeniedError,
    ServiceNowError,
    UnexpectedResponseError,
)

log = logging.getLogger(__name__)

Record = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TableCheck:
    table: str
    ok: bool
    visible_records: int | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class Group:
    sys_id: str
    name: str


class ServiceNowClient:
    MAX_ATTEMPTS = 5

    def __init__(self, settings: ServiceNowSettings) -> None:
        if not settings.configured:
            raise ServiceNowError("לא הוגדר SN_INSTANCE")
        self._settings = settings
        self._base = settings.base_url
        self._ssl = self._build_ssl_context(settings)
        self._auth: AuthProvider = build_auth(settings.auth, self._post_form)

    # ------------------------------------------------------------------ public API
    def check_table(self, table: str) -> TableCheck:
        """Verify read access to a table and report how many records are visible."""
        try:
            _, headers = self._get(f"/api/now/table/{table}",
                                   {"sysparm_limit": 1, "sysparm_fields": "sys_id"})
        except ServiceNowError as exc:
            return TableCheck(table, ok=False, error=str(exc))
        return TableCheck(table, ok=True, visible_records=_int_or_none(headers.get("X-Total-Count")))

    def user_groups(self, username: str | None = None) -> list[Group]:
        """Groups `username` belongs to; the authenticated user when omitted."""
        query = f"user.user_name={username}" if username else "user=javascript:gs.getUserID()"
        body, _ = self._get("/api/now/table/sys_user_grmember", {
            "sysparm_query": query,
            "sysparm_fields": "group",
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": 1000,
        })
        groups = []
        for row in body.get("result", []):
            ref = row.get("group") or {}
            if ref.get("value"):
                groups.append(Group(ref["value"], ref.get("display_value") or ref["value"]))
        return groups

    def groups_by_name(self, names: Sequence[str]) -> list[Group]:
        body, _ = self._get("/api/now/table/sys_user_group", {
            "sysparm_query": "nameIN" + ",".join(names),
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": 1000,
        })
        return [Group(r["sys_id"], r["name"]) for r in body.get("result", [])]

    def iter_records(self, table: str, query: str, fields: Sequence[str]) -> Iterator[tuple[Record, int | None]]:
        """Yield (record, total_matching) for every record matching `query`, page by page."""
        page_size = self._settings.page_size
        offset = 0
        total: int | None = None
        while True:
            body, headers = self._get(f"/api/now/table/{table}", {
                "sysparm_query": query,
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": page_size,
                "sysparm_offset": offset,
            })
            if total is None:
                total = _int_or_none(headers.get("X-Total-Count"))
            rows = body.get("result", [])
            for row in rows:
                yield row, total
            if len(rows) < page_size:
                return
            offset += page_size

    # ------------------------------------------------------------------ transport
    @staticmethod
    def _build_ssl_context(settings: ServiceNowSettings) -> ssl.SSLContext:
        if not settings.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if settings.ca_bundle:
            return ssl.create_default_context(cafile=settings.ca_bundle)
        return ssl.create_default_context()

    def _open(self, request: urllib.request.Request) -> tuple[Any, Any]:
        with urllib.request.urlopen(request, timeout=self._settings.timeout, context=self._ssl) as resp:
            raw = resp.read().decode("utf-8")
            headers = resp.headers
        try:
            return json.loads(raw), headers
        except ValueError:
            raise UnexpectedResponseError(
                "התשובה מהשרת אינה JSON – ייתכן שהבקשה הופנתה לדף התחברות (SSO). "
                "בדוק את SN_INSTANCE ואת פרטי ההזדהות.") from None

    def _post_form(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._base + path,
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        try:
            body, _ = self._open(request)
            return body
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise AuthenticationError(f"קבלת טוקן OAuth נכשלה (HTTP {exc.code}): {detail}") from None
        except urllib.error.URLError as exc:
            raise ConnectionFailedError(f"OAuth – שגיאת חיבור: {exc.reason}") from None

    def _get(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        url = f"{self._base}{path}?{urllib.parse.urlencode(params)}"
        last_error = "הבקשה נכשלה"
        for attempt in range(self.MAX_ATTEMPTS):
            request = urllib.request.Request(url, headers={"Accept": "application/json", **self._auth.headers()})
            try:
                return self._open(request)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:400]
                if exc.code == 401:
                    if attempt == 0 and self._auth.invalidate():
                        continue
                    raise AuthenticationError("401 – ההזדהות נכשלה. בדוק משתמש/סיסמה או מפתח.") from None
                if exc.code == 403:
                    raise PermissionDeniedError(f"403 – אין הרשאה. {detail}") from None
                if exc.code == 429 or exc.code >= 500:
                    last_error = f"HTTP {exc.code}: {detail}"
                    self._backoff(attempt, exc.headers.get("Retry-After"))
                    continue
                raise ServiceNowError(f"HTTP {exc.code}: {detail}") from None
            except urllib.error.URLError as exc:
                last_error = f"שגיאת חיבור ל-{self._base}: {exc.reason}"
                self._backoff(attempt, None)
                continue
            except TimeoutError:
                last_error = f"תם הזמן ({self._settings.timeout} שניות) בהמתנה ל-{self._base}"
                self._backoff(attempt, None)
                continue
        raise ConnectionFailedError(last_error)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> None:
        delay = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt * 2, 30)
        log.warning("ניסיון %d נכשל, ממתין %d שניות", attempt + 1, delay)
        time.sleep(delay)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
