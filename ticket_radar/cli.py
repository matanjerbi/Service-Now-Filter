"""Command-line interface.

    python run.py                          start the UI (default command)
    python run.py serve --demo             start with generated demo data
    python run.py sync --from 2026-01-01 --to 2026-11-30
    python run.py sync --incremental       only tickets changed since the last sync
    python run.py check                    test the connection and permissions
    python run.py config                   show the effective settings (secrets masked)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from . import __version__
from .app import build_context
from .config import ConfigError, Settings, load_dotenv, load_settings
from .sync import DateField, SyncMode, SyncRequest
from .web import build_router, create_server

PROJECT_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger("ticket_radar")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"תאריך לא תקין: {value} (פורמט: YYYY-MM-DD)") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ticket-radar", description="חיפוש וניתוח טיקטים מ-ServiceNow")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env-file", type=Path, default=PROJECT_DIR / ".env",
                        help="קובץ .env (ברירת מחדל: ליד run.py)")
    parser.add_argument("-v", "--verbose", action="store_true", help="לוג מפורט")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="הפעלת הממשק (ברירת מחדל)")
    serve.add_argument("--demo", action="store_true", help="נתוני דמו, בלי ServiceNow")
    serve.add_argument("--host", help="כתובת האזנה (ברירת מחדל TR_HOST או 127.0.0.1)")
    serve.add_argument("--port", type=int, help="פורט (ברירת מחדל TR_PORT או 8765)")
    serve.add_argument("--no-browser", action="store_true", help="לא לפתוח דפדפן")

    sync = sub.add_parser("sync", help="משיכת טיקטים מהשורה (מתאים ל-cron)")
    mode = sync.add_mutually_exclusive_group(required=True)
    mode.add_argument("--incremental", action="store_true", help="רק טיקטים שהשתנו מאז הפעם הקודמת")
    mode.add_argument("--from", dest="date_from", type=_date, help="מתאריך (YYYY-MM-DD)")
    sync.add_argument("--to", dest="date_to", type=_date, help="עד תאריך (YYYY-MM-DD)")
    sync.add_argument("--by-updated", action="store_true", help="הטווח חל על תאריך עדכון ולא פתיחה")
    sync.add_argument("--tables", help="טבלאות מופרדות בפסיק (ברירת מחדל SN_TABLES)")

    sub.add_parser("check", help="בדיקת חיבור והרשאות מול ServiceNow")
    sub.add_parser("config", help="הצגת ההגדרות בתוקף (סודות מוסתרים)")
    return parser


def _require_instance(settings: Settings) -> None:
    if not settings.servicenow.configured:
        sys.exit("לא הוגדר SN_INSTANCE.\n"
                 "העתק את .env.example ל-.env ומלא פרטים (או הגדר משתני סביבה),\n"
                 "או הרץ:  python run.py serve --demo")


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    demo = getattr(args, "demo", False)
    if not demo:
        _require_instance(settings)
    ctx = build_context(settings, demo=demo, data_dir=PROJECT_DIR)
    host = getattr(args, "host", None) or settings.server.host
    port = getattr(args, "port", None) or settings.server.port
    server = create_server(build_router(ctx), host, port)
    actual_port = server.server_address[1]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{actual_port}/"
    print(f"Ticket Radar {__version__} פועל: {url}  ({'דמו' if demo else settings.servicenow.base_url})")
    if host not in ("127.0.0.1", "localhost"):
        print(f"שים לב: מאזין על {host} – הממשק נגיש ממחשבים אחרים ברשת, בלי סיסמה.")
    print("לעצירה: Ctrl+C")
    if not getattr(args, "no_browser", False):
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nנעצר.")
    finally:
        server.server_close()
    return 0


def cmd_sync(args: argparse.Namespace, settings: Settings) -> int:
    _require_instance(settings)
    ctx = build_context(settings)
    request = SyncRequest(
        mode=SyncMode.INCREMENTAL if args.incremental else SyncMode.FULL,
        date_field=DateField.UPDATED if args.by_updated else DateField.CREATED,
        date_from=args.date_from,
        date_to=args.date_to,
        tables=tuple(t.strip() for t in (args.tables or "").split(",") if t.strip()),
    )
    return 0 if ctx.sync.run(request) else 1


def cmd_check(_: argparse.Namespace, settings: Settings) -> int:
    _require_instance(settings)
    ctx = build_context(settings)
    ok = True
    for result in ctx.sync.check_connection():
        if result["ok"]:
            visible = result["visible_records"]
            print(f"✓ {result['table']}" + (f"  ({visible:,} רשומות גלויות)" if visible is not None else ""))
        else:
            ok = False
            print(f"✗ {result['table']}: {result['error']}")
    return 0 if ok else 1


def cmd_config(_: argparse.Namespace, settings: Settings) -> int:
    print(settings.describe())
    return 0


COMMANDS = {"serve": cmd_serve, "sync": cmd_sync, "check": cmd_check, "config": cmd_config}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    loaded = load_dotenv(args.env_file)
    if loaded:
        log.info("נטענו %d משתנים מ-%s", len(loaded), args.env_file.name)
    try:
        settings = load_settings(base_dir=PROJECT_DIR)
    except ConfigError as exc:
        print(f"שגיאת הגדרות: {exc}", file=sys.stderr)
        return 2
    return COMMANDS[args.command or "serve"](args, settings)
