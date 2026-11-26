#!/usr/bin/env python3
"""Ticket Radar – local ServiceNow ticket explorer.

Runs entirely on your machine (Python 3.8+, standard library only):
  python app.py                 # start UI using config.json
  python app.py --demo          # start with generated demo data (no ServiceNow needed)
  python app.py --sync --from 2026-01-01 --to 2026-11-30   # headless sync (cron friendly)
  python app.py --sync --incremental                      # only changes since last sync
"""
import argparse
import csv
import io
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db as dbm  # noqa: E402
from sn_client import FIELDS, JOURNAL_FIELDS, ServiceNowClient, SNError, normalize  # noqa: E402

DEFAULT_TABLES = ["incident", "sc_req_item", "change_request", "problem"]
TABLE_LABELS = {"incident": "תקלה", "sc_req_item": "בקשה", "sc_task": "משימת בקשה",
                "change_request": "שינוי", "problem": "בעיה", "task": "משימה"}


# ---------------------------------------------------------------- config
# Every setting can come from an environment variable (or a .env file next to app.py).
# Priority: real environment > .env file > config.json (optional) > defaults.
_LIST = lambda v: [x.strip() for x in v.replace("|", ",").split(",") if x.strip()]  # noqa: E731
_BOOL = lambda v: v.strip().lower() not in ("0", "false", "no", "off", "")  # noqa: E731
ENV_MAP = [
    # env var               config path                  parser
    ("SN_INSTANCE",         ("instance",),               str),
    ("SN_AUTH_TYPE",        ("auth", "type"),            str),
    ("SN_USERNAME",         ("auth", "username"),        str),
    ("SN_PASSWORD",         ("auth", "password"),        str),
    ("SN_API_KEY",          ("auth", "api_key"),         str),
    ("SN_CLIENT_ID",        ("auth", "client_id"),       str),
    ("SN_CLIENT_SECRET",    ("auth", "client_secret"),   str),
    ("SN_TOKEN",            ("auth", "token"),           str),
    ("SN_TABLES",           ("tables",),                 _LIST),
    ("SN_SCOPE",            ("scope", "mode"),           str),
    ("SN_SCOPE_GROUPS",     ("scope", "groups"),         _LIST),
    ("SN_SCOPE_USER",       ("scope", "user"),           str),
    ("SN_INCLUDE_JOURNAL",  ("include_journal",),        _BOOL),
    ("SN_EXTRA_QUERY",      ("extra_query",),            str),
    ("SN_DATE_QUERY_STYLE", ("date_query_style",),       str),
    ("SN_PAGE_SIZE",        ("page_size",),              int),
    ("SN_TIMEOUT",          ("timeout",),                int),
    ("SN_VERIFY_SSL",       ("verify_ssl",),             _BOOL),
    ("SN_CA_BUNDLE",        ("ca_bundle",),              str),
    ("TR_HOST",             ("host",),                   str),
    ("TR_PORT",             ("port",),                   int),
    ("TR_DB_PATH",          ("db_path",),                str),
]


