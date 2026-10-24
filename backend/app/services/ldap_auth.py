"""Active Directory sign-in.

Bind as DOMAIN\\login over LDAPS, then read the display name. The directory
address comes from the environment. Certificate checks are off by default so an
internal Active Directory certificate can still be used.
"""

import os
import ssl
from urllib.parse import urlparse

from app.errors import AppError


DEFAULT_LDAP_DOMAINS = ("NTSGPDOM", "NTMYPDOM")


def configured() -> bool:
    return bool(_host())


def domains() -> list[str]:
    """List of allowed Active Directory / LDAP domains configured in the environment."""
    raw = _env("LDAP_DOMAINS") or _env("LDAP_DOMAIN")
    parsed = [part.strip() for part in raw.split(",") if part.strip()]
    return parsed if parsed else list(DEFAULT_LDAP_DOMAINS)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _flag(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes", "y", "on"}


def _host() -> str:
    raw = _env("LDAP_HOST") or _env("LDAP_URL")
    if "://" in raw:
        return urlparse(raw).hostname or ""
    return raw


def escape_filter(value: str) -> str:
    """RFC 4515 escaping so a login ID cannot change the search filter."""
    return (
        value.replace("\\", r"\5c")
        .replace("*", r"\2a")
        .replace("(", r"\28")
        .replace(")", r"\29")
        .replace("\x00", r"\00")
    )


def authenticate(login_id: str, password: str, user_domain: str = "") -> str:
    """Confirm the password and return the directory display name."""
    login_id = login_id.strip()
    if not configured():
        raise AppError(503, "LDAP is not configured.")
    if not login_id or not password:
        raise AppError(401, "Invalid login ID or password")
    available = domains()
    domain = user_domain.strip() or _env("LDAP_DOMAIN") or (available[0] if available else "")
    base = _env("LDAP_BASE_DN")
    if not domain or not base:
        raise AppError(503, "Set LDAP_DOMAIN (or LDAP_DOMAINS) and LDAP_BASE_DN.")

    from ldap3 import NONE, SUBTREE, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPBindError, LDAPException

    host, port, use_ssl = _address()
    timeout = int(_env("LDAP_TIMEOUT_SECONDS") or "10")
    tls = None
    if use_ssl and not _flag("LDAP_TLS_VALIDATE"):
        tls = Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLS_CLIENT)
    server = Server(host, port=port, use_ssl=use_ssl, tls=tls, get_info=NONE, connect_timeout=timeout)
    try:
        conn = Connection(
            server,
            user=f"{domain}\\{login_id}",
            password=password,
            receive_timeout=timeout,
            auto_bind=True,
        )
    except LDAPBindError as exc:
        raise AppError(401, "Invalid login ID or password") from exc
    except LDAPException as exc:
        raise AppError(503, "The directory server could not be reached.") from exc

    try:
        filt = _env("LDAP_USER_FILTER") or "(&(objectClass=person)(sAMAccountName={login_id}))"
        conn.search(
            base,
            filt.replace("{login_id}", escape_filter(login_id)),
            search_scope=SUBTREE,
            attributes=["sAMAccountName", "displayName", "mail"],
        )
        if not conn.entries:
            return login_id
        entry = conn.entries[0]
        return _value(entry, "displayName") or _value(entry, "sAMAccountName") or login_id
    finally:
        conn.unbind()


def _address() -> tuple[str, int, bool]:
    raw = _env("LDAP_HOST") or _env("LDAP_URL")
    if "://" in raw:
        parsed = urlparse(raw)
        use_ssl = parsed.scheme == "ldaps"
        return parsed.hostname or "", parsed.port or (636 if use_ssl else 389), use_ssl
    port = int(_env("LDAP_PORT") or "636")
    return raw, port, port == 636 or _flag("LDAP_USE_SSL")


def _value(entry, name: str) -> str:
    try:
        raw = entry[name].value
    except (KeyError, AttributeError, IndexError, TypeError):
        return ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").strip()
