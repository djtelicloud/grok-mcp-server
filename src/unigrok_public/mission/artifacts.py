"""Sealed content hashes vs redacted storage projections."""

from __future__ import annotations

import hashlib

from unigrok_public.state import redact_secrets

PROJECTION_MAX_BYTES = 100_000


def sealed_content_hash(raw: bytes | str, *, kind: str = "text") -> str:
    """Hash exact sealed bytes. Never truncate or redact before hashing."""
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    else:
        payload = raw
    prefix = f"{kind}\n".encode()
    return hashlib.sha256(prefix + payload).hexdigest()


def artifact_projection(raw: str, *, max_bytes: int = PROJECTION_MAX_BYTES) -> str:
    """Redacted, size-capped form safe for SQLite / MCP echo. Not used for hashes."""
    text = redact_secrets(raw)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Truncate on UTF-8 byte boundary.
    cut = encoded[:max_bytes]
    while cut:
        try:
            return cut.decode("utf-8") + "\n…[truncated]"
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "…[truncated]"
