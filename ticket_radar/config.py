"""Application settings, loaded from environment variables (and an optional .env file).

Priority: real environment  >  .env file  >  defaults.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_TABLES: tuple[str, ...] = ("incident", "sc_req_item", "change_request", "problem")


class ConfigError(ValueError):
    """Raised when an environment variable holds an invalid value."""


class AuthType(StrEnum):
    BASIC = "basic"
    API_KEY = "api_key"
    OAUTH = "oauth"
    BEARER = "bearer"


class ScopeMode(StrEnum):
    ALL = "all"
    MY_GROUPS = "my_groups"
    GROUPS = "groups"


class DateQueryStyle(StrEnum):
    SCRIPT = "script"
    PLAIN = "plain"


@dataclass(frozen=True, slots=True)
class AuthSettings:
    type: AuthType = AuthType.BASIC
    username: str = ""
    password: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    token: str = field(default="", repr=False)

    def secrets_present(self) -> list[str]:
        names = ("password", "api_key", "client_secret", "token")
        return [n for n in names if getattr(self, n)]


@dataclass(frozen=True, slots=True)
class ScopeSettings:
    mode: ScopeMode = ScopeMode.ALL
    groups: tuple[str, ...] = ()
    user: str = ""


@dataclass(frozen=True, slots=True)
class ServiceNowSettings:
    instance: str = ""
    auth: AuthSettings = field(default_factory=AuthSettings)
    tables: tuple[str, ...] = DEFAULT_TABLES
    scope: ScopeSettings = field(default_factory=ScopeSettings)
    include_journal: bool = False
    extra_query: str = ""
    date_query_style: DateQueryStyle = DateQueryStyle.SCRIPT
    page_size: int = 500
    timeout: int = 60
    verify_ssl: bool = True
    ca_bundle: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.instance)

    @property
    def base_url(self) -> str:
        url = self.instance.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url


@dataclass(frozen=True, slots=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("tickets.db")


@dataclass(frozen=True, slots=True)
class Settings:
    servicenow: ServiceNowSettings = field(default_factory=ServiceNowSettings)
    server: ServerSettings = field(default_factory=ServerSettings)

    def describe(self) -> str:
        """Human-readable summary with secrets masked."""
        sn = self.servicenow
        secrets = ", ".join(sn.auth.secrets_present()) or "(אין!)"
        lines = [
            f"SN_INSTANCE      = {sn.instance or '(חסר!)'}",
            f"SN_AUTH_TYPE     = {sn.auth.type}",
            f"SN_USERNAME      = {sn.auth.username or '-'}",
            f"סודות מוגדרים    = {secrets}",
            f"SN_TABLES        = {', '.join(sn.tables)}",
            f"SN_SCOPE         = {sn.scope.mode}"
            + (f"  groups={', '.join(sn.scope.groups)}" if sn.scope.groups else "")
            + (f"  user={sn.scope.user}" if sn.scope.user else ""),
            f"SSL              = {'verify' if sn.verify_ssl else 'NO VERIFY'}"
            + (f"  ca={sn.ca_bundle}" if sn.ca_bundle else ""),
            f"server           = {self.server.host}:{self.server.port}  db={self.server.db_path}",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- parsing
def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name}: צפוי true/false, התקבל {raw!r}")


def _parse_int(name: str, raw: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}: צפוי מספר, התקבל {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name}: חייב להיות חיובי")
    return value


def _parse_list(_: str, raw: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in raw.replace("|", ",").split(",") if x.strip())


def _parse_enum(enum: type[StrEnum]) -> Callable[[str, str], StrEnum]:
    def parse(name: str, raw: str) -> StrEnum:
        try:
            return enum(raw.strip().lower())
        except ValueError:
            allowed = " / ".join(e.value for e in enum)
            raise ConfigError(f"{name}: ערך לא חוקי {raw!r} (אפשרויות: {allowed})") from None
    return parse


def _parse_str(_: str, raw: str) -> str:
    return raw.strip()


def load_dotenv(path: Path, environ: dict[str, str] | None = None) -> list[str]:
    """Load KEY=VALUE lines from `path` into `environ` without overriding existing keys.

    Supports comments (#), blank lines, `export KEY=...` and single/double quotes.
    Returns the list of keys that were set.
    """
    target = os.environ if environ is None else environ
    if not path.is_file():
        return []
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in target:
            target[key] = value
            loaded.append(key)
    return loaded


def load_settings(environ: Mapping[str, str] | None = None, base_dir: Path | None = None) -> Settings:
    """Build Settings from environment variables. Empty values fall back to defaults."""
    env = os.environ if environ is None else environ
    base = base_dir or Path.cwd()

    def get(name: str, parser: Callable[[str, str], Any], default: Any) -> Any:
        raw = env.get(name, "")
        return parser(name, raw) if raw.strip() else default

    auth = AuthSettings(
        type=get("SN_AUTH_TYPE", _parse_enum(AuthType), AuthType.BASIC),
        username=get("SN_USERNAME", _parse_str, ""),
        password=env.get("SN_PASSWORD", ""),  # not stripped – may legitimately contain spaces
        api_key=get("SN_API_KEY", _parse_str, ""),
        client_id=get("SN_CLIENT_ID", _parse_str, ""),
        client_secret=get("SN_CLIENT_SECRET", _parse_str, ""),
        token=get("SN_TOKEN", _parse_str, ""),
    )
    scope = ScopeSettings(
        mode=get("SN_SCOPE", _parse_enum(ScopeMode), ScopeMode.ALL),
        groups=get("SN_SCOPE_GROUPS", _parse_list, ()),
        user=get("SN_SCOPE_USER", _parse_str, ""),
    )
    if scope.mode is ScopeMode.GROUPS and not scope.groups:
        raise ConfigError("SN_SCOPE=groups דורש רשימת צוותים ב-SN_SCOPE_GROUPS")

    sn = ServiceNowSettings(
        instance=get("SN_INSTANCE", _parse_str, ""),
        auth=auth,
        tables=get("SN_TABLES", _parse_list, DEFAULT_TABLES) or DEFAULT_TABLES,
        scope=scope,
        include_journal=get("SN_INCLUDE_JOURNAL", _parse_bool, False),
        extra_query=get("SN_EXTRA_QUERY", _parse_str, ""),
        date_query_style=get("SN_DATE_QUERY_STYLE", _parse_enum(DateQueryStyle), DateQueryStyle.SCRIPT),
        page_size=get("SN_PAGE_SIZE", _parse_int, 500),
        timeout=get("SN_TIMEOUT", _parse_int, 60),
        verify_ssl=get("SN_VERIFY_SSL", _parse_bool, True),
        ca_bundle=get("SN_CA_BUNDLE", _parse_str, ""),
    )

    db_path = Path(get("TR_DB_PATH", _parse_str, "tickets.db"))
    if not db_path.is_absolute():
        db_path = base / db_path
    server = ServerSettings(
        host=get("TR_HOST", _parse_str, "127.0.0.1"),
        port=get("TR_PORT", _parse_int, 8765),
        db_path=db_path,
    )
    return Settings(servicenow=sn, server=server)
