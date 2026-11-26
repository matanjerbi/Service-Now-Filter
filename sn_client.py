"""Minimal ServiceNow REST client (Python standard library only).

Supports Basic auth, OAuth 2.0 (password / client_credentials grant) and
REST API keys. Handles pagination, retries on 429/5xx, custom CA bundles
and corporate proxies (via the standard HTTPS_PROXY env variable).
"""
import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

FIELDS = [
    "sys_id", "number", "short_description", "description", "state",
    "priority", "assignment_group", "assigned_to", "opened_by", "caller_id",
    "sys_created_on", "sys_updated_on", "closed_at", "active", "category",
    "cmdb_ci", "close_notes", "sys_class_name",
]
JOURNAL_FIELDS = ["comments_and_work_notes"]


class SNError(Exception):
    pass


def _v(rec, f):
    x = rec.get(f)
    if isinstance(x, dict):
        return x.get("value") or ""
    return x if x not in (None,) else ""


def _d(rec, f):
    x = rec.get(f)
    if isinstance(x, dict):
        return x.get("display_value") or x.get("value") or ""
    return x if x not in (None,) else ""


def normalize(rec, table):
    """Turn a sysparm_display_value=all record into a flat dict for the DB."""
    active = str(_v(rec, "active")).lower() in ("true", "1")
    return {
        "sys_id": _v(rec, "sys_id"),
        "tbl": table,
        "sys_class": _v(rec, "sys_class_name") or table,
        "number": str(_d(rec, "number")),
        "short_description": str(_d(rec, "short_description")),
        "description": str(_d(rec, "description")),
        "state": str(_d(rec, "state")),
        "priority": str(_d(rec, "priority")),
        "active": 1 if active else 0,
        "group_id": str(_v(rec, "assignment_group")),
        "group_name": str(_d(rec, "assignment_group")),
        "assigned_to": str(_d(rec, "assigned_to")),
        "opened_by": str(_d(rec, "caller_id") or _d(rec, "opened_by")),
        "category": str(_d(rec, "category")),
        "cmdb_ci": str(_d(rec, "cmdb_ci")),
        "close_notes": str(_d(rec, "close_notes")),
        "journal": str(_d(rec, "comments_and_work_notes")),
        "created_on": str(_v(rec, "sys_created_on")),
        "updated_on": str(_v(rec, "sys_updated_on")),
        "closed_at": str(_v(rec, "closed_at")),
    }


