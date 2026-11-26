"""Persistence of tickets and sync metadata."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..search.hebrew import document_forms
from ..servicenow.models import Ticket
from .database import Database

# Ticket attribute -> DB column (where they differ).
_COLUMN_FOR = {"table": "tbl"}
_TICKET_FIELDS = tuple(Ticket.__dataclass_fields__)
_COLUMNS = (*(_COLUMN_FOR.get(f, f) for f in _TICKET_FIELDS), "heb_forms", "synced_at")

_UPSERT_SQL = (
    f"INSERT INTO tickets ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT(sys_id) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "sys_id")
)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True, slots=True)
class StoreSummary:
    count: int
    open: int
    min_created: str | None
    max_created: str | None
    max_updated: str | None
    last_sync: str | None


class TicketRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_many(self, tickets: Iterable[Ticket]) -> int:
        now = utc_now()
        rows = []
        for t in tickets:
            if not t.sys_id:
                continue
            values = [getattr(t, f) for f in _TICKET_FIELDS]
            values = [int(v) if isinstance(v, bool) else v for v in values]
            values += [document_forms(t.short_description, t.description, t.close_notes), now]
            rows.append(values)
        if rows:
            with self._db.connect() as conn:
                conn.executemany(_UPSERT_SQL, rows)
        return len(rows)

    def get(self, ticket_id: int) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data.pop("heb_forms", None)
        return data

    def summary(self) -> StoreSummary:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(active), 0), MIN(created_on), MAX(created_on), MAX(updated_on) "
                "FROM tickets").fetchone()
            last_sync = self._get_meta(conn, "last_sync")
        return StoreSummary(r[0], r[1], r[2], r[3], r[4], last_sync)

    def latest_update(self) -> str | None:
        with self._db.connect() as conn:
            return conn.execute("SELECT MAX(updated_on) FROM tickets").fetchone()[0]

    def mark_synced(self) -> None:
        with self._db.connect() as conn:
            conn.execute("INSERT INTO meta(key, value) VALUES ('last_sync', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (utc_now(),))

    @staticmethod
    def _get_meta(conn: Any, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
