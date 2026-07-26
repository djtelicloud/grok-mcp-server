"""Owner-default and optional OAuth-principal-bound xAI credentials."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

from .identity import get_active_principal, principal_kind
from .remote_auth import authorization_servers

_PLACEHOLDER = "your_xai_api_key_here"
_PRINCIPAL_KEYS_ENV = "UNIGROK_PRINCIPAL_XAI_KEYS_JSON"
_MAX_MAP_BYTES = 65_536
_MAX_MAP_ENTRIES = 256
_CANONICAL_PRINCIPAL = re.compile(r"^oauth:[^:]+:[^:]+$")
_GENERATIONS: dict[str, tuple[str, str]] = {}
_GENERATIONS_LOCK = threading.Lock()

# The public runtime accepts one owner-default inference slot. Management keys,
# editor tokens, and factory-specific aliases are never candidates.
_OWNER_INFERENCE_ENV = "XAI_API_KEY"


class PrincipalXAIConfigurationError(ValueError):
    """Secret-safe principal credential configuration failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Principal xAI key configuration is invalid.")


def _normalize_key(value: Any) -> str:
    key = str(value or "").strip()
    return "" if not key or key == _PLACEHOLDER else key


def _canonical_principal_is_configured(
    principal: str, environ: Mapping[str, str]
) -> bool:
    parts = principal.split(":", 2)
    if len(parts) != 3 or parts[0] != "oauth":
        return False
    issuer = unquote(parts[1])
    subject = unquote(parts[2])
    if not issuer or not subject or issuer not in set(authorization_servers(environ)):
        return False
    return principal == (
        "oauth:"
        f"{quote(issuer, safe='-._~')}:"
        f"{quote(subject, safe='-._~')}"
    )


def load_principal_key_table(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    raw = str(source.get(_PRINCIPAL_KEYS_ENV, "") or "").strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > _MAX_MAP_BYTES:
        raise PrincipalXAIConfigurationError("too_large")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise PrincipalXAIConfigurationError("duplicate_principal")
            parsed[key] = value
        return parsed

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError:
        raise PrincipalXAIConfigurationError("invalid_json") from None
    if not isinstance(document, dict):
        raise PrincipalXAIConfigurationError("not_object")
    if len(document) > _MAX_MAP_ENTRIES:
        raise PrincipalXAIConfigurationError("too_many_entries")
    table: dict[str, str] = {}
    for principal, value in document.items():
        if (
            not isinstance(principal, str)
            or len(principal) > 240
            or principal != principal.strip()
            or any(ord(char) <= 31 or ord(char) == 127 for char in principal)
            or _CANONICAL_PRINCIPAL.fullmatch(principal) is None
            or not _canonical_principal_is_configured(principal, source)
        ):
            raise PrincipalXAIConfigurationError("invalid_principal")
        key = _normalize_key(value if isinstance(value, str) else None)
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not key
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise PrincipalXAIConfigurationError("invalid_key")
        table[principal] = key
    return table


def validate_principal_key_configuration() -> None:
    load_principal_key_table()


def _looks_like_inference_key(key: str) -> bool:
    """Accept real xAI inference material; reject Cursor / empty placeholders."""
    if not key:
        return False
    # Unit tests may use short non-xai test keys via XAI_API_KEY only.
    if key.startswith("crsr_"):
        return False
    if "management" in key.lower():
        return False
    return True


def _resolve_owner_inference_key(
    source: Mapping[str, str],
) -> tuple[str, str]:
    """Resolve the public runtime's single owner-default inference key."""
    key = _normalize_key(source.get(_OWNER_INFERENCE_ENV))
    if key and _looks_like_inference_key(key):
        return key, f"owner_default:{_OWNER_INFERENCE_ENV}"
    return "", "owner_default"


def resolve_xai_api_key(
    *,
    principal: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    source = os.environ if environ is None else environ
    active = principal if principal is not None else get_active_principal()
    table = load_principal_key_table(source)
    if active and principal_kind(active) == "oauth" and active in table:
        return table[active], "principal"
    return _resolve_owner_inference_key(source)


def _generation(slot: str, key: str) -> str:
    if not key:
        return "missing"
    with _GENERATIONS_LOCK:
        current = _GENERATIONS.get(slot)
        if current is not None and current[0] == key:
            return current[1]
        generation = secrets.token_hex(16)
        _GENERATIONS[slot] = (key, generation)
        return generation


def resolve_inference_credential() -> tuple[str, str, str]:
    active = get_active_principal()
    key, source = resolve_xai_api_key(principal=active)
    owner_default = source == "owner_default" or source.startswith("owner_default:")
    slot = "owner_default" if owner_default else f"principal:{active}"
    return key, source, _generation(slot, key)


def active_credential_source() -> str:
    try:
        key, source = resolve_xai_api_key()
    except PrincipalXAIConfigurationError:
        return "configuration_error"
    return source if key else "missing"
