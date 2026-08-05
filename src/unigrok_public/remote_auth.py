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
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from starlette.responses import JSONResponse

from .identity import principal_label

logger = logging.getLogger(__name__)

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
_OAUTH_INTROSPECTION_MAX_INFLIGHT = 256
_OAUTH_RETRY_AFTER_SECONDS = "1"
_oauth_introspection_tasks: dict[
    tuple[bytes, str], asyncio.Task[dict[str, Any] | None]
] = {}


class OAuthIntrospectionUnavailable(RuntimeError):
    pass


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


def public_mcp_resource() -> str | None:
    resource = _validated_https_url(os.environ.get("UNIGROK_PUBLIC_MCP_URL", ""))
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


def service_token_scopes() -> list[str]:
    """Scopes granted to every valid service token (default = full MCP capability)."""
    values = [
        item.strip()
        for item in os.environ.get(
            "UNIGROK_SERVICE_TOKEN_SCOPES", " ".join(_DEFAULT_SERVICE_SCOPES)
        ).replace(",", " ").split()
        if item.strip()
    ]
    if not values or any(_OAUTH_SCOPE_RE.fullmatch(item) is None for item in values):
        return list(_DEFAULT_SERVICE_SCOPES)
    return list(dict.fromkeys(values))


def service_token_label() -> str:
    raw = os.environ.get("UNIGROK_SERVICE_TOKEN_LABEL", "automation").strip() or "automation"
    # Stable principal segment: keep it URL-safe and short.
    cleaned = re.sub(r"[^A-Za-z0-9._-]{1,64}", "-", raw)[:64].strip("-._")
    return cleaned or "automation"


def _digest_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _configured_service_digests() -> frozenset[bytes]:
    digests: set[bytes] = set()
    for raw in os.environ.get("UNIGROK_SERVICE_TOKENS", "").split(","):
        token = raw.strip()
        if not token:
            continue
        if _SERVICE_TOKEN_RE.fullmatch(token) is None:
            logger.warning("ignoring malformed UNIGROK_SERVICE_TOKENS entry")
            continue
        digests.add(_digest_token(token))
    for raw in os.environ.get("UNIGROK_SERVICE_TOKEN_SHA256", "").split(","):
        digest_hex = raw.strip().lower()
        if not digest_hex:
            continue
        if re.fullmatch(r"[0-9a-f]{64}", digest_hex) is None:
            logger.warning("ignoring malformed UNIGROK_SERVICE_TOKEN_SHA256 entry")
            continue
        digests.add(bytes.fromhex(digest_hex))
    return frozenset(digests)


def service_tokens_configured() -> bool:
    return bool(_configured_service_digests())


def auth_enforcement_active() -> bool:
    """True when the remote edge must reject anonymous protected traffic."""
    return bool(introspection_url()) or service_tokens_configured()


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
    try:
        issuer.encode("utf-8", "strict")
        subject.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return None
    return (
        "oauth:"
        f"{quote(issuer, safe='-._~')}:"
        f"{quote(subject, safe='-._~')}"
    )


def validate_remote_configuration() -> None:
    """Reject a Cloud Run process before it can accept anonymous traffic."""
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


def _scope_header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


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


def _oauth_introspection_key(token: str, required: str) -> tuple[bytes, str]:
    return hashlib.sha256(token.encode("utf-8", "strict")).digest(), required


def _finish_oauth_introspection_task(
    key: tuple[bytes, str], task: asyncio.Task[dict[str, Any] | None]
) -> None:
    if _oauth_introspection_tasks.get(key) is task:
        _oauth_introspection_tasks.pop(key, None)
    if task.cancelled():
        return
    with contextlib.suppress(Exception):
        task.exception()


