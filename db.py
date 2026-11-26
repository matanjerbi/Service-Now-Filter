"""Local SQLite store with full-text search (FTS5) and 'smart' matching."""
import re
import sqlite3
from datetime import datetime, timezone

COLS = ["sys_id", "tbl", "sys_class", "number", "short_description", "description",
        "state", "priority", "active", "group_id", "group_name", "assigned_to",
        "opened_by", "category", "cmdb_ci", "close_notes", "journal",
        "created_on", "updated_on", "closed_at"]
FTS_COLS = ["number", "short_description", "description", "close_notes", "journal", "cmdb_ci", "heb"]
WEIGHTS = "10.0, 6.0, 1.0, 1.5, 0.7, 3.0, 2.0"
SMART_MIN = 3          # below this many AND-hits, fall back to "similar" (OR) ranking
NO_GROUP = "__none__"
HEB_PREFIX = set("והבלמשכ")
STOP = set("the a an and or of to in on for is are was be with at by from this that it not no "
           "של את על עם אין לא זה זו גם כי או אם יש כל עוד רק".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY,
  sys_id TEXT UNIQUE NOT NULL,
  tbl TEXT, sys_class TEXT, number TEXT, short_description TEXT, description TEXT,
  state TEXT, priority TEXT, active INTEGER,
  group_id TEXT, group_name TEXT, assigned_to TEXT, opened_by TEXT,
  category TEXT, cmdb_ci TEXT, close_notes TEXT, journal TEXT,
  created_on TEXT, updated_on TEXT, closed_at TEXT, synced_at TEXT, heb TEXT);
CREATE INDEX IF NOT EXISTS ix_created ON tickets(created_on);
CREATE INDEX IF NOT EXISTS ix_updated ON tickets(updated_on);
CREATE INDEX IF NOT EXISTS ix_group ON tickets(group_name);
CREATE INDEX IF NOT EXISTS ix_number ON tickets(number);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def _fts_sql(tokenizer):
    c = ", ".join(FTS_COLS)
    new = ", ".join("new." + x for x in FTS_COLS)
    old = ", ".join("old." + x for x in FTS_COLS)
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5({c},
  content='tickets', content_rowid='id', tokenize='{tokenizer}');
CREATE TRIGGER IF NOT EXISTS t_ai AFTER INSERT ON tickets BEGIN
  INSERT INTO tickets_fts(rowid, {c}) VALUES (new.id, {new}); END;
CREATE TRIGGER IF NOT EXISTS t_ad AFTER DELETE ON tickets BEGIN
  INSERT INTO tickets_fts(tickets_fts, rowid, {c}) VALUES ('delete', old.id, {old}); END;
CREATE TRIGGER IF NOT EXISTS t_au AFTER UPDATE ON tickets BEGIN
  INSERT INTO tickets_fts(tickets_fts, rowid, {c}) VALUES ('delete', old.id, {old});
  INSERT INTO tickets_fts(rowid, {c}) VALUES (new.id, {new}); END;
