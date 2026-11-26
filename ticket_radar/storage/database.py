"""SQLite database: connection management and schema (with FTS5 full-text index)."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Columns of the full-text index, in order. `heb_forms` holds normalised Hebrew word forms.
FTS_COLUMNS: tuple[str, ...] = (
    "number", "short_description", "description", "close_notes", "journal", "cmdb_ci", "heb_forms",
)
# bm25 weights per FTS column (higher = more important).
FTS_WEIGHTS: tuple[float, ...] = (10.0, 6.0, 1.0, 1.5, 0.7, 3.0, 2.0)

_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY,
    sys_id            TEXT NOT NULL UNIQUE,
    tbl               TEXT NOT NULL,
    sys_class         TEXT,
    number            TEXT,
    short_description TEXT,
    description       TEXT,
    state             TEXT,
    priority          TEXT,
    active            INTEGER NOT NULL DEFAULT 0,
    group_id          TEXT,
    group_name        TEXT,
    assigned_to       TEXT,
    opened_by         TEXT,
    category          TEXT,
    cmdb_ci           TEXT,
    close_notes       TEXT,
    journal           TEXT,
    created_on        TEXT,
    updated_on        TEXT,
    closed_at         TEXT,
    heb_forms         TEXT,
    synced_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_tickets_created ON tickets(created_on);
CREATE INDEX IF NOT EXISTS ix_tickets_updated ON tickets(updated_on);
CREATE INDEX IF NOT EXISTS ix_tickets_group   ON tickets(group_name);
CREATE INDEX IF NOT EXISTS ix_tickets_number  ON tickets(number);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _fts_sql(tokenizer: str) -> str:
    cols = ", ".join(FTS_COLUMNS)
    new = ", ".join(f"new.{c}" for c in FTS_COLUMNS)
    old = ", ".join(f"old.{c}" for c in FTS_COLUMNS)
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(
    {cols}, content='tickets', content_rowid='id', tokenize='{tokenizer}');
CREATE TRIGGER IF NOT EXISTS tickets_ai AFTER INSERT ON tickets BEGIN
    INSERT INTO tickets_fts(rowid, {cols}) VALUES (new.id, {new});
END;
CREATE TRIGGER IF NOT EXISTS tickets_ad AFTER DELETE ON tickets BEGIN
    INSERT INTO tickets_fts(tickets_fts, rowid, {cols}) VALUES ('delete', old.id, {old});
END;
CREATE TRIGGER IF NOT EXISTS tickets_au AFTER UPDATE ON tickets BEGIN
    INSERT INTO tickets_fts(tickets_fts, rowid, {cols}) VALUES ('delete', old.id, {old});
    INSERT INTO tickets_fts(rowid, {cols}) VALUES (new.id, {new});
END;
"""


class Database:
    """Thin wrapper around a SQLite file. Each call to `connect()` opens a fresh connection,
    which keeps it safe to use from the HTTP server's worker threads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.fts_enabled = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create tables (idempotent) and detect full-text search support."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version and version < SCHEMA_VERSION:
                log.warning("סכמת מסד נתונים ישנה (v%s) – בונה מחדש. יש להריץ סנכרון מחדש.", version)
                conn.executescript("DROP TABLE IF EXISTS tickets_fts; DROP TABLE IF EXISTS tickets;")
            conn.executescript(_TABLES_SQL)
            for tokenizer in ("unicode61 remove_diacritics 2", "unicode61"):
                try:
                    conn.executescript(_fts_sql(tokenizer))
                    break
                except sqlite3.OperationalError as exc:
                    log.debug("FTS5 tokenizer %r unavailable: %s", tokenizer, exc)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.fts_enabled = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='tickets_fts'").fetchone() is not None
        if not self.fts_enabled:
            log.warning("SQLite בלי FTS5 – החיפוש יעבוד במצב טקסט פשוט (איטי יותר, בלי דירוג)")