async def _perform_oauth_introspection(
    token: str, required: str
) -> dict[str, Any] | None:
    url = introspection_url()
    if not url or not token or len(token) > 8_192:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "unigrok-public-remote-mcp/1",
                },
                data={"required_scope": required},
            )
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise OAuthIntrospectionUnavailable("oauth_control_unreachable") from exc
    if response.status_code in {401, 403}:
        return None
    if response.status_code != 200:
        raise OAuthIntrospectionUnavailable("oauth_control_status")
    if len(response.content) > 16_384:
        raise OAuthIntrospectionUnavailable("oauth_control_response_too_large")
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise OAuthIntrospectionUnavailable("oauth_control_invalid_json") from exc
    if not isinstance(payload, dict):
        raise OAuthIntrospectionUnavailable("oauth_control_invalid_payload")
    active = payload.get("active")
    if active is False:
        return None
    if active is not True:
        raise OAuthIntrospectionUnavailable("oauth_control_missing_active")
    scopes = payload.get("scope")
    granted = set(scopes.split()) if isinstance(scopes, str) else set()
    if not set(required.split()).issubset(granted):
        return None
    principal = canonical_oauth_principal(payload.get("iss"), payload.get("sub"))
    if principal is None:
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
        return None
    if public_mcp_resource() not in audiences:
        return None
    return {
        "active": True,
        "scope": scopes,
        "unigrok_principal": principal,
        "unigrok_auth": "oauth",
    }


async def introspect_oauth_token(token: str, required: str) -> dict[str, Any] | None:
    if not token or len(token) > 8_192:
        return None
    key = _oauth_introspection_key(token, required)
    task = _oauth_introspection_tasks.get(key)
    if task is None:
        if len(_oauth_introspection_tasks) >= _OAUTH_INTROSPECTION_MAX_INFLIGHT:
            raise OAuthIntrospectionUnavailable("oauth_control_capacity")
        task = asyncio.create_task(_perform_oauth_introspection(token, required))
        _oauth_introspection_tasks[key] = task
        task.add_done_callback(
            lambda completed, task_key=key: _finish_oauth_introspection_task(
                task_key, completed
            )
        )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except OAuthIntrospectionUnavailable:
        raise
    except Exception as exc:
        raise OAuthIntrospectionUnavailable("oauth_control_failure") from exc


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


def _oauth_unavailable_response() -> JSONResponse:
    return JSONResponse(
        {"error": "authorization_service_unavailable"},
        status_code=503,
        headers={
            "Cache-Control": "no-store",
            "Retry-After": _OAUTH_RETRY_AFTER_SECONDS,
        },
    )


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
        claims: dict[str, Any] | None = match_service_token(token)
        auth_kind = "service_token" if claims is not None else "oauth"
        body = b""
        if path == "/mcp" and scope.get("method") == "POST":
            # Authenticate the connection before buffering a potentially large
            # base64 file request. Service tokens resolve locally; OAuth uses
            # Control introspection. Scope is re-checked once tools/call is known.
            if claims is None:
                try:
                    claims = await introspect_oauth_token(
                        token or "", "unigrok:connect"
                    )
                except OAuthIntrospectionUnavailable:
                    logger.warning(
                        "oauth control_unavailable path=%s scope=%s",
                        path,
                        "unigrok:connect",
                    )
                    await _oauth_unavailable_response()(scope, receive, send)
                    return
            if claims is None:
                required = "unigrok:connect"
                logger.warning("oauth access_denied path=%s scope=%s", path, required)
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
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
            claims = match_service_token(token)
            if claims is not None:
                auth_kind = "service_token"
                granted_scopes = set(str(claims.get("scope") or "").split())
                if not set(required.split()).issubset(granted_scopes):
                    claims = None
            else:
                auth_kind = "oauth"
                try:
                    claims = await introspect_oauth_token(token or "", required)
                except OAuthIntrospectionUnavailable:
                    logger.warning(
                        "oauth control_unavailable path=%s scope=%s", path, required
                    )
                    await _oauth_unavailable_response()(scope, receive, send)
                    return
        if claims is not None:
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
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        metadata = _metadata_url(path, scope.get("query_string", b""))
        if metadata:
            response.headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{metadata}", scope="{required}"'
            )
        else:
            response.headers["WWW-Authenticate"] = f'Bearer scope="{required}"'
        await response(scope, receive, send)


def _allowed_origins() -> set[str]:
    return {
        item.strip().rstrip("/")
        for item in os.environ.get("UNIGROK_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    value = origin.strip().rstrip("/")
    if value in _allowed_origins():
        return True
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


class RemoteOriginMiddleware:
    """Reject browser DNS-rebinding attempts before OAuth introspection."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and str(scope.get("path") or "").startswith(("/mcp", "/v1"))
            and not _origin_allowed(_scope_header(scope, b"origin"))
        ):
            response = JSONResponse({"error": "origin_not_allowed"}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
