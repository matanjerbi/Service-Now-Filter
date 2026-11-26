"""Synchronisation of tickets from ServiceNow into the local store."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..config import ScopeMode, ServiceNowSettings
from ..servicenow import (
    BASE_FIELDS,
    JOURNAL_FIELDS,
    PermissionDeniedError,
    ServiceNowClient,
    ServiceNowError,
    Ticket,
    queries,
)
from ..storage import TicketRepository

log = logging.getLogger(__name__)

BATCH_SIZE = 250
PROGRESS_EVERY = 50
# Incremental syncs look back this far before the newest stored update, to absorb
# timezone differences between ServiceNow's user timezone and stored UTC values.
INCREMENTAL_OVERLAP = timedelta(days=1)


class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class DateField(StrEnum):
    CREATED = "created"
    UPDATED = "updated"

    @property
    def column(self) -> str:
        return "sys_created_on" if self is DateField.CREATED else "sys_updated_on"


@dataclass(frozen=True, slots=True)
class SyncRequest:
    mode: SyncMode = SyncMode.FULL
    date_field: DateField = DateField.CREATED
    date_from: date | None = None
    date_to: date | None = None
    tables: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SyncRequest:
        def parse_date(v: Any) -> date | None:
            return date.fromisoformat(v) if v else None
        return cls(
            mode=SyncMode(data.get("mode") or "full"),
            date_field=DateField(data.get("field") or "created"),
            date_from=parse_date(data.get("from")),
            date_to=parse_date(data.get("to")),
            tables=tuple(data.get("tables") or ()),
        )


@dataclass(slots=True)
class SyncStatus:
    running: bool = False
    table: str | None = None
    fetched: int = 0
    total: int | None = None
    saved: int = 0
    error: str | None = None
    started: str | None = None
    finished: str | None = None
    log: list[str] = field(default_factory=list)


class SyncTracker:
    """Thread-safe progress state shared between the sync worker and the web API."""
    MAX_LOG_LINES = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = SyncStatus()

    def reset(self) -> None:
        with self._lock:
            self._status = SyncStatus(running=True, started=_now())

    def update(self, **changes: Any) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(self._status, k, v)

    def message(self, text: str) -> None:
        log.info(text)
        with self._lock:
            self._status.log.append(f"[{datetime.now():%H:%M:%S}] {text}")
            del self._status.log[:-self.MAX_LOG_LINES]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self._status)
        return data

    @property
    def running(self) -> bool:
        with self._lock:
            return self._status.running


class SyncService:
    def __init__(self, settings: ServiceNowSettings, repository: TicketRepository,
                 client_factory: Callable[[ServiceNowSettings], ServiceNowClient] = ServiceNowClient) -> None:
        self._settings = settings
        self._repo = repository
        self._client_factory = client_factory
        self.tracker = SyncTracker()
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------ entry points
    def start_in_background(self, request: SyncRequest) -> bool:
        """Start a sync thread. Returns False if one is already running."""
        with self._start_lock:
            if self.tracker.running:
                return False
            self.tracker.reset()
        threading.Thread(target=self._run_safely, args=(request,), name="sync", daemon=True).start()
        return True

    def run(self, request: SyncRequest) -> bool:
        """Run synchronously (CLI). Returns True on success."""
        self.tracker.reset()
        self._run_safely(request)
        return self.tracker.snapshot()["error"] is None

    def check_connection(self) -> list[dict[str, Any]]:
        client = self._client_factory(self._settings)
        return [asdict(client.check_table(t)) for t in self._settings.tables]

    # ------------------------------------------------------------------ worker
    def _run_safely(self, request: SyncRequest) -> None:
        t = self.tracker
        try:
            saved = self._run(request)
            self._repo.mark_synced()
            t.message(f'הסתיים ✓ סה"כ {saved:,} טיקטים נשמרו/עודכנו')
        except ServiceNowError as exc:
            t.update(error=str(exc))
            t.message(f"שגיאה: {exc}")
        except Exception as exc:  # unexpected – keep the server alive, surface the error
            log.exception("sync failed")
            t.update(error=f"שגיאה לא צפויה: {exc}")
            t.message(f"שגיאה לא צפויה: {exc}")
        finally:
            t.update(running=False, finished=_now())

    def _run(self, request: SyncRequest) -> int:
        client = self._client_factory(self._settings)
        scope_clause = self._scope_clause(client)
        date_clause = self._date_clause(request)
        fields = BASE_FIELDS + (JOURNAL_FIELDS if self._settings.include_journal else ())
        tables = request.tables or self._settings.tables
        query = queries.ordered(queries.join(date_clause, scope_clause, self._settings.extra_query),
                                "sys_created_on", "sys_id")
        saved = 0
        for table in tables:
            saved += self._sync_table(client, table, query, fields)
            self.tracker.update(saved=saved)
        return saved

    def _sync_table(self, client: ServiceNowClient, table: str, query: str, fields: tuple[str, ...]) -> int:
        t = self.tracker
        t.update(table=table, fetched=0, total=None)
        t.message(f"מושך מטבלה {table} …")
        batch: list[Ticket] = []
        count = 0
        try:
            for record, total in client.iter_records(table, query, fields):
                batch.append(Ticket.from_api(record, table))
                count += 1
                if len(batch) >= BATCH_SIZE:
                    self._repo.upsert_many(batch)
                    batch.clear()
                if count % PROGRESS_EVERY == 0:
                    t.update(fetched=count, total=total)
        except PermissionDeniedError:
            t.message(f"דילוג על {table}: אין הרשאת קריאה")
            return 0
        finally:
            if batch:
                self._repo.upsert_many(batch)
        t.update(fetched=count)
        t.message(f"{table}: {count:,} טיקטים")
        return count

    # ------------------------------------------------------------------ query parts
    def _date_clause(self, request: SyncRequest) -> str:
        style = self._settings.date_query_style
        if request.mode is SyncMode.INCREMENTAL:
            latest = self._repo.latest_update()
            if not latest:
                raise ServiceNowError("אין עדיין נתונים במאגר – הרץ קודם משיכה לפי טווח תאריכים")
            since = (datetime.strptime(latest[:19], "%Y-%m-%d %H:%M:%S") - INCREMENTAL_OVERLAP).date()
            self.tracker.message(f"עדכון מהיר: טיקטים שעודכנו מאז {since}")
            return queries.date_range("sys_updated_on", since, None, style)
        verb = "נפתחו" if request.date_field is DateField.CREATED else "עודכנו"
        self.tracker.message(f"טווח: {request.date_from or '…'} עד {request.date_to or '…'} ({verb})")
        return queries.date_range(request.date_field.column, request.date_from, request.date_to, style)

    def _scope_clause(self, client: ServiceNowClient) -> str:
        scope = self._settings.scope
        msg = self.tracker.message
        match scope.mode:
            case ScopeMode.ALL:
                msg("היקף: כל הטיקטים שהמשתמש מורשה לראות")
                return ""
            case ScopeMode.MY_GROUPS:
                who = scope.user or self._settings.auth.username or None
                groups = client.user_groups(scope.user) if scope.user else client.user_groups()
                if not groups and not scope.user and self._settings.auth.username:
                    groups = client.user_groups(self._settings.auth.username)
                if not groups:
                    raise ServiceNowError(f"לא נמצאו קבוצות עבור {who or 'המשתמש המחובר'}. "
                                          "הגדר SN_SCOPE_USER או SN_SCOPE=all")
                msg(f"היקף: הקבוצות של {who or 'המשתמש המחובר'} – " + ", ".join(g.name for g in groups))
            case ScopeMode.GROUPS:
                groups = client.groups_by_name(scope.groups)
                if not groups:
                    raise ServiceNowError("לא נמצאה אף קבוצה מ-SN_SCOPE_GROUPS")
                if missing := set(scope.groups) - {g.name for g in groups}:
                    msg("אזהרה – קבוצות שלא נמצאו: " + ", ".join(sorted(missing)))
                msg("היקף: " + ", ".join(g.name for g in groups))
        return queries.in_list("assignment_group", [g.sys_id for g in groups])


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