def load_dotenv(path):
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes. Never overrides
    variables that are already set in the real environment."""
    if not os.path.exists(path):
        return []
    loaded = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].rstrip()
            if k and k not in os.environ:
                os.environ[k] = v
                loaded.append(k)
    return loaded


def load_config(path=None):
    cfg = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    for env, keys, parse in ENV_MAP:
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        try:
            val = parse(raw)
        except ValueError:
            raise SystemExit(f"ערך לא תקין במשתנה {env}: {raw!r}")
        node = cfg
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = val
    cfg.setdefault("auth", {}).setdefault("type", "basic")
    cfg.setdefault("tables", DEFAULT_TABLES)
    cfg.setdefault("scope", {}).setdefault("mode", "all")
    cfg.setdefault("host", "127.0.0.1")
    cfg.setdefault("port", 8765)
    cfg.setdefault("db_path", "tickets.db")
    return cfg


def config_summary(cfg):
    """Safe-to-print view of the effective config (secrets masked)."""
    auth = cfg.get("auth", {})
    secrets = [k for k in ("password", "api_key", "client_secret", "token") if auth.get(k)]
    return (f"instance={cfg.get('instance') or '(חסר!)'}  auth={auth.get('type')}"
            f"{' user=' + auth['username'] if auth.get('username') else ''}"
            f"  secrets={','.join(secrets) or '(אין!)'}  scope={cfg['scope'].get('mode')}"
            f"  tables={','.join(cfg['tables'])}")


def sn_date(d, t):
    return f"javascript:gs.dateGenerate('{d}','{t}')"


def date_clause(field, d_from, d_to, style):
    if style == "plain":
        parts = []
        if d_from:
            parts.append(f"{field}>={d_from} 00:00:00")
        if d_to:
            parts.append(f"{field}<={d_to} 23:59:59")
        return "^".join(parts)
    if d_from and d_to:
        return f"{field}BETWEEN{sn_date(d_from, '00:00:00')}@{sn_date(d_to, '23:59:59')}"
    if d_from:
        return f"{field}>={sn_date(d_from, '00:00:00')}"
    if d_to:
        return f"{field}<={sn_date(d_to, '23:59:59')}"
    return ""


def scope_clause(client, cfg, log):
    scope = cfg.get("scope") or {}
    mode = scope.get("mode", "all")
    if mode == "all":
        log("היקף: כל הטיקטים שהמשתמש מורשה לראות")
        return ""
    if mode == "my_groups":
        user = scope.get("user")
        if user:  # e.g. a service account looking up *your* groups
            groups = client.my_groups(user)
        else:
            groups = client.my_groups()
            if not groups and (cfg.get("auth") or {}).get("username"):
                groups = client.my_groups(cfg["auth"]["username"])
        if not groups:
            raise SNError("לא נמצאו קבוצות (sys_user_grmember) עבור "
                          f"{user or 'המשתמש המחובר'}. הגדר SN_SCOPE_USER או SN_SCOPE=all")
        log(f"היקף: הקבוצות של {user or 'המשתמש המחובר'} – " + ", ".join(n for _, n in groups))
        return "assignment_groupIN" + ",".join(i for i, _ in groups)
    if mode == "groups":
        names = scope.get("groups") or []
        groups = client.groups_by_name(names)
        if not groups:
            raise SNError("לא נמצאה אף קבוצה מהרשימה ב-scope.groups")
        missing = set(names) - {n for _, n in groups}
        if missing:
            log("אזהרה – קבוצות שלא נמצאו: " + ", ".join(missing))
        log("היקף: קבוצות נבחרות – " + ", ".join(n for _, n in groups))
        return "assignment_groupIN" + ",".join(i for i, _ in groups)
    raise SNError(f"scope.mode לא מוכר: {mode}")


# ---------------------------------------------------------------- sync
class SyncState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"running": False, "log": [], "fetched": 0, "total": None, "table": None,
                     "error": None, "started": None, "finished": None, "saved": 0}

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)

    def log(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.data["log"] = (self.data["log"] + [f"[{stamp}] {msg}"])[-200:]
        print(f"[sync] {msg}", flush=True)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))


def run_sync(cfg, db_path, params, state):
    """params: mode=full|incremental, field=created|updated, from, to, tables"""
    state.update(running=True, error=None, fetched=0, total=None, saved=0, log=[],
                 started=datetime.now().isoformat(timespec="seconds"), finished=None)
    try:
        client = ServiceNowClient(cfg)
        style = cfg.get("date_query_style", "script")
        scope_q = scope_clause(client, cfg, state.log)
        conn = dbm.connect(db_path)
        if params.get("mode") == "incremental":
            last = dbm.summary(conn)["max_updated"]
            if not last:
                raise SNError("אין עדיין נתונים במאגר – הרץ סנכרון לפי טווח תאריכים קודם")
            since = (datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_q = date_clause("sys_updated_on", since, None, style)
            state.log(f"עדכון מהיר: טיקטים שעודכנו מאז {since}")
        else:
            field = "sys_updated_on" if params.get("field") == "updated" else "sys_created_on"
            date_q = date_clause(field, params.get("from"), params.get("to"), style)
            state.log(f"טווח: {params.get('from') or '…'} עד {params.get('to') or '…'} "
                      f"({'עודכנו' if field == 'sys_updated_on' else 'נפתחו'})")
        fields = FIELDS + (JOURNAL_FIELDS if cfg.get("include_journal") else [])
        tables = params.get("tables") or cfg["tables"]
        grand = 0
        for table in tables:
            q = "^".join(x for x in [date_q, scope_q, cfg.get("extra_query", "")] if x)
            q += "^ORDERBYsys_created_on^ORDERBYsys_id"
            state.update(table=table, fetched=0, total=None)
            state.log(f"מושך מטבלה {table} …")
            batch, n = [], 0
            try:
                for rec, total in client.iter_records(table, q, fields):
                    batch.append(normalize(rec, table))
                    n += 1
                    if len(batch) >= 250:
                        dbm.upsert(conn, batch)
                        batch = []
                    if n % 50 == 0:
                        state.update(fetched=n, total=total)
                if batch:
                    dbm.upsert(conn, batch)
            except SNError as e:
                if "403" in str(e):
                    state.log(f"דילוג על {table}: אין הרשאה")
                    continue
                raise
            grand += n
            state.update(fetched=n, saved=grand)
            state.log(f"{table}: נשמרו {n:,} טיקטים")
        dbm.set_meta(conn, "last_sync", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        conn.close()
        state.log(f"הסתיים ✓ סה\"כ {grand:,} טיקטים נשמרו/עודכנו")
    except Exception as e:  # noqa: BLE001
        state.update(error=str(e))
        state.log("שגיאה: " + str(e))
        if not isinstance(e, SNError):
            traceback.print_exc()
    finally:
        state.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))


# ---------------------------------------------------------------- http
def to_utc_param(s, end=False):
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (already UTC from the UI)."""
    if not s:
        return None
    s = s.strip().replace("T", " ")
    if len(s) == 10:
        s += " 23:59:59" if end else " 00:00:00"
    return s[:19]


