"""Application wiring: builds the object graph from Settings."""
from __future__ import annotations

from pathlib import Path

from .config import Settings
from .search.engine import SearchEngine
from .storage import Database, TicketRepository
from .sync import SyncService
from .web import AppContext

DEMO_DB_NAME = "demo.db"


def build_context(settings: Settings, *, demo: bool = False, data_dir: Path | None = None) -> AppContext:
    db_path = (data_dir or settings.server.db_path.parent) / DEMO_DB_NAME if demo else settings.server.db_path
    db = Database(db_path)
    fresh = not db_path.exists()
    db.initialize()
    repo = TicketRepository(db)
    if demo and fresh:
        from . import demo as demo_data
        demo_data.generate(repo)
    return AppContext(
        settings=settings,
        db=db,
        repo=repo,
        search=SearchEngine(db),
        sync=SyncService(settings.servicenow, repo),
        demo=demo,
    )