"""


def connect(path):
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init(path):
    """Create schema; returns True if full-text search (FTS5) is available."""
    c = connect(path)
    c.executescript(SCHEMA)
    for tok in ("unicode61 remove_diacritics 2", "unicode61"):
        try:
            c.executescript(_fts_sql(tok))
            break
        except sqlite3.OperationalError:
            continue
    c.commit()
    ok = has_fts(c)
    c.close()
    return ok


def has_fts(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='tickets_fts'").fetchone() is not None


HEB_WORD = re.compile(r"[\u05d0-\u05ea]{4,}")


def heb_norm(*texts):
    """Extra searchable forms for Hebrew words: strip attached prefixes (ו,ה,ב,ל,מ,ש,כ)
    and common suffixes, so a search for "שרת" finds "לשרתים"."""
    out = set()
    for w in HEB_WORD.findall(" ".join(t or "" for t in texts)):
        bases = [w]
        if w[0] in HEB_PREFIX:
            bases.append(w[1:])
            if len(w) >= 5 and w[1] in HEB_PREFIX:
                bases.append(w[2:])
        for b in bases:
            if len(b) >= 3:
                out.add(b)
            for suf in ("ים", "ות", "ה", "ת", "י"):
                if b.endswith(suf) and len(b) - len(suf) >= 3:
                    out.add(b[: -len(suf)])
    return " ".join(sorted(out))


def upsert(conn, rows):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        r["heb"] = heb_norm(r.get("short_description"), r.get("description"), r.get("close_notes"))
    cols = COLS + ["heb", "synced_at"]
    sql = (f"INSERT INTO tickets({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
           f"ON CONFLICT(sys_id) DO UPDATE SET " +
           ",".join(f"{c}=excluded.{c}" for c in cols if c != "sys_id"))
    conn.executemany(sql, [[r.get(c, "") for c in COLS] + [r["heb"], now] for r in rows if r.get("sys_id")])
    conn.commit()


def set_meta(conn, k, v):
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    conn.commit()


def get_meta(conn, k, default=None):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def summary(conn):
    r = conn.execute("SELECT COUNT(*), SUM(active), MIN(created_on), MAX(created_on), MAX(updated_on) "
                     "FROM tickets").fetchone()
    return {"count": r[0] or 0, "open": r[1] or 0, "min_created": r[2], "max_created": r[3],
            "max_updated": r[4], "last_sync": get_meta(conn, "last_sync")}


# ---------------- query building ----------------
TOKEN_RE = re.compile(r'-?"[^"]*"|\S+')
NUMBER_RE = re.compile(r"[A-Za-z]{2,8}\d{3,}")


def _is_hebrew(s):
    return any("֐" <= ch <= "׿" for ch in s)


def parse_terms(q):
    """Split a user query into (positive_terms, negative_terms) as FTS5 expressions."""
    pos, neg = [], []
    for raw in TOKEN_RE.findall(q or ""):
        is_neg = raw.startswith("-") and len(raw) > 1
        t = raw[1:] if is_neg else raw
        if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
            inner = t[1:-1].replace('"', " ").strip()
            if not re.search(r"\w", inner):
                continue
            term = f'"{inner}"'
        else:
            inner = t.replace('"', " ").strip()
            if not re.search(r"\w", inner):
                continue
            term = f'"{inner}"*' if len(inner) >= 2 else f'"{inner}"'
            # Light Hebrew normalisation: "השרתים" should match "שרת", "הרשאה" -> "הרשאות"
            if _is_hebrew(inner) and " " not in inner and len(inner) > 3:
                variants = {inner}
                bases = [inner] + ([inner[1:]] if inner[0] in HEB_PREFIX and len(inner) >= 5 else [])
                for b in bases:
                    variants.add(b)
                    for suf in ("ים", "ות", "ה", "ת", "י"):
                        if b.endswith(suf) and len(b) - len(suf) >= 3:
                            variants.add(b[: -len(suf)])
                if len(variants) > 1:
                    term = "(" + " OR ".join(f'"{v}"*' for v in sorted(variants, key=len, reverse=True)) + ")"
        (neg if is_neg else pos).append((term, inner))
    return pos, neg


def fts_queries(q):
    pos, neg = parse_terms(q)
    if not pos:
        return None, None
    tail = "".join(f" NOT {t}" for t, _ in neg)
    and_q = "(" + " AND ".join(t for t, _ in pos) + ")" + tail
    or_q = "(" + " OR ".join(t for t, _ in pos) + ")" + tail
    return and_q, or_q


def _like_tokens(q):
    return [w for _, w in parse_terms(q)[0]]


def build(f, exclude=()):
    """Return (from_clause, where_sql, args) for filter dict f."""
    w, a = [], []
    frm = "tickets t"
    if f.get("match"):
        frm = "tickets t JOIN tickets_fts ON tickets_fts.rowid = t.id"
        w.append("tickets_fts MATCH ?")
        a.append(f["match"])
    if f.get("like"):
        for tok in f["like"]:
            w.append("(t.number LIKE ? OR t.short_description LIKE ? OR t.description LIKE ? "
                     "OR t.close_notes LIKE ? OR t.cmdb_ci LIKE ?)")
            a += [f"%{tok}%"] * 5
    if f.get("number"):
        w.append("t.number LIKE ?")
        a.append(f["number"])
    if f.get("from"):
        w.append("t.created_on >= ?")
        a.append(f["from"])
    if f.get("to"):
        w.append("t.created_on <= ?")
        a.append(f["to"])
    if f.get("groups") and "groups" not in exclude:
        names = [g for g in f["groups"] if g != NO_GROUP]
        parts = []
        if names:
            parts.append(f"t.group_name IN ({','.join('?' * len(names))})")
            a += names
        if NO_GROUP in f["groups"]:
            parts.append("(t.group_name IS NULL OR t.group_name = '')")
        w.append("(" + " OR ".join(parts) + ")")
    if f.get("tables") and "tables" not in exclude:
        w.append(f"t.tbl IN ({','.join('?' * len(f['tables']))})")
        a += f["tables"]
    if f.get("priorities") and "priorities" not in exclude:
        w.append(f"t.priority IN ({','.join('?' * len(f['priorities']))})")
        a += f["priorities"]
    if f.get("status") in ("open", "closed") and "status" not in exclude:
        w.append("t.active = ?")
        a.append(1 if f["status"] == "open" else 0)
    if f.get("exclude_id"):
        w.append("t.id != ?")
        a.append(f["exclude_id"])
    return frm, (" WHERE " + " AND ".join(w)) if w else "", a


def _count(conn, f):
    frm, where, a = build(f)
    return conn.execute(f"SELECT COUNT(*) FROM {frm}{where}", a).fetchone()[0]


def resolve(conn, f, q, mode, fts):
    """Decide how to apply the text query. Returns (filters, mode_used)."""
    f = dict(f)
    q = (q or "").strip()
    if not q:
        return f, None
    if NUMBER_RE.fullmatch(q):
        f["number"] = q.upper() + "%"
        return f, "number"
    if not fts:
        f["like"] = _like_tokens(q)
        return f, "like"
    and_q, or_q = fts_queries(q)
    if not and_q:
        return f, None
    if mode == "any":
        f["match"] = or_q
        return f, "any"
    f["match"] = and_q
    if mode == "smart" and and_q != or_q:
        try:
            if _count(conn, f) < SMART_MIN:
                f["match"] = or_q
                return f, "similar"
        except sqlite3.OperationalError:
            pass
    return f, "all"


def _row_out(r, has_hl):
    d = {k: r[k] for k in ("id", "sys_id", "tbl", "sys_class", "number", "short_description",
                           "state", "priority", "active", "group_name", "assigned_to",
                           "created_on", "updated_on", "closed_at", "cmdb_ci")}
    if has_hl:
        d["title_hl"] = r["hl_title"]
        d["snippet"] = next((x for x in (r["snip"], r["snip2"], r["snip3"]) if x and "\x01" in x),
                            r["snip"] or r["snip2"] or "")
    else:
        d["snippet"] = (r["description"] or r["close_notes"] or "")[:240]
    return d


def search(conn, filters, q="", mode="smart", sort="relevance", limit=50, offset=0, fts=True,
           facets=True):
    f, used = resolve(conn, filters, q, mode, fts)
    frm, where, a = build(f)
    total = conn.execute(f"SELECT COUNT(*) FROM {frm}{where}", a).fetchone()[0]
    has_hl = bool(f.get("match"))
    sel = "t.*"
    if has_hl:
        sel += (", highlight(tickets_fts, 1, char(1), char(2)) AS hl_title"
                ", snippet(tickets_fts, 2, char(1), char(2), '…', 18) AS snip"
                ", snippet(tickets_fts, 3, char(1), char(2), '…', 18) AS snip2"
                ", snippet(tickets_fts, 4, char(1), char(2), '…', 18) AS snip3"
                f", bm25(tickets_fts, {WEIGHTS}) AS score")
    if sort == "newest" or (sort == "relevance" and not has_hl):
        order = "t.created_on DESC"
    elif sort == "oldest":
        order = "t.created_on ASC"
    elif sort == "updated":
        order = "t.updated_on DESC"
    else:
        order = "score ASC, t.created_on DESC"
    rows = conn.execute(f"SELECT {sel} FROM {frm}{where} ORDER BY {order} LIMIT ? OFFSET ?",
                        a + [int(limit), int(offset)]).fetchall()
    out = {"total": total, "mode_used": used, "results": [_row_out(r, has_hl) for r in rows]}
    if facets:
        out["facets"] = _facets(conn, f)
    return out


def _facets(conn, f):
    res = {}
    frm, where, a = build(f, exclude=("groups",))
    res["groups"] = [
        {"name": r[0] or NO_GROUP, "count": r[1], "open": r[2] or 0}
        for r in conn.execute(f"SELECT t.group_name, COUNT(*), SUM(t.active) FROM {frm}{where} "
                              f"GROUP BY t.group_name ORDER BY COUNT(*) DESC", a)]
    frm, where, a = build(f, exclude=("tables",))
    res["tables"] = [{"name": r[0], "count": r[1]} for r in conn.execute(
        f"SELECT t.tbl, COUNT(*) FROM {frm}{where} GROUP BY t.tbl ORDER BY COUNT(*) DESC", a)]
    frm, where, a = build(f, exclude=("priorities",))
    res["priorities"] = [{"name": r[0], "count": r[1]} for r in conn.execute(
        f"SELECT t.priority, COUNT(*) FROM {frm}{where} GROUP BY t.priority ORDER BY t.priority", a)
        if r[0]]
    frm, where, a = build(f, exclude=("status",))
    r = conn.execute(f"SELECT COUNT(*), SUM(t.active) FROM {frm}{where}", a).fetchone()
    res["status"] = {"all": r[0] or 0, "open": r[1] or 0, "closed": (r[0] or 0) - (r[1] or 0)}
    frm, where, a = build(f)
    mm = conn.execute(f"SELECT MIN(t.created_on), MAX(t.created_on) FROM {frm}{where}", a).fetchone()
    unit, n = "day", 10
    if mm[0] and mm[1]:
        try:
            span = (datetime.fromisoformat(mm[1][:10]) - datetime.fromisoformat(mm[0][:10])).days
            if span > 150:
                unit, n = "month", 7
            elif span > 45:
                unit = "week"
        except ValueError:
            pass
    key = "strftime('%Y-%W', t.created_on)" if unit == "week" else f"substr(t.created_on, 1, {n})"
    res["timeline"] = {"unit": unit, "points": [
        [r[0], r[1], r[2] or 0] for r in conn.execute(
            f"SELECT {key} k, COUNT(*), SUM(t.active) FROM {frm}{where} GROUP BY k ORDER BY k", a) if r[0]]}
    return res


def ticket(conn, tid):
    r = conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def similar(conn, tid, fts=True, limit=20):
    t = ticket(conn, tid)
    if not t:
        return {"results": [], "total": 0}
    text = " ".join([t["short_description"] or "", t["cmdb_ci"] or ""])
    words, seen = [], set()
    for w in re.findall(r"[\w\-\.]{3,}", text):
        lw = w.lower()
        if lw in STOP or lw in seen or lw.isdigit() and len(lw) < 4:
            continue
        seen.add(lw)
        words.append(w)
    if not words:
        return {"results": [], "total": 0, "source": t["number"]}
    q = " ".join(words[:14])
    res = search(conn, {"exclude_id": tid}, q=q, mode="any", sort="relevance", limit=limit,
                 fts=fts, facets=False)
    res["source"] = t["number"]
    res["query"] = q
    return res


def export_rows(conn, filters, q, mode, sort, fts, max_rows=200000):
    f, _ = resolve(conn, filters, q, mode, fts)
    frm, where, a = build(f)
    sel = "t.*" + (f", bm25(tickets_fts, {WEIGHTS}) AS score" if f.get("match") else "")
    order = "score ASC" if (f.get("match") and sort == "relevance") else "t.created_on DESC"
    return conn.execute(f"SELECT {sel} FROM {frm}{where} ORDER BY {order} LIMIT ?", a + [max_rows])