class ServiceNowClient:
    def __init__(self, cfg):
        inst = (cfg.get("instance") or "").strip().rstrip("/")
        if not inst:
            raise SNError("לא הוגדר instance בקובץ config.json")
        if not inst.startswith("http"):
            inst = "https://" + inst
        self.base = inst
        self.auth = cfg.get("auth", {}) or {}
        self.timeout = int(cfg.get("timeout", 60))
        self.page_size = int(cfg.get("page_size", 500))
        self.ctx = self._ssl(cfg)
        self._token = None
        self._token_exp = 0

    # ---------- auth ----------
    @staticmethod
    def _ssl(cfg):
        if cfg.get("verify_ssl", True) is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if cfg.get("ca_bundle"):
            return ssl.create_default_context(cafile=cfg["ca_bundle"])
        return ssl.create_default_context()

    def _secret(self, key, env):
        return os.environ.get(env) or self.auth.get(key) or ""

    def _headers(self):
        h = {"Accept": "application/json"}
        t = (self.auth.get("type") or "basic").lower()
        if t == "basic":
            u = self._secret("username", "SN_USERNAME")
            p = self._secret("password", "SN_PASSWORD")
            h["Authorization"] = "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()
        elif t == "oauth":
            h["Authorization"] = "Bearer " + self._oauth_token()
        elif t == "api_key":
            h["x-sn-apikey"] = self._secret("api_key", "SN_API_KEY")
        elif t == "bearer":
            h["Authorization"] = "Bearer " + self._secret("token", "SN_TOKEN")
        else:
            raise SNError(f"סוג הזדהות לא מוכר: {t}")
        return h

    def _oauth_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        user = self._secret("username", "SN_USERNAME")
        data = {
            "client_id": self._secret("client_id", "SN_CLIENT_ID"),
            "client_secret": self._secret("client_secret", "SN_CLIENT_SECRET"),
        }
        if user:
            data.update(grant_type="password", username=user,
                        password=self._secret("password", "SN_PASSWORD"))
        else:
            data["grant_type"] = "client_credentials"
        req = urllib.request.Request(
            self.base + "/oauth_token.do",
            data=urllib.parse.urlencode(data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                j = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SNError(f"OAuth נכשל (HTTP {e.code}): {e.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as e:
            raise SNError(f"OAuth – שגיאת חיבור: {e.reason}")
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 1800))
        return self._token

    # ---------- http ----------
    def _get(self, path, params, retries=5):
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                    raw = r.read().decode("utf-8")
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        raise SNError("התשובה מהשרת אינה JSON – ייתכן שהגעת לדף התחברות (SSO). "
                                      "בדוק כתובת instance והזדהות.")
                    return body, r.headers
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")[:400]
                if e.code == 401:
                    if (self.auth.get("type") == "oauth") and attempt == 0:
                        self._token = None
                        continue
                    raise SNError("401 – הזדהות נכשלה. בדוק משתמש/סיסמה או טוקן.")
                if e.code == 403:
                    raise SNError(f"403 – אין הרשאה לטבלה/לשאילתה הזו. {msg}")
                if e.code == 429 or e.code >= 500:
                    last = f"HTTP {e.code}: {msg}"
                    wait = e.headers.get("Retry-After")
                    time.sleep(int(wait) if wait and wait.isdigit() else 2 ** attempt * 2)
                    continue
                raise SNError(f"HTTP {e.code}: {msg}")
            except urllib.error.URLError as e:
                last = f"שגיאת חיבור: {e.reason}"
                time.sleep(2 ** attempt)
                continue
        raise SNError(last or "הבקשה נכשלה")

    # ---------- api ----------
    def test(self, table):
        body, headers = self._get(f"/api/now/table/{table}",
                                  {"sysparm_limit": 1, "sysparm_fields": "sys_id"})
        total = headers.get("X-Total-Count")
        return {"ok": True, "table": table, "visible_records": int(total) if total else None}

    def my_groups(self, username=None):
        """Groups the authenticated user (or `username`) is a member of."""
        q = f"user.user_name={username}" if username else "user=javascript:gs.getUserID()"
        body, _ = self._get("/api/now/table/sys_user_grmember", {
            "sysparm_query": q, "sysparm_fields": "group",
            "sysparm_display_value": "all", "sysparm_limit": 1000,
            "sysparm_exclude_reference_link": "true"})
        return [(_v(r, "group"), _d(r, "group")) for r in body.get("result", []) if _v(r, "group")]

    def groups_by_name(self, names):
        body, _ = self._get("/api/now/table/sys_user_group", {
            "sysparm_query": "nameIN" + ",".join(names),
            "sysparm_fields": "sys_id,name", "sysparm_limit": 1000})
        return [(r["sys_id"], r["name"]) for r in body.get("result", [])]

    def iter_records(self, table, query, fields):
        """Yield (record, total) for every record matching `query` (paged)."""
        offset = 0
        total = None
        while True:
            body, headers = self._get(f"/api/now/table/{table}", {
                "sysparm_query": query,
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": self.page_size,
                "sysparm_offset": offset,
            })
            if total is None and headers.get("X-Total-Count"):
                try:
                    total = int(headers["X-Total-Count"])
                except ValueError:
                    pass
            rows = body.get("result", [])
            for r in rows:
                yield r, total
            if len(rows) < self.page_size:
                break
            offset += self.page_size
