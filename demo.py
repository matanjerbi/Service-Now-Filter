"""Generate realistic-looking demo tickets so the UI can be explored offline."""
import random
import uuid
from datetime import datetime, timedelta, timezone

import db as dbm

HOSTS = ["redis-prd-01", "redis-prd-02", "redis-stg-03", "pg-prd-11", "pg-prd-12", "k8s-node-07",
         "app-srv-21", "nfs-04", "lb-ext-02", "vault-01"]
CLUSTERS = ["payments", "sessions", "catalog", "auth-cache", "analytics", "orders"]
TEAMS = ["Billing", "Mobile", "Search", "Data", "Platform", "CRM"]

G = {
    "DBA - Redis": [
        ("Redis cluster {cl} - high memory usage on shard {n}",
         "used_memory reached 92% of maxmemory on {h}. Evictions started. "
         "OOM command not allowed when used memory > 'maxmemory'."),
        ("Replication lag on Redis DB {cl}", "Replica on {h} is behind master by {n} seconds, "
         "client timeouts reported by team {t}."),
        ("בקשה לפתיחת DB חדש ב-Redis עבור צוות {t}", "צוות {t} מבקש DB חדש בגודל {n}GB עם replication ו-persistence AOF."),
        ("Redis latency spikes - slowlog shows KEYS command", "Latency p99 > 200ms on {cl}. SLOWLOG shows KEYS * from app-srv."),
        ("חיבור ל-Redis נכשל: ERR max number of clients reached", "האפליקציה של {t} מקבלת max number of clients reached מ-{h}."),
    ],
    "DBA - PostgreSQL": [
        ("PostgreSQL: FATAL: too many connections for role app_{t}",
         "Connection pool exhausted on {h}. FATAL: sorry, too many clients already."),
        ("Disk usage 95% on /var/lib/pgsql on {h}", "WAL files accumulating, replication slot inactive."),
        ("Slow query on orders table after deploy", "Seq scan on orders, missing index on customer_id. Team {t}."),
        ("בקשה להרשאת קריאה ל-DB של {cl}", "נא לפתוח הרשאת read-only למשתמש של צוות {t}."),
    ],
    "Kubernetes Platform": [
        ("Pod CrashLoopBackOff in namespace {t}", "Deployment {cl}-api restarting, OOMKilled, exit code 137."),
        ("Ingress returns 502 Bad Gateway for {cl}", "nginx ingress 502 upstream connect error on {h}."),
        ("בקשה להגדלת quota ל-namespace של {t}", "צריך עוד {n} CPU ו-{n}0Gi זיכרון."),
    ],
    "Network": [
        ("VPN לא מתחבר מהבית", "משתמש מצוות {t} לא מצליח להתחבר ל-VPN, שגיאת timeout."),
        ("Packet loss between DC1 and DC2", "Packet loss {n}% observed on link to {h}, affects replication."),
        ("פתיחת פורט {n}379 מ-{h} לשרתי Redis", "בקשת firewall לפתיחת פורט עבור צוות {t}."),
    ],
    "Linux Infra": [
        ("Certificate expiring on {h}", "TLS certificate expires in {n} days. Renew via internal CA."),
        ("High load average on {h}", "Load average {n}0 on {h}, iowait high, check disk."),
        ("NTP drift detected on {h}", "Clock drift of {n} seconds causing auth failures."),
    ],
    "Service Desk": [
        ("בקשה להרשאות ל-Grafana", "נא לתת הרשאת viewer ל-Grafana לצוות {t}."),
        ("איפוס סיסמה למשתמש", "משתמש נעול לאחר {n} ניסיונות התחברות."),
        ("Laptop replacement request", "Battery swollen, need replacement for user in {t}."),
    ],
    "Security Ops": [
        ("Suspicious login attempts on {h}", "{n}00 failed SSH logins from internal subnet."),
        ("Vulnerability scan: Redis exposed without AUTH on {h}", "Scanner found port 6379 open without requirepass."),
    ],
    "": [
        ("General question about monitoring", "Where can I find the dashboards for {cl}?"),
    ],
}
RESOLUTIONS = [
    "Increased maxmemory and enabled eviction policy allkeys-lru. Monitored for 24h.",
    "Root cause: connection leak in client library. Team upgraded driver and restarted.",
    "הוגדל נפח הדיסק ונוקו קבצי WAL ישנים.",
    "Restarted service, issue resolved. Added alert.",
    "Replaced KEYS with SCAN in the application code.",
    "Added missing index, query time dropped from 12s to 40ms.",
    "Duplicate of an existing ticket, closed.",
]
TABLES = [("incident", "INC", 0.55), ("sc_req_item", "RITM", 0.25), ("change_request", "CHG", 0.12),
          ("problem", "PRB", 0.08)]
PRIOS = ["1 - Critical", "2 - High", "3 - Moderate", "4 - Low"]
PEOPLE = ["Matan Jerbi", "Noa Levi", "Yossi Cohen", "Dana Mizrahi", "Avi Peretz", "Shira Katz", ""]
OPEN_STATES = ["New", "In Progress", "On Hold"]
CLOSED_STATES = ["Resolved", "Closed", "Closed Complete"]


def generate(path, n=1800, seed=7):
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc)
    rows, counters = [], {p: 10000 for _, p, _ in TABLES}
    groups = list(G.keys())
    gw = [5, 3, 3, 2, 2, 3, 1, 0.3]
    for _ in range(n):
        tbl, pre, _ = rnd.choices(TABLES, weights=[w for *_, w in TABLES])[0]
        counters[pre] += rnd.randint(1, 3)
        g = rnd.choices(groups, weights=gw)[0]
        title_t, desc_t = rnd.choice(G[g])
        ctx = dict(cl=rnd.choice(CLUSTERS), n=rnd.randint(2, 9), h=rnd.choice(HOSTS), t=rnd.choice(TEAMS))
        created = now - timedelta(days=rnd.random() ** 1.4 * 365, minutes=rnd.randint(0, 1440))
        age = (now - created).days
        active = rnd.random() < (0.75 if age < 7 else 0.3 if age < 30 else 0.04)
        updated = created + timedelta(hours=rnd.randint(1, 24 * min(age + 1, 20)))
        updated = min(updated, now)
        fmt = "%Y-%m-%d %H:%M:%S"
        rows.append({
            "sys_id": uuid.UUID(int=rnd.getrandbits(128)).hex, "tbl": tbl, "sys_class": tbl,
            "number": f"{pre}{counters[pre]:07d}",
            "short_description": title_t.format(**ctx), "description": desc_t.format(**ctx),
            "state": rnd.choice(OPEN_STATES if active else CLOSED_STATES),
            "priority": rnd.choices(PRIOS, weights=[1, 3, 8, 4])[0], "active": 1 if active else 0,
            "group_id": "", "group_name": g, "assigned_to": rnd.choice(PEOPLE),
            "opened_by": rnd.choice(PEOPLE[:-1]), "category": "", "cmdb_ci": ctx["h"],
            "close_notes": "" if active else ("נפתחה הרשאה כמבוקש." if "הרשא" in title_t
                                              else rnd.choice(RESOLUTIONS)), "journal": "",
            "created_on": created.strftime(fmt), "updated_on": updated.strftime(fmt),
            "closed_at": "" if active else updated.strftime(fmt),
        })
    conn = dbm.connect(path)
    dbm.upsert(conn, rows)
    dbm.set_meta(conn, "last_sync", now.strftime("%Y-%m-%d %H:%M:%S"))
    conn.close()
