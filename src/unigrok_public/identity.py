"""Authenticated request identity and tenant-safe state names.

The public core is single-operator by default.  A hosted deployment binds an
issuer-qualified OAuth principal in :mod:`unigrok_public.remote_auth`; this
module turns that identity into a non-reversible, SQLite-safe namespace.
Caller-controlled labels never form the security boundary.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from contextvars import ContextVar, Token
from typing import Any

_ACTIVE_PRINCIPAL: ContextVar[str | None] = ContextVar(
    "unigrok_authenticated_principal", default=None
)
_TENANT_PREFIX_RE = re.compile(r"^tenant-[0-9a-f]{24}:")
_MAX_STATE_NAME_CHARS = 128


def set_active_principal(principal: str | None) -> Token[str | None]:
    return _ACTIVE_PRINCIPAL.set(str(principal or "").strip() or None)


def reset_active_principal(token: Token[str | None]) -> None:
    with contextlib.suppress(Exception):
        _ACTIVE_PRINCIPAL.reset(token)


def get_active_principal() -> str | None:
    return _ACTIVE_PRINCIPAL.get()


def principal_kind(principal: str | None = None) -> str:
    value = principal if principal is not None else get_active_principal()
    if not value:
        return "none"
    return "oauth" if value.startswith("oauth:") else "other"


def principal_label(principal: str | None = None) -> str | None:
    """Return a stable attribution label without exposing issuer or subject."""
    value = principal if principal is not None else get_active_principal()
    if not value:
        return None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"oauth-{digest}" if value.startswith("oauth:") else f"principal-{digest}"


def tenant_prefix(principal: str | None = None) -> str | None:
    value = principal if principal is not None else get_active_principal()
    if not value:
        return None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"tenant-{digest}:"


def _scoped_name(value: Any) -> str:
    name = str(value or "").strip()
    prefix = tenant_prefix()
    if not prefix:
        return name
    existing = _TENANT_PREFIX_RE.match(name)
    if existing:
        if name.startswith(prefix):
            return name
        raise ValueError("state resource does not belong to the authenticated principal")
    available = _MAX_STATE_NAME_CHARS - len(prefix)
    if len(name) > available:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        name = f"{name[: max(1, available - 17)]}~{digest}"
    return f"{prefix}{name}"


def scoped_session(value: Any) -> str:
    return _scoped_name(value)


def scoped_scope(value: Any = "global") -> str:
    return _scoped_name(str(value or "global").strip() or "global")


def public_state_name(value: Any) -> str:
    """Strip only the active tenant's internal prefix from a returned name."""
    name = str(value or "")
    prefix = tenant_prefix()
    return name[len(prefix) :] if prefix and name.startswith(prefix) else name

