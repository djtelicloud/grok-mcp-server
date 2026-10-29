"""Fail-closed auth edge for a generic remote MCP deployment.

Local Docker remains loopback-first and credential-free at the gateway layer.
When ``UNIGROK_RUNTIME=cloudrun``, protected requests require either:

1. Control OAuth bearer tokens validated by remote introspection, or
2. Operator-minted **service tokens** (``UNIGROK_SERVICE_TOKENS`` /
   ``UNIGROK_SERVICE_TOKEN_SHA256``) for non-OAuth automation such as
   GitHub Copilot cloud agent / code review.

Provider keys stay server-side and are never accepted as gateway bearer
credentials. Service tokens are not OAuth and never expand authority beyond
this public MCP resource.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from .identity import principal_label

logger = logging.getLogger(__name__)

_LOCAL_TOKEN_ENV = "UNIGROK_LOCAL_MCP_TOKEN"  # noqa: S105 - environment name
_LOCAL_TOKEN_DIGEST_ENV = "UNIGROK_LOCAL_MCP_TOKEN_SHA256"  # noqa: S105
_SERVICE_TOKEN_ENV = "UNIGROK_SERVICE_TOKENS"  # noqa: S105 - environment name
_SERVICE_TOKEN_DIGEST_ENV = "UNIGROK_SERVICE_TOKEN_SHA256"  # noqa: S105
_SERVICE_TOKEN_SCOPES_ENV = "UNIGROK_SERVICE_TOKEN_SCOPES"  # noqa: S105
_AUTH_FAILURE_WINDOW_SECONDS = 60.0
_AUTH_FAILURE_LIMIT = 30
_AUTH_FAILURE_MAX_PEERS = 1_024
_auth_failures: dict[str, list[float]] = {}
_OAUTH_INTROSPECTION_MAX_INFLIGHT = 16
_OAUTH_INTROSPECTION_DEADLINE_SECONDS = 5.0
_OAUTH_INTROSPECTION_MAX_RESPONSE_BYTES = 16_384
_OAUTH_INTROSPECTION_RETRY_AFTER_SECONDS = 1
_OAUTH_NEGATIVE_CACHE_TTL_SECONDS = 15.0
_OAUTH_NEGATIVE_CACHE_MAX_ENTRIES = 2_048
_OAuthCacheKey = tuple[bytes, str]
_OAuthClaims = dict[str, Any]


@dataclass(frozen=True, slots=True)
class OAuthIntrospectionCapacity:
    """Typed temporary failure returned when no outbound lookup slot is available."""

    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class OAuthIntrospectionUnavailable:
    """Typed transient result for Control transport or protocol failures."""

    retry_after_seconds: int


_OAUTH_INTROSPECTION_CAPACITY = OAuthIntrospectionCapacity(
    retry_after_seconds=_OAUTH_INTROSPECTION_RETRY_AFTER_SECONDS
)
_OAUTH_INTROSPECTION_UNAVAILABLE = OAuthIntrospectionUnavailable(
    retry_after_seconds=_OAUTH_INTROSPECTION_RETRY_AFTER_SECONDS
)
_OAuthSharedResult = _OAuthClaims | OAuthIntrospectionUnavailable | None
_OAuthIntrospectionResult = _OAuthSharedResult | OAuthIntrospectionCapacity
_oauth_introspection_state_lock = threading.Lock()
_oauth_introspection_inflight: dict[
    _OAuthCacheKey, asyncio.Future[_OAuthSharedResult]
] = {}
_oauth_negative_cache: OrderedDict[_OAuthCacheKey, float] = OrderedDict()

_FASTMCP_LOCAL_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
    "unigrok",
    "unigrok:*",
    "grok-mcp",
    "grok-mcp:*",
)
_FASTMCP_LOCAL_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)

_PUBLIC_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/.well-known/webmcp",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    }
)
_STATUS_TOOLS = frozenset(
    {
        "grok_mcp_discover_self",
        "grok_mcp_status",
        "list_models",
        "benchmark_status",
    }
)
_OAUTH_SCOPE_RE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


def is_cloudrun_runtime() -> bool:
    return os.environ.get("UNIGROK_RUNTIME", "").strip().lower() == "cloudrun"


def stateless_http_enabled() -> bool:
    return is_cloudrun_runtime()


def _validated_https_url(raw: str) -> str | None:
    value = str(raw or "").strip()
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    normalized_host = host.lower().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith(
        (".localhost", ".local", ".internal")
    ):
        return None
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    return f"https://{parsed.netloc}{parsed.path.rstrip('/')}"


def public_mcp_resource(environ: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    resource = _validated_https_url(source.get("UNIGROK_PUBLIC_MCP_URL", ""))
    if not resource:
        return None
    parsed = urlsplit(resource)
    if not parsed.path:
        return f"{resource}/mcp"
    return resource if parsed.path == "/mcp" else None


def authorization_servers(environ: Mapping[str, str] | None = None) -> list[str]:
    source = os.environ if environ is None else environ
    values = [
        item.strip()
        for item in source.get("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", "").split(",")
        if item.strip()
    ]
    validated = [_validated_https_url(item) for item in values]
    if not values or any(item is None for item in validated):
        return []
    return list(dict.fromkeys(item for item in validated if item is not None))


def introspection_url() -> str | None:
    return _validated_https_url(os.environ.get("UNIGROK_OAUTH_INTROSPECTION_URL", ""))


def oauth_scopes() -> list[str]:
    values = [
        item.strip()
        for item in os.environ.get("UNIGROK_OAUTH_SCOPES", "unigrok:connect").split(",")
        if item.strip()
    ]
    if not values or any(_OAUTH_SCOPE_RE.fullmatch(item) is None for item in values):
        return []
    return list(dict.fromkeys(values))



_DEFAULT_SERVICE_SCOPES = (
    "unigrok:connect",
    "unigrok:invoke",
    "unigrok:review",
    "unigrok:status",
    "unigrok:chat",
)
_SERVICE_TOKEN_MIN = 32
_SERVICE_TOKEN_MAX = 256
_SERVICE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{32,256}$")


class ServiceTokenConfigurationError(RuntimeError):
    """Raised when optional service-token authentication is configured unsafely."""


def service_token_scopes(environ: Mapping[str, str] | None = None) -> list[str]:
    """Return strictly configured scopes, defaulting only when the setting is absent."""
    source = os.environ if environ is None else environ
    if _SERVICE_TOKEN_SCOPES_ENV not in source:
        return list(_DEFAULT_SERVICE_SCOPES)
    raw = source.get(_SERVICE_TOKEN_SCOPES_ENV, "")
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ServiceTokenConfigurationError(
            f"{_SERVICE_TOKEN_SCOPES_ENV} contains a malformed entry"
        )
    groups = raw.split(",")
    if any(not group.strip() for group in groups):
        raise ServiceTokenConfigurationError(
            f"{_SERVICE_TOKEN_SCOPES_ENV} contains a malformed entry"
        )
    values = [scope for group in groups for scope in group.split()]
    if not values or any(_OAUTH_SCOPE_RE.fullmatch(scope) is None for scope in values):
        raise ServiceTokenConfigurationError(
            f"{_SERVICE_TOKEN_SCOPES_ENV} contains a malformed entry"
        )
    if len(set(values)) != len(values):
        raise ServiceTokenConfigurationError(
            f"{_SERVICE_TOKEN_SCOPES_ENV} contains a duplicate entry"
        )
    return values


def service_token_label() -> str:
    raw = os.environ.get("UNIGROK_SERVICE_TOKEN_LABEL", "automation").strip() or "automation"
    # Stable principal segment: keep it URL-safe and short.
    cleaned = re.sub(r"[^A-Za-z0-9._-]{1,64}", "-", raw)[:64].strip("-._")
    return cleaned or "automation"


def _digest_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class LocalMCPTokenConfigurationError(RuntimeError):
    """Raised when optional local MCP authentication is configured unsafely."""


def _strict_config_values(source: Mapping[str, str], name: str) -> list[str]:
    raw = source.get(name, "")
    if raw == "":
        return []
    values = [item.strip() for item in raw.split(",")]
    if not values or any(not item for item in values):
        raise LocalMCPTokenConfigurationError(f"{name} contains a malformed entry")
    return values


def _configured_local_mcp_digests(
    environ: Mapping[str, str] | None = None,
) -> frozenset[bytes]:
    """Parse optional local tokens strictly so configuration can never fail open."""
    source = os.environ if environ is None else environ
    digests: set[bytes] = set()
    for token in _strict_config_values(source, _LOCAL_TOKEN_ENV):
        if _SERVICE_TOKEN_RE.fullmatch(token) is None:
            raise LocalMCPTokenConfigurationError(f"{_LOCAL_TOKEN_ENV} contains a malformed entry")
        digests.add(_digest_token(token))
    for digest_hex in _strict_config_values(source, _LOCAL_TOKEN_DIGEST_ENV):
        normalized = digest_hex.lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise LocalMCPTokenConfigurationError(
                f"{_LOCAL_TOKEN_DIGEST_ENV} contains a malformed entry"
            )
        digests.add(bytes.fromhex(normalized))
    return frozenset(digests)


def validate_local_mcp_token_configuration(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject every nonempty malformed local-token setting without exposing values."""
    _configured_local_mcp_digests(environ)