class App:
    def __init__(self, cfg, db_path, demo=False):
        self.cfg = cfg
        self.db_path = db_path
        self.demo = demo
        self.fts = dbm.init(db_path)
        self.sync = SyncState()


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = "TicketRadar/1.0"

        def log_message(self, fmt, *args):  # quieter console
            if "/api/sync/status" not in (args[0] if args else ""):
                sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

        # ---- helpers
        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def body_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def filters(self, qs):
            g = lambda k: (qs.get(k) or [""])[0]  # noqa: E731
            lst = lambda k: [x for x in g(k).split("|") if x]  # noqa: E731
            return ({"from": to_utc_param(g("from")), "to": to_utc_param(g("to"), end=True),
                     "groups": lst("groups"), "tables": lst("tables"), "priorities": lst("priorities"),
                     "status": g("status")},
                    g("q"), g("mode") or "smart", g("sort") or "relevance")

        # ---- routes
        def do_GET(self):
            try:
                u = urlparse(self.path)
                qs = parse_qs(u.query)
                if u.path in ("/", "/index.html"):
                    with open(os.path.join(HERE, "static", "index.html"), "rb") as fh:
                        body = fh.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/status":
                    conn = dbm.connect(app.db_path)
                    s = dbm.summary(conn)
                    conn.close()
                    cfg = app.cfg or {}
                    self.send_json({
                        "demo": app.demo, "configured": bool(cfg.get("instance")),
                        "instance": cfg.get("instance", ""), "tables": cfg.get("tables", DEFAULT_TABLES),
                        "table_labels": TABLE_LABELS, "scope": cfg.get("scope", {}),
                        "fts": app.fts, "db": s, "sync": app.sync.snapshot()})
                elif u.path == "/api/sync/status":
                    self.send_json(app.sync.snapshot())
                elif u.path == "/api/search":
                    f, q, mode, sort = self.filters(qs)
                    limit = min(int((qs.get("limit") or ["50"])[0]), 500)
                    offset = int((qs.get("offset") or ["0"])[0])
                    conn = dbm.connect(app.db_path)
                    t0 = time.time()
                    res = dbm.search(conn, f, q, mode, sort, limit, offset, app.fts)
                    res["ms"] = int((time.time() - t0) * 1000)
                    conn.close()
                    self.send_json(res)
                elif u.path == "/api/ticket":
                    conn = dbm.connect(app.db_path)
                    t = dbm.ticket(conn, int(qs["id"][0]))
                    conn.close()
                    self.send_json(t or {}, 200 if t else 404)
                elif u.path == "/api/similar":
                    conn = dbm.connect(app.db_path)
                    res = dbm.similar(conn, int(qs["id"][0]), app.fts)
                    conn.close()
                    self.send_json(res)
                elif u.path == "/api/export.csv":
                    f, q, mode, sort = self.filters(qs)
                    conn = dbm.connect(app.db_path)
                    buf = io.StringIO()
                    cols = ["number", "tbl", "short_description", "state", "priority", "group_name",
                            "assigned_to", "opened_by", "created_on", "updated_on", "closed_at",
                            "cmdb_ci", "category", "description", "close_notes"]
                    heb = ["מספר", "סוג", "תיאור קצר", "סטטוס", "עדיפות", "צוות", "מטפל", "פותח",
                           "נפתח (UTC)", "עודכן (UTC)", "נסגר (UTC)", "רכיב (CI)", "קטגוריה",
                           "תיאור", "הערות סגירה"]
                    w = csv.writer(buf)
                    w.writerow(heb)
                    for r in dbm.export_rows(conn, f, q, mode, sort, app.fts):
                        w.writerow([(r[c] or "") for c in cols])
                    conn.close()
                    body = ("\ufeff" + buf.getvalue()).encode("utf-8")
                    name = "tickets_" + datetime.now().strftime("%Y%m%d_%H%M") + ".csv"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_json({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.send_json({"error": str(e)}, 500)

        def do_POST(self):
            try:
                u = urlparse(self.path)
                origin = self.headers.get("Origin")
                if origin and origin.split("://", 1)[-1] != (self.headers.get("Host") or ""):
                    return self.send_json({"error": "forbidden origin"}, 403)
                if u.path in ("/api/sync", "/api/test") and (app.demo or not app.cfg):
                    return self.send_json({"error": "במצב דמו אין חיבור ל-ServiceNow. "
                                                    "הגדר SN_INSTANCE (.env) והפעל בלי --demo."}, 400)
                if u.path == "/api/test":
                    client = ServiceNowClient(app.cfg)
                    out = []
                    for t in app.cfg["tables"]:
                        try:
                            out.append(client.test(t))
                        except SNError as e:
                            out.append({"ok": False, "table": t, "error": str(e)})
                    self.send_json({"results": out})
                elif u.path == "/api/sync":
                    if app.sync.snapshot()["running"]:
                        return self.send_json({"error": "סנכרון כבר רץ"}, 409)
                    p = self.body_json()
                    threading.Thread(target=run_sync, args=(app.cfg, app.db_path, p, app.sync),
                                     daemon=True).start()
                    self.send_json({"started": True})
                else:
                    self.send_json({"error": "not found"}, 404)
            except SNError as e:
                self.send_json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.send_json({"error": str(e)}, 500)

    return H


def main():
    ap = argparse.ArgumentParser(description="Ticket Radar – ServiceNow ticket explorer. "
                                 "All settings come from environment variables / .env (see .env.example).")
    ap.add_argument("--env-file", default=os.path.join(HERE, ".env"), help="path to .env file")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"), help="optional JSON config")
    ap.add_argument("--demo", action="store_true", help="run with generated demo data")
    ap.add_argument("--host", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--show-config", action="store_true", help="print effective config (secrets masked)")
    ap.add_argument("--sync", action="store_true", help="run a sync from the command line and exit")
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--by-updated", action="store_true", help="date range applies to sys_updated_on")
    args = ap.parse_args()

    loaded = load_dotenv(args.env_file)
    cfg = load_config(args.config)
    if loaded:
        print(f"נטענו {len(loaded)} משתנים מ-{os.path.basename(args.env_file)}")
    if args.show_config:
        print(config_summary(cfg))
        sys.exit(0)

    if args.demo:
        db_path = os.path.join(HERE, "demo.db")
        if not os.path.exists(db_path):
            import demo
            dbm.init(db_path)
            demo.generate(db_path)
    else:
        if not cfg.get("instance"):
            print("לא הוגדר SN_INSTANCE.\n"
                  "העתק את .env.example ל-.env ומלא את הפרטים (או הגדר משתני סביבה),\n"
                  "או הרץ עם --demo כדי לראות את הממשק עם נתוני דוגמה.")
            sys.exit(1)
        db_path = cfg["db_path"]
        if not os.path.isabs(db_path):
            db_path = os.path.join(HERE, db_path)
        print(config_summary(cfg))

    app = App(None if args.demo else cfg, db_path, demo=args.demo)

    if args.sync:
        if args.demo:
            sys.exit("--sync לא זמין במצב דמו")
        p = {"mode": "incremental" if args.incremental else "full", "from": args.d_from,
             "to": args.d_to, "field": "updated" if args.by_updated else "created"}
        run_sync(cfg, db_path, p, app.sync)
        sys.exit(1 if app.sync.snapshot()["error"] else 0)

    host = args.host or cfg.get("host", "127.0.0.1")
    port = args.port or cfg.get("port", 8765)
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, p), make_handler(app))
            port = p
            break
        except OSError:
            continue
    if not server:
        sys.exit("לא נמצא פורט פנוי")
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"
    print(f"Ticket Radar פועל: {url}   ({'דמו' if args.demo else cfg.get('instance')})")
    if host not in ("127.0.0.1", "localhost"):
        print(f"שים לב: השרת מאזין על {host} – נגיש ממחשבים אחרים ברשת, בלי סיסמה.")
    print("לעצירה: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nנעצר.")


if __name__ == "__main__":
    main()
