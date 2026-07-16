"""Owner-default and optional principal-bound xAI API keys.

Cloud twin law (sponsor Approved):
- Default spend path is the service ``XAI_API_KEY`` (owner Secret Manager).
- Write+ insiders may optionally bind their own key for their OAuth principal.
- Labels like ``X-Client-ID`` never select a key.
- Never log key material.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from src.identity import get_active_principal, principal_kind

_PLACEHOLDER = "your_xai_api_key_here"
_PRINCIPAL_KEYS_ENV = "UNIGROK_PRINCIPAL_XAI_KEYS_JSON"


def normalize_xai_api_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    return "" if not key or key == _PLACEHOLDER else key


def default_xai_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Owner / service default key (Live cloud twin path)."""
    source = os.environ if environ is None else environ
    return normalize_xai_api_key(source.get("XAI_API_KEY"))


def _parse_principal_key_table(raw: str) -> Dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        norm_key = re.sub(r"[\x00-\x1f\x7f]", "", key).strip()
        if not norm_key or len(norm_key) > 240:
            continue
        secret = normalize_xai_api_key(value if isinstance(value, str) else None)
        if secret:
            out[norm_key] = secret
    return out


def load_principal_xai_key_table(
    environ: Mapping[str, str] | None = None,
) -> Dict[str, str]:
    """Load optional principal → key map (never log values)."""
    source = os.environ if environ is None else environ
    return _parse_principal_key_table(source.get(_PRINCIPAL_KEYS_ENV, ""))


def _lookup_principal_key(
    principal: str, table: Mapping[str, str]
) -> Optional[str]:
    if principal in table:
        return table[principal]
    # Allow map keys without the ``oauth:`` prefix when principal is OAuth.
    if principal.startswith("oauth:"):
        bare = principal[len("oauth:") :]
        if bare in table:
            return table[bare]
    return None


def resolve_xai_api_key(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> Tuple[str, str]:
    """Return ``(key, source)`` where source is ``owner_default`` or ``principal``.

    Principal overrides apply only for authenticated OAuth principals. Anonymous
    loopback and static API-key principals keep the owner default (static keys
    already *are* a principal form of auth for the gateway, not per-human BYOK).
    """
    source = os.environ if environ is None else environ
    owner = default_xai_api_key(source)
    active = principal if principal is not None else get_active_principal()
    if not active or principal_kind(active) != "oauth":
        return owner, "owner_default"
    table = load_principal_xai_key_table(source)
    if not table:
        return owner, "owner_default"
    personal = _lookup_principal_key(active, table)
    if personal:
        return personal, "principal"
    return owner, "owner_default"


def effective_xai_api_key(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    key, _src = resolve_xai_api_key(principal=principal, environ=environ)
    return key


def xai_api_key_fingerprint(key: str) -> str:
    """Stable non-secret cache id for a resolved key."""
    material = normalize_xai_api_key(key)
    if not material:
        return "missing"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def principal_xai_status(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Secret-safe status for diagnostics (never includes key material)."""
    key, source = resolve_xai_api_key(principal=principal, environ=environ)
    active = principal if principal is not None else get_active_principal()
    table = load_principal_xai_key_table(environ)
    return {
        "configured": bool(key),
        "source": source,
        "principal_kind": principal_kind(active),
        "principal_override_available": bool(
            active
            and principal_kind(active) == "oauth"
            and _lookup_principal_key(active, table)
        ),
        "owner_default_configured": bool(default_xai_api_key(environ)),
        "principal_map_entries": len(table),
    }