def local_mcp_token_configured(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    digests = _configured_local_mcp_digests(source)
    runtime = source.get("UNIGROK_RUNTIME", "").strip().lower()
    return runtime != "cloudrun" and bool(digests)


def match_local_mcp_token(
    token: str | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return bounded operator claims for a valid non-hosted local bearer token."""
    source = os.environ if environ is None else environ
    allowed = _configured_local_mcp_digests(source)
    if source.get("UNIGROK_RUNTIME", "").strip().lower() == "cloudrun":
        return None
    if not token or len(token) > _SERVICE_TOKEN_MAX or not allowed:
        return None
    candidate = _digest_token(token)
    matched = False
    for digest in allowed:
        matched = secrets.compare_digest(candidate, digest) or matched
    if not matched:
        return None
    return {
        "active": True,
        "token_type": "local_mcp",
        "scope": " ".join(_DEFAULT_SERVICE_SCOPES),
        "iss": "unigrok:local-mcp-token",
        "sub": "operator",
        "aud": "local-runtime",
        "unigrok_principal": "local:operator",
        "unigrok_auth": "local_mcp_token",
    }


def _strict_service_values(source: Mapping[str, str], name: str) -> list[str]:
    if name not in source:
        return []
    raw = source.get(name, "")
    if raw == "":
        raise ServiceTokenConfigurationError(f"{name} contains a malformed entry")
    values = [item.strip() for item in raw.split(",")]
    if any(not item for item in values):
        raise ServiceTokenConfigurationError(f"{name} contains a malformed entry")
    return values


def _configured_service_digests(
    environ: Mapping[str, str] | None = None,
) -> frozenset[bytes]:
    source = os.environ if environ is None else environ
    digests: set[bytes] = set()
    for token in _strict_service_values(source, _SERVICE_TOKEN_ENV):
        if _SERVICE_TOKEN_RE.fullmatch(token) is None:
            raise ServiceTokenConfigurationError(
                f"{_SERVICE_TOKEN_ENV} contains a malformed entry"
            )
        digest = _digest_token(token)
        if digest in digests:
            raise ServiceTokenConfigurationError(
                f"{_SERVICE_TOKEN_ENV} contains a duplicate entry"
            )
        digests.add(digest)
    for raw_digest in _strict_service_values(source, _SERVICE_TOKEN_DIGEST_ENV):
        digest_hex = raw_digest.lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest_hex) is None:
            raise ServiceTokenConfigurationError(
                f"{_SERVICE_TOKEN_DIGEST_ENV} contains a malformed entry"
            )
        digest = bytes.fromhex(digest_hex)
        if digest in digests:
            raise ServiceTokenConfigurationError(
                f"{_SERVICE_TOKEN_DIGEST_ENV} contains a duplicate entry"
            )
        digests.add(digest)
    return frozenset(digests)


def validate_service_token_configuration(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject malformed service-token credentials or scope policy without exposing values."""
    _configured_service_digests(environ)
    service_token_scopes(environ)


def service_tokens_configured(environ: Mapping[str, str] | None = None) -> bool:
    digests = _configured_service_digests(environ)
    service_token_scopes(environ)
    return bool(digests)


def auth_enforcement_active() -> bool:
    """True when the edge must reject anonymous protected traffic."""
    return bool(introspection_url()) or service_tokens_configured() or local_mcp_token_configured()


def match_service_token(token: str | None) -> dict[str, Any] | None:
    """Return synthetic auth claims for a valid service token, else None."""
    if not token or len(token) > _SERVICE_TOKEN_MAX:
        return None
    allowed = _configured_service_digests()
    if not allowed:
        return None
    candidate = _digest_token(token)
    matched = False
    for digest in allowed:
        if secrets.compare_digest(candidate, digest):
            matched = True
            break
    if not matched:
        return None
    scopes = " ".join(service_token_scopes())
    label = service_token_label()
    resource = public_mcp_resource() or "service-token"
    principal = f"service:{label}"
    return {
        "active": True,
        "token_type": "service",
        "scope": scopes,
        "iss": "unigrok:service-token",
        "sub": label,
        "aud": resource,
        "unigrok_principal": principal,
        "unigrok_auth": "service_token",
    }


def canonical_oauth_principal(issuer: Any, subject: Any) -> str | None:
    if not isinstance(issuer, str) or issuer not in authorization_servers():
        return None
    if not isinstance(subject, str) or not subject or len(subject) > 1_024:
        return None
    if any(ord(char) <= 31 or ord(char) == 127 for char in subject):
        return None
    return (
        "oauth:"
        f"{quote(issuer, safe='-._~')}:"
        f"{quote(subject, safe='-._~')}"
    )


def _normalized_host_for_header(host: str) -> str:
    normalized = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def _origin_from_url(value: str) -> str | None:
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    authority = _normalized_host_for_header(host)
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def fastmcp_allowed_hosts(environ: Mapping[str, str] | None = None) -> list[str]:
    """Return deterministic DNS-rebinding Host patterns for the active runtime."""
    source = os.environ if environ is None else environ
    values = list(_FASTMCP_LOCAL_HOSTS)
    resource = public_mcp_resource(source)
    if resource:
        parsed = urlsplit(resource)
        host = _normalized_host_for_header(str(parsed.hostname))
        values.extend((host, f"{host}:*"))
        if parsed.port is not None:
            values.append(f"{host}:{parsed.port}")
    return list(dict.fromkeys(values))


def fastmcp_allowed_origins(environ: Mapping[str, str] | None = None) -> list[str]:
    """Return deterministic Origin patterns for local and configured hosted clients."""
    source = os.environ if environ is None else environ
    values = list(_FASTMCP_LOCAL_ORIGINS)
    resource = public_mcp_resource(source)
    if resource:
        resource_origin = _origin_from_url(resource.rsplit("/mcp", 1)[0])
        if resource_origin:
            values.append(resource_origin)
    for raw in source.get("UNIGROK_ALLOWED_ORIGINS", "").split(","):
        origin = _origin_from_url(raw.strip().rstrip("/"))
        if origin:
            values.append(origin)
    return list(dict.fromkeys(values))


def transport_security_settings() -> TransportSecuritySettings:
    """Build the FastMCP DNS-rebinding policy for the active runtime."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=fastmcp_allowed_hosts(),
        allowed_origins=fastmcp_allowed_origins(),
    )


def validate_remote_configuration() -> None:
    """Reject malformed local auth or unsafe Cloud Run configuration at startup."""
    validate_local_mcp_token_configuration()
    validate_service_token_configuration()
    if not is_cloudrun_runtime():
        return
    if os.environ.get("UNIGROK_ALLOW_UNAUTHENTICATED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        raise RuntimeError("UNIGROK_ALLOW_UNAUTHENTICATED is forbidden in Cloud Run")
    if not public_mcp_resource():
        raise RuntimeError("Cloud Run requires a valid UNIGROK_PUBLIC_MCP_URL ending in /mcp")
    if not authorization_servers():
        raise RuntimeError("Cloud Run requires valid OAuth authorization servers")
    if not introspection_url():
        raise RuntimeError("Cloud Run requires UNIGROK_OAUTH_INTROSPECTION_URL")
    configured = set(oauth_scopes())
    required = {
        "unigrok:connect",
        "unigrok:invoke",
        "unigrok:review",
        "unigrok:status",
    }
    if not required.issubset(configured):
        raise RuntimeError("Cloud Run OAuth scopes omit a required MCP capability")


def oauth_metadata() -> tuple[dict[str, Any], int, dict[str, str]]:
    resource = public_mcp_resource()
    servers = authorization_servers()
    scopes = oauth_scopes()
    if not resource or not servers or not scopes or not introspection_url():
        return (
            {
                "status": "unavailable",
                "code": "oauth_discovery_not_configured",
                "detail": "The remote OAuth boundary is not fully configured.",
            },
            503,
            {"Cache-Control": "no-store"},
        )
    validation = "remote-introspection"
    if service_tokens_configured():
        validation = "remote-introspection-or-service-token"
    payload: dict[str, Any] = {
        "resource": resource,
        "authorization_servers": servers,
        "scopes_supported": scopes,
        "bearer_methods_supported": ["header"],
        "x_unigrok_authorization_status": "active",
        "x_unigrok_access_token_validation": validation,
    }
    if service_tokens_configured():
        payload["x_unigrok_service_token"] = True
        payload["x_unigrok_service_token_scopes"] = service_token_scopes()
    return (
        payload,
        200,
        {"Cache-Control": "public, max-age=300"},
    )


def _scope_headers(scope: Mapping[str, Any], name: bytes) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in scope.get("headers") or []
        if key.lower() == name
    ]


def _scope_header(scope: Mapping[str, Any], name: bytes) -> str | None:
    values = _scope_headers(scope, name)
    return values[0] if values else None


def _extract_bearer(value: str | None) -> str | None:
    scheme, separator, token = str(value or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token if token and len(token) <= 8_192 else None


def _tool_scope(name: Any) -> str:
    tool = str(name or "")
    if tool == "agent_result":
        # The random 128-bit job id is a capability.  Requiring only connect
        # lets a review-scoped token poll the review job it just created.
        return "unigrok:connect"
    if tool == "review_pull_request":
        return "unigrok:review"
    if tool in _STATUS_TOOLS:
        return "unigrok:status"
    return "unigrok:invoke"


def required_scope(path: str, body: bytes = b"") -> str:
    if path.startswith("/v1"):
        return "unigrok:chat"
    if path != "/mcp":
        return "unigrok:status"
    required = {"unigrok:connect"}
    if not body:
        return "unigrok:connect"
    try:
        document = json.loads(body)
    except (TypeError, ValueError):
        return "unigrok:connect"
    requests = document if isinstance(document, list) else [document]
    for item in requests:
        if not isinstance(item, dict) or item.get("method") != "tools/call":
            continue
        params = item.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        required.add(_tool_scope(name))
    return " ".join(sorted(required))


def _oauth_cache_key(token: str, required: str) -> _OAuthCacheKey:
    return _digest_token(token), required


def _oauth_negative_cache_hit(key: _OAuthCacheKey) -> bool:
    now = time.monotonic()
    with _oauth_introspection_state_lock:
        expires_at = _oauth_negative_cache.get(key)
        if expires_at is None:
            return False
        if expires_at <= now:
            _oauth_negative_cache.pop(key, None)
            return False
        _oauth_negative_cache.move_to_end(key)
        return True


def _cache_negative_oauth_result(key: _OAuthCacheKey) -> None:
    now = time.monotonic()
    expires_at = now + max(0.0, float(_OAUTH_NEGATIVE_CACHE_TTL_SECONDS))
    maximum = max(0, int(_OAUTH_NEGATIVE_CACHE_MAX_ENTRIES))
    with _oauth_introspection_state_lock:
        for cached_key, cached_expiry in tuple(_oauth_negative_cache.items()):
            if cached_expiry <= now:
                _oauth_negative_cache.pop(cached_key, None)
        if maximum == 0 or expires_at <= now:
            return
        _oauth_negative_cache[key] = expires_at
        _oauth_negative_cache.move_to_end(key)
        while len(_oauth_negative_cache) > maximum:
            _oauth_negative_cache.popitem(last=False)


def _begin_oauth_introspection(
    key: _OAuthCacheKey,
) -> tuple[bool, asyncio.Future[_OAuthSharedResult] | None]:
    maximum = max(1, int(_OAUTH_INTROSPECTION_MAX_INFLIGHT))
    loop = asyncio.get_running_loop()
    with _oauth_introspection_state_lock:
        existing = _oauth_introspection_inflight.get(key)
        if existing is not None:
            return False, existing if existing.get_loop() is loop else None
        if len(_oauth_introspection_inflight) >= maximum:
            return False, None
        future: asyncio.Future[_OAuthSharedResult] = loop.create_future()
        _oauth_introspection_inflight[key] = future
        return True, future


def _finish_oauth_introspection(
    key: _OAuthCacheKey,
    future: asyncio.Future[_OAuthSharedResult],
    result: _OAuthSharedResult,
) -> None:
    with _oauth_introspection_state_lock:
        if _oauth_introspection_inflight.get(key) is future:
            _oauth_introspection_inflight.pop(key, None)
        if not future.done():
            future.set_result(result)


async def _fetch_oauth_introspection_payload(
    url: str, token: str, required: str
) -> dict[str, Any] | OAuthIntrospectionUnavailable:
    """Fetch one bounded response without buffering or decompressing untrusted bytes."""
    deadline = min(
        5.0, max(0.001, float(_OAUTH_INTROSPECTION_DEADLINE_SECONDS))
    )
    maximum = min(
        16_384, max(1, int(_OAUTH_INTROSPECTION_MAX_RESPONSE_BYTES))
    )
    try:
        async with asyncio.timeout(deadline):
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "unigrok-public-remote-mcp/1",
                        "Accept-Encoding": "identity",
                    },
                    data={"required_scope": required},
                ) as response:
                    if response.status_code != 200:
                        return _OAUTH_INTROSPECTION_UNAVAILABLE
                    content_encoding = response.headers.get(
                        "content-encoding", ""
                    ).strip().lower()
                    if content_encoding not in {"", "identity"}:
                        return _OAUTH_INTROSPECTION_UNAVAILABLE
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(chunk) > maximum - len(body):
                            return _OAUTH_INTROSPECTION_UNAVAILABLE
                        body.extend(chunk)
                    payload = json.loads(body)
    except (TimeoutError, httpx.HTTPError, TypeError, ValueError):
        return _OAUTH_INTROSPECTION_UNAVAILABLE
    return payload if isinstance(payload, dict) else _OAUTH_INTROSPECTION_UNAVAILABLE


async def introspect_oauth_token(
    token: str, required: str
) -> _OAuthIntrospectionResult:
    url = introspection_url()
    if not url or not token or len(token) > 8_192:
        return None
    cache_key = _oauth_cache_key(token, required)
    if _oauth_negative_cache_hit(cache_key):
        return None
    leader, shared_future = _begin_oauth_introspection(cache_key)
    if shared_future is None:
        return _OAUTH_INTROSPECTION_CAPACITY
    if not leader:
        return await asyncio.shield(shared_future)
    cache_negative = False
    result: _OAuthSharedResult = None
    try:
        payload = await _fetch_oauth_introspection_payload(url, token, required)
        if isinstance(payload, OAuthIntrospectionUnavailable):
            result = payload
            return result
        active = payload.get("active")
        if active is False:
            cache_negative = True
            return None
        if active is not True:
            result = _OAUTH_INTROSPECTION_UNAVAILABLE
            return result
        scopes = payload.get("scope")
        granted = set(scopes.split()) if isinstance(scopes, str) else set()
        if not set(required.split()).issubset(granted):
            cache_negative = True
            return None
        principal = canonical_oauth_principal(payload.get("iss"), payload.get("sub"))
        if principal is None:
            cache_negative = True
            return None
        audience = payload.get("aud")
        if isinstance(audience, str):
            audiences = (audience,)
        elif (
            isinstance(audience, list)
            and len(audience) <= 16
            and all(isinstance(item, str) and len(item) <= 2_048 for item in audience)
        ):
            audiences = tuple(audience)
        else:
            cache_negative = True
            return None
        if public_mcp_resource() not in audiences:
            cache_negative = True
            return None
        # Treat the introspection response as untrusted boundary data.  Only the
        # fields required by the public gateway survive validation; arbitrary
        # provider metadata must not flow into request state or logs.
        result = {
            "active": True,
            "scope": scopes,
            "unigrok_principal": principal,
            "unigrok_auth": "oauth",
        }
        return result
    except asyncio.CancelledError:
        # A disconnected leader must not turn a coalesced follower into a false
        # credential denial. Publish temporary unavailability, then preserve
        # cancellation for the original request.
        result = _OAUTH_INTROSPECTION_UNAVAILABLE
        raise
    finally:
        if cache_negative:
            _cache_negative_oauth_result(cache_key)
        _finish_oauth_introspection(cache_key, shared_future, result)


def _metadata_url(path: str, query_string: bytes) -> str | None:
    resource = public_mcp_resource()
    if not resource or query_string:
        return None
    parsed = urlsplit(resource)
    if path != parsed.path:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource{path}"


def _body_limit() -> int:
    try:
        configured = int(os.environ.get("UNIGROK_REMOTE_BODY_MAX_BYTES", "28000000"))
    except ValueError:
        configured = 28_000_000
    return max(64_000, min(configured, 32_000_000))


def _auth_failure_key(scope: Mapping[str, Any]) -> str:
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        peer = str(client[0] or "").strip()
        if peer:
            return peer
    return "unknown"


def _fresh_auth_failures(key: str, now: float) -> list[float]:
    return [
        stamp
        for stamp in _auth_failures.get(key, ())
        if 0.0 <= now - stamp < _AUTH_FAILURE_WINDOW_SECONDS
    ]


def _auth_failure_limited(scope: Mapping[str, Any]) -> bool:
    key = _auth_failure_key(scope)
    stamps = _fresh_auth_failures(key, time.monotonic())
    if stamps:
        _auth_failures[key] = stamps
    else:
        _auth_failures.pop(key, None)
    return len(stamps) >= _AUTH_FAILURE_LIMIT


def _record_auth_failure(scope: Mapping[str, Any]) -> bool:
    now = time.monotonic()
    key = _auth_failure_key(scope)
    stamps = _fresh_auth_failures(key, now)
    if key not in _auth_failures and len(_auth_failures) >= _AUTH_FAILURE_MAX_PEERS:
        for stale_key in tuple(_auth_failures):
            if not _fresh_auth_failures(stale_key, now):
                _auth_failures.pop(stale_key, None)
        while len(_auth_failures) >= _AUTH_FAILURE_MAX_PEERS:
            _auth_failures.pop(next(iter(_auth_failures)))
    stamps.append(now)
    _auth_failures[key] = stamps
    return len(stamps) >= _AUTH_FAILURE_LIMIT


def _clear_auth_failures(scope: Mapping[str, Any]) -> None:
    _auth_failures.pop(_auth_failure_key(scope), None)


def _static_token_claims(token: str | None) -> tuple[dict[str, Any] | None, str]:
    claims = match_local_mcp_token(token)
    if claims is not None:
        return claims, "local_mcp_token"
    claims = match_service_token(token)
    return claims, "service_token" if claims is not None else "oauth"


async def _reject_oauth_introspection_temporary_failure(
    scope: Mapping[str, Any],
    receive: Any,
    send: Any,
    required: str,
    failure: OAuthIntrospectionCapacity | OAuthIntrospectionUnavailable,
) -> None:
    """Report a temporary Control failure without mislabeling a token invalid."""
    capacity = isinstance(failure, OAuthIntrospectionCapacity)
    reason = "capacity" if capacity else "unavailable"
    logger.warning(
        "oauth introspection_%s path=%s scope=%s",
        reason,
        str(scope.get("path") or ""),
        required,
    )
    response = JSONResponse(
        {"error": f"oauth_introspection_{reason}"}, status_code=503
    )
    response.headers["Retry-After"] = str(max(1, failure.retry_after_seconds))
    await response(scope, receive, send)


class RemoteOAuthMiddleware:
    """Pure-ASGI OAuth enforcement that preserves MCP streaming semantics."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        active = auth_enforcement_active()
        if path in _PUBLIC_PATHS or not active:
            await self.app(scope, receive, send)
            return

        token = _extract_bearer(_scope_header(scope, b"authorization"))
        local_auth_configured = local_mcp_token_configured()
        claims, auth_kind = _static_token_claims(token)
        if claims is not None:
            _clear_auth_failures(scope)
        elif local_auth_configured and introspection_url() is None and _auth_failure_limited(scope):
            response = JSONResponse({"error": "too_many_auth_failures"}, status_code=429)
            await response(scope, receive, send)
            return
        body = b""
        if path == "/mcp" and scope.get("method") == "POST":
            # Authenticate the connection before buffering a potentially large
            # base64 file request. Local/service tokens resolve locally; OAuth uses
            # Control introspection. Scope is re-checked once tools/call is known.
            if claims is None:
                introspection = await introspect_oauth_token(
                    token or "", "unigrok:connect"
                )
                if isinstance(
                    introspection,
                    (OAuthIntrospectionCapacity, OAuthIntrospectionUnavailable),
                ):
                    await _reject_oauth_introspection_temporary_failure(
                        scope, receive, send, "unigrok:connect", introspection
                    )
                    return
                claims = introspection
            if claims is None:
                required = "unigrok:connect"
                logger.warning("oauth access_denied path=%s scope=%s", path, required)
                limited = local_auth_configured and _record_auth_failure(scope)
                response = JSONResponse(
                    {"error": ("too_many_auth_failures" if limited else "unauthorized")},
                    status_code=429 if limited else 401,
                )
                metadata = _metadata_url(path, scope.get("query_string", b""))
                response.headers["WWW-Authenticate"] = (
                    f'Bearer resource_metadata="{metadata}", scope="{required}"'
                    if metadata
                    else f'Bearer scope="{required}"'
                )
                await response(scope, receive, send)
                return
            original_receive = receive
            messages: list[dict[str, Any]] = []
            limit = _body_limit()
            while True:
                message = await receive()
                messages.append(message)
                if message.get("type") == "http.request":
                    body += message.get("body", b"")
                    if len(body) > limit:
                        response = JSONResponse(
                            {"error": "request_too_large"}, status_code=413
                        )
                        await response(scope, receive, send)
                        return
                if message.get("type") != "http.request" or not message.get("more_body"):
                    break
            replay_index = 0

            async def replay_receive() -> dict[str, Any]:
                nonlocal replay_index
                if replay_index < len(messages):
                    message = messages[replay_index]
                    replay_index += 1
                    return message
                return await original_receive()

            receive = replay_receive

        required = required_scope(path, body)
        if claims is not None:
            granted = claims.get("scope")
            granted_scopes = set(granted.split()) if isinstance(granted, str) else set()
            if not set(required.split()).issubset(granted_scopes):
                # Token already authenticated (connect phase or service match) but
                # lacks the tool scope — hard deny, do not re-introspect.
                claims = None
        else:
            claims, auth_kind = _static_token_claims(token)
            if claims is not None:
                granted_scopes = set(str(claims.get("scope") or "").split())
                if not set(required.split()).issubset(granted_scopes):
                    claims = None
            else:
                auth_kind = "oauth"
                introspection = await introspect_oauth_token(token or "", required)
                if isinstance(
                    introspection,
                    (OAuthIntrospectionCapacity, OAuthIntrospectionUnavailable),
                ):
                    await _reject_oauth_introspection_temporary_failure(
                        scope, receive, send, required, introspection
                    )
                    return
                claims = introspection
        if claims is not None:
            if local_auth_configured:
                _clear_auth_failures(scope)
            scope["unigrok.oauth"] = claims
            logger.info(
                "%s access_allowed path=%s scope=%s principal=%s",
                auth_kind,
                path,
                required,
                principal_label(str(claims.get("unigrok_principal") or "")),
            )
            await self.app(scope, receive, send)
            return

        logger.warning("oauth access_denied path=%s scope=%s", path, required)
        limited = local_auth_configured and _record_auth_failure(scope)
        response = JSONResponse(
            {"error": "too_many_auth_failures" if limited else "unauthorized"},
            status_code=429 if limited else 401,
        )
        metadata = _metadata_url(path, scope.get("query_string", b""))
        if metadata:
            response.headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{metadata}", scope="{required}"'
            )
        else:
            response.headers["WWW-Authenticate"] = f'Bearer scope="{required}"'
        await response(scope, receive, send)


def _allowed_origins() -> set[str]:
    return set(fastmcp_allowed_origins())


def _normalized_host_header(value: str | None) -> str | None:
    host_header = str(value or "")
    if (
        not host_header
        or any(ord(char) <= 32 or ord(char) == 127 for char in host_header)
        or "\\" in host_header
    ):
        return None
    try:
        parsed = urlsplit(f"//{host_header}")
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or port == 0
    ):
        return None
    authority = _normalized_host_for_header(host)
    return f"{authority}:{port}" if port is not None else authority


def _host_allowed(host: str | None) -> bool:
    value = _normalized_host_header(host)
    if value is None:
        return False
    for allowed in fastmcp_allowed_hosts():
        if value == allowed:
            return True
        if allowed.endswith(":*") and value.startswith(f"{allowed[:-2]}:"):
            return True
    return False


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    value = _origin_from_url(origin.strip().rstrip("/"))
    if value is None:
        return False
    for allowed in _allowed_origins():
        if value == allowed:
            return True
        if allowed.endswith(":*") and value.startswith(f"{allowed[:-2]}:"):
            return True
    return False


class RemoteOriginMiddleware:
    """Apply Host protection app-wide and reject unsafe browser origins before auth."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            host_headers = _scope_headers(scope, b"host")
            if len(host_headers) != 1 or not _host_allowed(host_headers[0]):
                response = JSONResponse({"error": "host_not_allowed"}, status_code=421)
                await response(scope, receive, send)
                return
            if str(scope.get("path") or "").startswith(("/mcp", "/v1")):
                origin_headers = _scope_headers(scope, b"origin")
                if len(origin_headers) > 1 or not _origin_allowed(
                    origin_headers[0] if origin_headers else None
                ):
                    response = JSONResponse({"error": "origin_not_allowed"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
