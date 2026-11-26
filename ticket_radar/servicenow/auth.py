"""Authentication strategies for the ServiceNow REST API."""
from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any, Protocol

from ..config import AuthSettings, AuthType
from .errors import AuthenticationError

# (path, form_fields) -> parsed JSON body. Supplied by the client so OAuth reuses its transport.
FormPoster = Callable[[str, dict[str, str]], dict[str, Any]]


class AuthProvider(Protocol):
    def headers(self) -> dict[str, str]: ...

    def invalidate(self) -> bool:
        """Drop cached credentials. Returns True if a retry might succeed."""
        ...


class BasicAuth:
    def __init__(self, username: str, password: str) -> None:
        if not username:
            raise AuthenticationError("חסר SN_USERNAME עבור הזדהות basic")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._header = {"Authorization": f"Basic {token}"}

    def headers(self) -> dict[str, str]:
        return self._header

    def invalidate(self) -> bool:
        return False


class ApiKeyAuth:
    """ServiceNow inbound REST API key (sent in the x-sn-apikey header)."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise AuthenticationError("חסר SN_API_KEY עבור הזדהות api_key")
        self._header = {"x-sn-apikey": api_key}

    def headers(self) -> dict[str, str]:
        return self._header

    def invalidate(self) -> bool:
        return False


class BearerAuth:
    def __init__(self, token: str) -> None:
        if not token:
            raise AuthenticationError("חסר SN_TOKEN עבור הזדהות bearer")
        self._header = {"Authorization": f"Bearer {token}"}

    def headers(self) -> dict[str, str]:
        return self._header

    def invalidate(self) -> bool:
        return False


class OAuthAuth:
    """OAuth 2.0 against /oauth_token.do – password grant if a username is set,
    otherwise client_credentials. Tokens are cached until shortly before expiry."""

    TOKEN_PATH = "/oauth_token.do"
    EXPIRY_MARGIN_SEC = 60

    def __init__(self, settings: AuthSettings, post_form: FormPoster) -> None:
        if not settings.client_id or not settings.client_secret:
            raise AuthenticationError("חסרים SN_CLIENT_ID / SN_CLIENT_SECRET עבור הזדהות oauth")
        self._settings = settings
        self._post_form = post_form
        self._token: str | None = None
        self._expires_at = 0.0

    def _fetch_token(self) -> None:
        s = self._settings
        form = {"client_id": s.client_id, "client_secret": s.client_secret}
        if s.username:
            form |= {"grant_type": "password", "username": s.username, "password": s.password}
        else:
            form["grant_type"] = "client_credentials"
        body = self._post_form(self.TOKEN_PATH, form)
        try:
            self._token = body["access_token"]
        except KeyError:
            raise AuthenticationError("OAuth: התשובה לא הכילה access_token") from None
        self._expires_at = time.time() + int(body.get("expires_in", 1800))

    def headers(self) -> dict[str, str]:
        if not self._token or time.time() > self._expires_at - self.EXPIRY_MARGIN_SEC:
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def invalidate(self) -> bool:
        had_token = self._token is not None
        self._token = None
        return had_token


def build_auth(settings: AuthSettings, post_form: FormPoster) -> AuthProvider:
    match settings.type:
        case AuthType.BASIC:
            return BasicAuth(settings.username, settings.password)
        case AuthType.API_KEY:
            return ApiKeyAuth(settings.api_key)
        case AuthType.BEARER:
            return BearerAuth(settings.token)
        case AuthType.OAUTH:
            return OAuthAuth(settings, post_form)
    raise AuthenticationError(f"סוג הזדהות לא נתמך: {settings.type}")
