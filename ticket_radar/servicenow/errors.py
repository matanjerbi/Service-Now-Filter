"""Exceptions raised by the ServiceNow layer. Messages are user-facing (Hebrew)."""


class ServiceNowError(Exception):
    """Base class for all ServiceNow communication errors."""


class AuthenticationError(ServiceNowError):
    """401 – bad credentials / expired token."""


class PermissionDeniedError(ServiceNowError):
    """403 – the user may not read this table / run this query."""


class ConnectionFailedError(ServiceNowError):
    """Network-level failure (DNS, TLS, proxy, timeout)."""


class UnexpectedResponseError(ServiceNowError):
    """The server answered with something that isn't the expected JSON (e.g. an SSO login page)."""
