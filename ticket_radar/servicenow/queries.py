"""Builders for ServiceNow encoded queries."""
from __future__ import annotations

from datetime import date

from ..config import DateQueryStyle


def _gen(day: date, time_of_day: str) -> str:
    return f"javascript:gs.dateGenerate('{day.isoformat()}','{time_of_day}')"


def date_range(field: str, start: date | None, end: date | None,
               style: DateQueryStyle = DateQueryStyle.SCRIPT) -> str:
    """Inclusive date range on `field` (whole days)."""
    if style is DateQueryStyle.PLAIN:
        parts = []
        if start:
            parts.append(f"{field}>={start.isoformat()} 00:00:00")
        if end:
            parts.append(f"{field}<={end.isoformat()} 23:59:59")
        return "^".join(parts)
    if start and end:
        return f"{field}BETWEEN{_gen(start, '00:00:00')}@{_gen(end, '23:59:59')}"
    if start:
        return f"{field}>={_gen(start, '00:00:00')}"
    if end:
        return f"{field}<={_gen(end, '23:59:59')}"
    return ""


def in_list(field: str, values: list[str] | tuple[str, ...]) -> str:
    return f"{field}IN{','.join(values)}"


def join(*clauses: str) -> str:
    """AND-join non-empty clauses."""
    return "^".join(c for c in clauses if c)


def ordered(query: str, *fields: str) -> str:
    """Append a stable ORDER BY (needed for offset pagination)."""
    return join(query, *(f"ORDERBY{f}" for f in fields))
