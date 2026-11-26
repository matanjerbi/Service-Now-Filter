"""Search engine: ranked full-text search, facets, similar tickets and export."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from ..storage.database import FTS_COLUMNS, FTS_WEIGHTS, Database
from . import query_parser
from .filters import NO_GROUP, Filters, MatchKind, SqlQuery, TextMatch, build_sql

HL_START, HL_END = "\x01", "\x02"      # highlight markers; the UI turns them into <mark>
SMART_MIN_RESULTS = 3                  # below this, "smart" mode falls back to similarity ranking
SIMILAR_MAX_WORDS = 14

_STOP_WORDS = frozenset((
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be", "with", "at",
    "by", "from", "this", "that", "it", "not", "no",
    "של", "את", "על", "עם", "אין", "לא", "זה", "זו", "גם", "כי", "או", "אם", "יש", "כל", "עוד", "רק",
))
_BM25 = f"bm25(tickets_fts, {', '.join(str(w) for w in FTS_WEIGHTS)})"
_SNIPPET_COLUMNS = tuple(FTS_COLUMNS.index(c) for c in ("description", "close_notes", "journal"))
_TITLE_COLUMN = FTS_COLUMNS.index("short_description")

RESULT_FIELDS = ("id", "sys_id", "tbl", "sys_class", "number", "short_description", "state", "priority",
                 "active", "group_name", "assigned_to", "created_on", "updated_on", "closed_at", "cmdb_ci")


class MatchMode(StrEnum):
    SMART = "smart"    # all words; fall back to similarity if too few hits
    ALL = "all"        # all words must appear
    ANY = "any"        # any word, ranked by relevance


class MatchUsed(StrEnum):
    ALL = "all"
    ANY = "any"
    SIMILAR = "similar"    # smart mode fell back to ANY
    NUMBER = "number"
    LIKE = "like"


class Sort(StrEnum):
    RELEVANCE = "relevance"
    NEWEST = "newest"
    OLDEST = "oldest"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    text: str = ""
    mode: MatchMode = MatchMode.SMART
    filters: Filters = field(default_factory=Filters)
    sort: Sort = Sort.RELEVANCE
    limit: int = 50
    offset: int = 0
    with_facets: bool = True


class SearchEngine:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ public
    def search(self, req: SearchRequest) -> dict[str, Any]:
        with self._db.connect() as conn:
            match, used = self._resolve_match(conn, req)
            q = build_sql(req.filters, match)
            total = conn.execute(f"SELECT COUNT(*) FROM {q.from_clause}{q.where()}", q.params).fetchone()[0]
            rows = conn.execute(
                f"SELECT {self._select(q)} FROM {q.from_clause}{q.where()} "
                f"ORDER BY {self._order(q, req.sort)} LIMIT ? OFFSET ?",
                [*q.params, req.limit, req.offset]).fetchall()
            result: dict[str, Any] = {
                "total": total,
                "mode_used": used.value if used else None,
                "results": [self._row_to_result(r, q.uses_fts) for r in rows],
            }
            if req.with_facets:
                result["facets"] = self._facets(conn, req.filters, match)
        return result

    def similar(self, ticket_id: int, limit: int = 20) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute("SELECT number, short_description, cmdb_ci FROM tickets WHERE id = ?",
                               (ticket_id,)).fetchone()
        if row is None:
            return {"results": [], "total": 0}
        words: list[str] = []
        seen: set[str] = set()
        for w in re.findall(r"[\w\-.]{3,}", f"{row['short_description'] or ''} {row['cmdb_ci'] or ''}"):
            key = w.lower()
            if key in _STOP_WORDS or key in seen or (key.isdigit() and len(key) < 4):
                continue
            seen.add(key)
            words.append(w)
        if not words:
            return {"results": [], "total": 0, "source": row["number"]}
        text = " ".join(words[:SIMILAR_MAX_WORDS])
        res = self.search(SearchRequest(text=text, mode=MatchMode.ANY, filters=Filters(exclude_id=ticket_id),
                                        limit=limit, with_facets=False))
        return res | {"source": row["number"], "query": text}

    def export(self, req: SearchRequest, max_rows: int = 200_000) -> Iterator[sqlite3.Row]:
        with self._db.connect() as conn:
            match, _ = self._resolve_match(conn, req)
            q = build_sql(req.filters, match)
            select = "t.*" + (f", {_BM25} AS score" if q.uses_fts else "")
            yield from conn.execute(
                f"SELECT {select} FROM {q.from_clause}{q.where()} ORDER BY {self._order(q, req.sort)} LIMIT ?",
                [*q.params, max_rows])

    # ------------------------------------------------------------------ matching
    def _resolve_match(self, conn: sqlite3.Connection, req: SearchRequest) -> tuple[TextMatch | None, MatchUsed | None]:
        text = req.text.strip()
        if not text:
            return None, None
        if query_parser.is_ticket_number(text):
            return TextMatch(MatchKind.NUMBER, text.upper() + "%"), MatchUsed.NUMBER
        parsed = query_parser.parse(text)
        if parsed.empty:
            return None, None
        if not self._db.fts_enabled:
            return TextMatch(MatchKind.LIKE, parsed.words), MatchUsed.LIKE
        if req.mode is MatchMode.ANY:
            return TextMatch(MatchKind.FTS, parsed.match_any()), MatchUsed.ANY
        all_match = TextMatch(MatchKind.FTS, parsed.match_all())
        too_few = (req.mode is MatchMode.SMART and not parsed.single_term
                   and self._count(conn, req.filters, all_match) < SMART_MIN_RESULTS)
        if too_few:
            return TextMatch(MatchKind.FTS, parsed.match_any()), MatchUsed.SIMILAR
        return all_match, MatchUsed.ALL

    @staticmethod
    def _count(conn: sqlite3.Connection, filters: Filters, match: TextMatch | None) -> int:
        q = build_sql(filters, match)
        return conn.execute(f"SELECT COUNT(*) FROM {q.from_clause}{q.where()}", q.params).fetchone()[0]

    # ------------------------------------------------------------------ SQL pieces
    @staticmethod
    def _select(q: SqlQuery) -> str:
        if not q.uses_fts:
            return "t.*"
        marks = f"char({ord(HL_START)}), char({ord(HL_END)})"
        snippets = ", ".join(f"snippet(tickets_fts, {c}, {marks}, '…', 18) AS snip{i}"
                             for i, c in enumerate(_SNIPPET_COLUMNS))
        return f"t.*, highlight(tickets_fts, {_TITLE_COLUMN}, {marks}) AS title_hl, {snippets}, {_BM25} AS score"

    @staticmethod
    def _order(q: SqlQuery, sort: Sort) -> str:
        match sort:
            case Sort.NEWEST:
                return "t.created_on DESC"
            case Sort.OLDEST:
                return "t.created_on ASC"
            case Sort.UPDATED:
                return "t.updated_on DESC"
        return "score ASC, t.created_on DESC" if q.uses_fts else "t.created_on DESC"

    @staticmethod
    def _row_to_result(row: sqlite3.Row, has_fts: bool) -> dict[str, Any]:
        out = {k: row[k] for k in RESULT_FIELDS}
        if has_fts:
            out["title_hl"] = row["title_hl"]
            snippets = [row[f"snip{i}"] for i in range(len(_SNIPPET_COLUMNS))]
            out["snippet"] = next((s for s in snippets if s and HL_START in s), snippets[0] or "")
        else:
            out["snippet"] = (row["description"] or row["close_notes"] or "")[:240]
        return out

    # ------------------------------------------------------------------ facets
    def _facets(self, conn: sqlite3.Connection, filters: Filters, match: TextMatch | None) -> dict[str, Any]:
        def run(sql_tail: str, ignore: str | None = None) -> list[sqlite3.Row]:
            q = build_sql(filters, match, frozenset({ignore}) if ignore else frozenset())
            return conn.execute(sql_tail.format(src=f"{q.from_clause}{q.where()}"), q.params).fetchall()

        groups = [{"name": r[0] or NO_GROUP, "count": r[1], "open": r[2] or 0} for r in run(
            "SELECT t.group_name, COUNT(*), SUM(t.active) FROM {src} GROUP BY t.group_name ORDER BY 2 DESC",
            "groups")]
        tables = [{"name": r[0], "count": r[1]} for r in run(
            "SELECT t.tbl, COUNT(*) FROM {src} GROUP BY t.tbl ORDER BY 2 DESC", "tables")]
        priorities = [{"name": r[0], "count": r[1]} for r in run(
            "SELECT t.priority, COUNT(*) FROM {src} GROUP BY t.priority ORDER BY t.priority", "priorities") if r[0]]
        all_, open_ = run("SELECT COUNT(*), COALESCE(SUM(t.active), 0) FROM {src}", "status")[0]
        return {
            "groups": groups,
            "tables": tables,
            "priorities": priorities,
            "status": {"all": all_, "open": open_, "closed": all_ - open_},
            "timeline": self._timeline(run),
        }

    @staticmethod
    def _timeline(run: Any) -> dict[str, Any]:
        first, last = run("SELECT MIN(t.created_on), MAX(t.created_on) FROM {src}")[0]
        unit, key = "day", "substr(t.created_on, 1, 10)"
        if first and last:
            span = (date.fromisoformat(last[:10]) - date.fromisoformat(first[:10])).days
            if span > 150:
                unit, key = "month", "substr(t.created_on, 1, 7)"
            elif span > 45:
                unit, key = "week", "strftime('%Y-%W', t.created_on)"
        points = [[r[0], r[1], r[2] or 0] for r in run(
            f"SELECT {key} AS k, COUNT(*), SUM(t.active) FROM {{src}} GROUP BY k ORDER BY k") if r[0]]
        return {"unit": unit, "points": points}
