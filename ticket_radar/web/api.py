"""JSON API consumed by the web UI."""
from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from http import HTTPStatus
from typing import TypeVar

from ..config import Settings
from ..search.engine import MatchMode, SearchEngine, SearchRequest, Sort
from ..search.filters import Filters, Status
from ..servicenow import TABLE_LABELS, ServiceNowError
from ..storage import Database, TicketRepository
from ..sync import SyncRequest, SyncService
from .server import HttpError, Request, Response, Router

MAX_PAGE = 500
E = TypeVar("E", bound=StrEnum)

EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("number", "מספר"), ("tbl", "סוג"), ("short_description", "תיאור קצר"), ("state", "סטטוס"),
    ("priority", "עדיפות"), ("group_name", "צוות"), ("assigned_to", "מטפל"), ("opened_by", "פותח"),
    ("created_on", "נפתח (UTC)"), ("updated_on", "עודכן (UTC)"), ("closed_at", "נסגר (UTC)"),
    ("cmdb_ci", "רכיב (CI)"), ("category", "קטגוריה"), ("description", "תיאור"), ("close_notes", "הערות סגירה"),
)


@dataclass(slots=True)
class AppContext:
    settings: Settings
    db: Database
    repo: TicketRepository
    search: SearchEngine
    sync: SyncService
    demo: bool = False


def _list_arg(req: Request, name: str) -> tuple[str, ...]:
    return tuple(x for x in req.arg(name).split("|") if x)


def _enum_arg(req: Request, name: str, enum: type[E], default: E) -> E:
    raw = req.arg(name)
    try:
        return enum(raw) if raw else default
    except ValueError:
        raise HttpError(HTTPStatus.BAD_REQUEST, f"ערך לא חוקי ל-{name}: {raw}") from None


def _utc_bound(raw: str, end: bool) -> str | None:
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (the UI sends UTC)."""
    raw = raw.strip().replace("T", " ")
    if not raw:
        return None
    if len(raw) == 10:
        raw += " 23:59:59" if end else " 00:00:00"
    return raw[:19]


def search_request_from(req: Request, *, paged: bool = True) -> SearchRequest:
    filters = Filters(
        created_from=_utc_bound(req.arg("from"), end=False),
        created_to=_utc_bound(req.arg("to"), end=True),
        groups=_list_arg(req, "groups"),
        tables=_list_arg(req, "tables"),
        priorities=_list_arg(req, "priorities"),
        status=_enum_arg(req, "status", Status, Status.ANY),
    )
    return SearchRequest(
        text=req.arg("q"),
        mode=_enum_arg(req, "mode", MatchMode, MatchMode.SMART),
        filters=filters,
        sort=_enum_arg(req, "sort", Sort, Sort.RELEVANCE),
        limit=min(max(req.int_arg("limit", 50), 1), MAX_PAGE) if paged else MAX_PAGE,
        offset=max(req.int_arg("offset", 0), 0) if paged else 0,
    )


def build_router(ctx: AppContext) -> Router:
    router = Router()
    sn = ctx.settings.servicenow

    def require_live() -> None:
        if ctx.demo or not sn.configured:
            raise HttpError(HTTPStatus.BAD_REQUEST,
                            "אין חיבור ל-ServiceNow במצב דמו. הגדר SN_INSTANCE והפעל בלי --demo.")

    @router.get("/api/status")
    def status(_: Request) -> Response:
        return Response.json({
            "demo": ctx.demo,
            "configured": sn.configured and not ctx.demo,
            "instance": "" if ctx.demo else sn.base_url,
            "tables": list(sn.tables),
            "table_labels": TABLE_LABELS,
            "scope": {"mode": sn.scope.mode.value, "groups": list(sn.scope.groups), "user": sn.scope.user},
            "fts": ctx.db.fts_enabled,
            "db": asdict(ctx.repo.summary()),
            "sync": ctx.sync.tracker.snapshot(),
        })

    @router.get("/api/search")
    def search(req: Request) -> Response:
        started = datetime.now()
        result = ctx.search.search(search_request_from(req))
        result["ms"] = int((datetime.now() - started).total_seconds() * 1000)
        return Response.json(result)

    @router.get("/api/ticket")
    def ticket(req: Request) -> Response:
        data = ctx.repo.get(req.int_arg("id", -1))
        if data is None:
            raise HttpError(HTTPStatus.NOT_FOUND, "טיקט לא נמצא")
        return Response.json(data)

    @router.get("/api/similar")
    def similar(req: Request) -> Response:
        return Response.json(ctx.search.similar(req.int_arg("id", -1)))

    @router.get("/api/export.csv")
    def export(req: Request) -> Response:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([label for _, label in EXPORT_COLUMNS])
        for row in ctx.search.export(search_request_from(req, paged=False)):
            writer.writerow([row[col] or "" for col, _ in EXPORT_COLUMNS])
        filename = f"tickets_{datetime.now():%Y%m%d_%H%M}.csv"
        return Response(HTTPStatus.OK, ("﻿" + buf.getvalue()).encode("utf-8"),  # BOM → Excel reads UTF-8
                        "text/csv; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/api/sync/status")
    def sync_status(_: Request) -> Response:
        return Response.json(ctx.sync.tracker.snapshot())

    @router.post("/api/sync")
    def start_sync(req: Request) -> Response:
        require_live()
        try:
            sync_req = SyncRequest.from_json(req.json())
        except ValueError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, f"בקשת סנכרון לא תקינה: {exc}") from None
        if not ctx.sync.start_in_background(sync_req):
            raise HttpError(HTTPStatus.CONFLICT, "סנכרון כבר רץ")
        return Response.json({"started": True}, HTTPStatus.ACCEPTED)

    @router.post("/api/test")
    def test_connection(_: Request) -> Response:
        require_live()
        try:
            return Response.json({"results": ctx.sync.check_connection()})
        except ServiceNowError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, str(exc)) from None

    return router
