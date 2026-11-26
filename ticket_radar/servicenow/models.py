"""Ticket model and conversion from Table API records."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Fields requested from every task-based table.
BASE_FIELDS: tuple[str, ...] = (
    "sys_id", "number", "short_description", "description", "state", "priority",
    "assignment_group", "assigned_to", "opened_by", "caller_id", "sys_created_on",
    "sys_updated_on", "closed_at", "active", "category", "cmdb_ci", "close_notes",
    "sys_class_name",
)
JOURNAL_FIELDS: tuple[str, ...] = ("comments_and_work_notes",)

TABLE_LABELS: dict[str, str] = {
    "incident": "תקלה",
    "sc_req_item": "בקשה",
    "sc_task": "משימת בקשה",
    "change_request": "שינוי",
    "change_task": "משימת שינוי",
    "problem": "בעיה",
    "task": "משימה",
}


@dataclass(slots=True)
class Ticket:
    sys_id: str
    table: str
    sys_class: str
    number: str
    short_description: str = ""
    description: str = ""
    state: str = ""
    priority: str = ""
    active: bool = False
    group_id: str = ""
    group_name: str = ""
    assigned_to: str = ""
    opened_by: str = ""
    category: str = ""
    cmdb_ci: str = ""
    close_notes: str = ""
    journal: str = ""
    created_on: str = ""   # UTC "YYYY-MM-DD HH:MM:SS"
    updated_on: str = ""   # UTC
    closed_at: str = ""    # UTC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_api(cls, record: dict[str, Any], table: str) -> Ticket:
        """Convert a record fetched with sysparm_display_value=all.

        Each field is {"value": raw, "display_value": human}. Dates use the raw (UTC) value,
        references and choice lists use the display value.
        """
        def raw(name: str) -> str:
            v = record.get(name)
            if isinstance(v, dict):
                v = v.get("value")
            return "" if v is None else str(v)

        def shown(name: str) -> str:
            v = record.get(name)
            if isinstance(v, dict):
                v = v.get("display_value") or v.get("value")
            return "" if v is None else str(v)

        return cls(
            sys_id=raw("sys_id"),
            table=table,
            sys_class=raw("sys_class_name") or table,
            number=shown("number"),
            short_description=shown("short_description"),
            description=shown("description"),
            state=shown("state"),
            priority=shown("priority"),
            active=raw("active").lower() in ("true", "1"),
            group_id=raw("assignment_group"),
            group_name=shown("assignment_group"),
            assigned_to=shown("assigned_to"),
            opened_by=shown("caller_id") or shown("opened_by"),
            category=shown("category"),
            cmdb_ci=shown("cmdb_ci"),
            close_notes=shown("close_notes"),
            journal=shown("comments_and_work_notes"),
            created_on=raw("sys_created_on"),
            updated_on=raw("sys_updated_on"),
            closed_at=raw("closed_at"),
        )
