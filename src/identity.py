"""Request identity, principal binding, and session namespace composition.

This module owns the security-sensitive distinction between authenticated HTTP
principals and caller-controlled client labels. It intentionally has no server,
storage, or provider dependencies so transports and tools share one contract.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import quote


_ACTIVE_CALLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_active_caller", default=None
)

_ACTIVE_PRINCIPAL: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_authenticated_principal", default=None
)

_ACTIVE_CLIENT_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_http_client_id", default=None
)

_ACTIVE_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "unigrok_http_session_id", default=None
)


def scoped_session(session: Optional[str]) -> Optional[str]:
    """Namespace a session by authenticated principal and client label.

    HTTP middleware always binds a principal (issuer-bound OAuth identity,
    stable static-key ID,
    or the loopback anonymous principal). ``X-Client-ID`` remains an untrusted
    subordinate label that separates one principal's IDEs; it never provides
    the security boundary by itself. Non-HTTP callers preserve the historical
    unscoped behavior unless their transport binds one of these context vars.
    """
    principal = _ACTIVE_PRINCIPAL.get()
    client_id = _ACTIVE_CLIENT_ID.get()
    if not session:
        session = _ACTIVE_SESSION_ID.get()
    # Canonically encode independently owned segments before joining. Without
    # this, principal ``oauth:a:b`` + client ``c`` could collide with
    # principal ``oauth:a`` + client ``b:c``.
    namespace = ":".join(
        quote(part, safe="-._~") for part in (principal, client_id) if part
    )
    if namespace and session and not session.startswith(f"{namespace}:"):
        return f"{namespace}:{session}"
    return session


def normalize_caller(value: Any) -> Optional[str]:
    """Sanitize a caller label and bound it for database/metrics use."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    return text[:80] or None


def normalize_principal(value: Any) -> Optional[str]:
    """Normalize a principal without collision-prone prefix truncation."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    if not text:
        return None
    if len(text) <= 240:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return f"{text[:215]}~{digest}"


def set_active_caller(caller: Optional[str]):
    """Bind the request's reporting identity and return its reset token."""
    return _ACTIVE_CALLER.set(normalize_caller(caller))


def reset_active_caller(token) -> None:
    with contextlib.suppress(Exception):
        _ACTIVE_CALLER.reset(token)


def get_active_caller() -> Optional[str]:
    return _ACTIVE_CALLER.get()


def set_active_principal(principal: Optional[str]):
    """Bind the authenticated security principal for the current request."""
    return _ACTIVE_PRINCIPAL.set(normalize_principal(principal))


def reset_active_principal(token) -> None:
    with contextlib.suppress(Exception):
        _ACTIVE_PRINCIPAL.reset(token)


def get_active_principal() -> Optional[str]:
    return _ACTIVE_PRINCIPAL.get()


def get_active_client_id() -> Optional[str]:
    """Return the untrusted X-Client-ID label bound for this request, if any."""
    return _ACTIVE_CLIENT_ID.get()


def principal_kind(principal: Optional[str] = None) -> str:
    """Classify the security principal without exposing raw identifiers."""
    value = principal if principal is not None else get_active_principal()
    if not value:
        return "none"
    if value.startswith("oauth:"):
        return "oauth"
    if value.startswith(("http:key:", "http:key-")):
        return "api_key"
    if value == "http:anon":
        return "anon"
    if value.startswith("stdio:"):
        return "stdio"
    return "other"


def resolve_request_caller(caller: Optional[str]) -> Optional[str]:
    """Resolve attribution without letting HTTP metadata replace principal.

    FastMCP handlers often pass ``clientInfo.name`` explicitly. On HTTP that
    value remains only a client label; the middleware's combined
    ``principal|label`` attribution wins so budget accounting stays anchored
    to the principal. Stdio has no HTTP principal and preserves explicit
    caller behavior.
    """
    if get_active_principal():
        return get_active_caller() or get_active_principal()
    return normalize_caller(caller) or get_active_caller()


def caller_from_mcp_context(ctx: Any) -> Optional[str]:
    """Read the MCP ``clientInfo.name`` label from a FastMCP context."""
    try:
        params = getattr(getattr(ctx, "session", None), "client_params", None)
        info = getattr(params, "clientInfo", None)
        return normalize_caller(getattr(info, "name", None))
    except Exception:
        return None


def telemetry_row_caller(row: Dict[str, Any]) -> Optional[str]:
    """Return the normalized caller from a telemetry metadata envelope."""
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return None
    if not isinstance(meta, dict):
        return None
    return normalize_caller(meta.get("caller"))
