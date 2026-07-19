"""Fenced mission leases with monotonic generation tokens."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_lease_token() -> str:
    return secrets.token_hex(8)


def fence_generation_next(current: int | None) -> int:
    return int(current or 0) + 1


def lease_expiry_iso(*, ttl_seconds: int) -> str:
    expires = datetime.now(UTC).timestamp() + max(5, int(ttl_seconds))
    return datetime.fromtimestamp(expires, tz=UTC).isoformat()


def lease_active(expires_at: str | None, *, now: str | None = None) -> bool:
    if not expires_at:
        return False
    current = now or datetime.now(UTC).isoformat()
    return str(expires_at) > current
