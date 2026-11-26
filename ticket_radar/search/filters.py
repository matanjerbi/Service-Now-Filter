"""Search filters and their translation to SQL."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

NO_GROUP = "__none__"   # sentinel for tickets without an assignment group


class Status(StrEnum):
    ANY = ""
    OPEN = "open"
    CLOSED = "closed"


class MatchKind(StrEnum):
    FTS = "fts"          # FTS5 MATCH expression
    LIKE = "like"        # fallback when FTS5 is unavailable
    NUMBER = "number"    # ticket-number prefix


@dataclass(frozen=True, slots=True)
class TextMatch:
    kind: MatchKind
    value: str | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Filters:
    created_from: str | None = None   # UTC "YYYY-MM-DD HH:MM:SS"
    created_to: str | None = None
    groups: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    status: Status = Status.ANY
    exclude_id: int | None = None


@dataclass(slots=True)
class SqlQuery:
    """FROM clause + WHERE clause + bound parameters."""
    from_clause: str = "tickets t"
    conditions: list[str] = field(default_factory=list)
    params: list[object] = field(default_factory=list)

    @property
    def uses_fts(self) -> bool:
        return "tickets_fts" in self.from_clause

    def where(self) -> str:
        return (" WHERE " + " AND ".join(self.conditions)) if self.conditions else ""

    def add(self, condition: str, *params: object) -> None:
        self.conditions.append(condition)
        self.params.extend(params)


_LIKE_COLUMNS = ("t.number", "t.short_description", "t.description", "t.close_notes", "t.cmdb_ci")


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" * len(values))


def build_sql(filters: Filters, match: TextMatch | None = None, ignore: frozenset[str] = frozenset()) -> SqlQuery:
    """Translate filters into SQL. `ignore` lists facet names to leave out (for facet counts)."""
    q = SqlQuery()
    if match is not None:
        match match.kind:
            case MatchKind.FTS:
                q.from_clause = "tickets t JOIN tickets_fts ON tickets_fts.rowid = t.id"
                q.add("tickets_fts MATCH ?", match.value)
            case MatchKind.NUMBER:
                q.add("t.number LIKE ?", match.value)
            case MatchKind.LIKE:
                for word in match.value:
                    q.add("(" + " OR ".join(f"{c} LIKE ?" for c in _LIKE_COLUMNS) + ")",
                          *[f"%{word}%"] * len(_LIKE_COLUMNS))
    if filters.created_from:
        q.add("t.created_on >= ?", filters.created_from)
    if filters.created_to:
        q.add("t.created_on <= ?", filters.created_to)
    if filters.groups and "groups" not in ignore:
        names = tuple(g for g in filters.groups if g != NO_GROUP)
        parts = []
        if names:
            parts.append(f"t.group_name IN ({_placeholders(names)})")
        if NO_GROUP in filters.groups:
            parts.append("COALESCE(t.group_name, '') = ''")
        q.add("(" + " OR ".join(parts) + ")", *names)
    if filters.tables and "tables" not in ignore:
        q.add(f"t.tbl IN ({_placeholders(filters.tables)})", *filters.tables)
    if filters.priorities and "priorities" not in ignore:
        q.add(f"t.priority IN ({_placeholders(filters.priorities)})", *filters.priorities)
    if filters.status is not Status.ANY and "status" not in ignore:
        q.add("t.active = ?", 1 if filters.status is Status.OPEN else 0)
    if filters.exclude_id is not None:
        q.add("t.id != ?", filters.exclude_id)
    return q


def without(filters: Filters, **changes: object) -> Filters:
    return replace(filters, **changes)
