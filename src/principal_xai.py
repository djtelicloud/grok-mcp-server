"""Owner-default and optional principal-bound xAI API keys.

Cloud twin law (sponsor Approved):
- Default spend path is the service ``XAI_API_KEY`` (owner Secret Manager).
- Write+ insiders may optionally bind their own key for their OAuth principal.
- Labels like ``X-Client-ID`` never select a key.
- Never log key material.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import threading
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

from src.identity import get_active_principal, principal_kind

_PLACEHOLDER = "your_xai_api_key_here"
_PRINCIPAL_KEYS_ENV = "UNIGROK_PRINCIPAL_XAI_KEYS_JSON"
_MAX_PRINCIPAL_KEY_MAP_BYTES = 65_536
_MAX_PRINCIPAL_KEY_MAP_ENTRIES = 256
_CANONICAL_OAUTH_PRINCIPAL = re.compile(r"^oauth:[^:]+:[^:]+$")
_CREDENTIAL_GENERATIONS: Dict[str, Tuple[str, str]] = {}
_CREDENTIAL_GENERATIONS_LOCK = threading.Lock()


class PrincipalXAIConfigurationError(ValueError):
    """Secret-safe principal-key configuration failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Principal xAI key configuration is invalid.")


def _configured_authorization_servers(source: Mapping[str, str]) -> set[str]:
    """Return the same normalized public issuer set used by HTTP metadata."""
    raw_values = [
        item.strip()
        for item in source.get("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", "").split(",")
        if item.strip()
    ]
    validated: set[str] = set()
    for raw in raw_values:
        if any(ord(char) <= 32 or ord(char) == 127 for char in raw):
            return set()
        try:
            parsed = urlsplit(raw)
            host = parsed.hostname
            parsed.port
        except ValueError:
            return set()
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return set()
        normalized_host = host.lower().rstrip(".")
        if normalized_host == "localhost" or normalized_host.endswith(
            (".localhost", ".local", ".internal")
        ):
            return set()
        try:
            address = ipaddress.ip_address(normalized_host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return set()
        normalized_path = parsed.path.rstrip("/")
        validated.add(f"https://{parsed.netloc}{normalized_path}")
    return validated


def _is_configured_canonical_oauth_principal(
    principal: str, authorization_servers: set[str]
) -> bool:
    parts = principal.split(":", 2)
    if len(parts) != 3 or parts[0] != "oauth":
        return False
    issuer = unquote(parts[1])
    subject = unquote(parts[2])
    if not issuer or not subject or issuer not in authorization_servers:
        return False
    return principal == (
        "oauth:"
        f"{quote(issuer, safe='-._~')}:"
        f"{quote(subject, safe='-._~')}"
    )


def normalize_xai_api_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    return "" if not key or key == _PLACEHOLDER else key


def default_xai_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Owner / service default key (Live cloud twin path)."""
    source = os.environ if environ is None else environ
    return normalize_xai_api_key(source.get("XAI_API_KEY"))


def _parse_principal_key_table(
    raw: str, *, authorization_servers: set[str]
) -> Dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if len(text.encode("utf-8")) > _MAX_PRINCIPAL_KEY_MAP_BYTES:
        raise PrincipalXAIConfigurationError("too_large")

    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        parsed: Dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise PrincipalXAIConfigurationError("duplicate_principal")
            parsed[key] = value
        return parsed

    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError:
        raise PrincipalXAIConfigurationError("invalid_json") from None
    if not isinstance(data, dict):
        raise PrincipalXAIConfigurationError("not_object")
    if len(data) > _MAX_PRINCIPAL_KEY_MAP_ENTRIES:
        raise PrincipalXAIConfigurationError("too_many_entries")
    out: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise PrincipalXAIConfigurationError("invalid_principal")
        norm_key = re.sub(r"[\x00-\x1f\x7f]", "", key).strip()
        if (
            not norm_key
            or len(norm_key) > 240
            or norm_key != key
            or _CANONICAL_OAUTH_PRINCIPAL.fullmatch(norm_key) is None
            or not _is_configured_canonical_oauth_principal(
                norm_key, authorization_servers
            )
        ):
            raise PrincipalXAIConfigurationError("invalid_principal")
        secret = normalize_xai_api_key(value if isinstance(value, str) else None)
        if (
            not secret
            or not isinstance(value, str)
            or value != value.strip()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise PrincipalXAIConfigurationError("invalid_key")
        out[norm_key] = secret
    return out


def load_principal_xai_key_table(
    environ: Mapping[str, str] | None = None,
) -> Dict[str, str]:
    """Load optional principal → key map (never log values)."""
    source = os.environ if environ is None else environ
    raw = source.get(_PRINCIPAL_KEYS_ENV, "")
    if not str(raw or "").strip():
        return {}
    return _parse_principal_key_table(
        raw,
        authorization_servers=_configured_authorization_servers(source),
    )


def _lookup_principal_key(
    principal: str, table: Mapping[str, str]
) -> Optional[str]:
    return table.get(principal)


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
    table = load_principal_xai_key_table(source)
    active = principal if principal is not None else get_active_principal()
    if not active or principal_kind(active) != "oauth":
        return owner, "owner_default"
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


def xai_api_service_configured(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return service-wide API-plane availability without request variance.

    Invalid configured maps fail closed. A valid owner key or at least one
    valid principal entry means the service has an API credential path, even
    when the current request principal does not personally have one.
    """

    source = os.environ if environ is None else environ
    try:
        table = load_principal_xai_key_table(source)
    except PrincipalXAIConfigurationError:
        return False
    return bool(default_xai_api_key(source) or table)


def inference_client_cache_id(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Non-secret cache id for the active inference credential path.

    Uses principal identity + resolution source only. Credential generation is
    tracked separately with a random process-local token and is never exposed.
    """
    _key, _source, cache_id, _generation = resolve_inference_credential(
        principal=principal,
        environ=environ,
    )
    return cache_id


def _inference_client_cache_slot(
    *, active: Optional[str], key: str, source: str
) -> str:
    if not key:
        return "missing"
    if source == "owner_default":
        return "owner_default"
    # Principal-bound path: one cache entry per OAuth principal id.
    return f"principal:{active or 'unknown'}"


def _credential_generation(cache_slot: str, key: str) -> str:
    """Return a random process-local token for the current slot/key pair.

    The token is never derived from credential material. A bounded registry
    retains only the current key per cache slot so equality can detect runtime
    replacement without creating a reusable secret fingerprint.
    """
    if not key:
        return "missing"
    with _CREDENTIAL_GENERATIONS_LOCK:
        current = _CREDENTIAL_GENERATIONS.get(cache_slot)
        if current is not None and current[0] == key:
            return current[1]
        generation = secrets.token_hex(16)
        _CREDENTIAL_GENERATIONS[cache_slot] = (key, generation)
        return generation


def resolve_inference_credential(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> Tuple[str, str, str, str]:
    """Resolve one atomic key/source/cache-slot/rotation-generation tuple."""

    active = principal if principal is not None else get_active_principal()
    key, source = resolve_xai_api_key(principal=active, environ=environ)
    cache_slot = _inference_client_cache_slot(
        active=active,
        key=key,
        source=source,
    )
    return (
        key,
        source,
        cache_slot,
        _credential_generation(cache_slot, key),
    )


def active_xai_credential_source(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return a secret-safe source label for execution receipts."""

    try:
        key, source = resolve_xai_api_key(principal=principal, environ=environ)
    except PrincipalXAIConfigurationError:
        return "configuration_error"
    return source if key else "missing"


def principal_xai_status(
    *,
    principal: Optional[str] = None,
    environ: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Secret-safe status for diagnostics (never includes key material)."""
    env = os.environ if environ is None else environ
    raw_map = str(env.get(_PRINCIPAL_KEYS_ENV, "") or "").strip()
    active = principal if principal is not None else get_active_principal()
    map_error: Optional[str] = None
    try:
        table = load_principal_xai_key_table(env)
        key, source = resolve_xai_api_key(principal=active, environ=env)
    except PrincipalXAIConfigurationError as exc:
        table = {}
        key, source = "", "configuration_error"
        map_error = exc.code
    return {
        "configured": bool(key),
        "source": source,
        "principal_kind": principal_kind(active),
        "principal_override_available": bool(
            active
            and principal_kind(active) == "oauth"
            and _lookup_principal_key(active, table)
        ),
        "owner_default_configured": bool(default_xai_api_key(env)),
        "principal_map_configured": bool(raw_map),
        "principal_map_valid": map_error is None,
        "principal_map_error": map_error,
        "principal_map_entries": len(table),
    }
