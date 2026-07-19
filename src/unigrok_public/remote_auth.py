"""Fail-closed OAuth edge for the owner-operated remote MCP deployment.

Local Docker remains loopback-first and credential-free at the gateway layer.
When ``UNIGROK_RUNTIME=cloudrun``, every non-probe request is authenticated by
the existing Control service through token introspection.  Provider keys stay
server-side and are never accepted as gateway bearer credentials.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
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
    return (
        {
            "resource": resource,
            "authorization_servers": servers,
            "scopes_supported": scopes,
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://grokmcp.org/",
            "x_unigrok_authorization_status": "active",
            "x_unigrok_access_token_validation": "remote-introspection",
        },
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


async def introspect_oauth_token(token: str, required: str) -> dict[str, Any] | None:
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
        if response.status_code != 200 or len(response.content) > 16_384:
            return None
        payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("active") is not True:
        return None
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
    claims = dict(payload)
    claims["unigrok_principal"] = principal
    return claims


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


class RemoteOAuthMiddleware:
    """Pure-ASGI OAuth enforcement that preserves MCP streaming semantics."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        active = bool(introspection_url())
        if path in _PUBLIC_PATHS or not active:
            await self.app(scope, receive, send)
            return

        token = _extract_bearer(_scope_header(scope, b"authorization"))
        claims: dict[str, Any] | None = None
        body = b""
        if path == "/mcp" and scope.get("method") == "POST":
            # Authenticate the connection before buffering a potentially large
            # base64 file request. The returned token scope is then checked
            # locally once the exact tools/call capability is known.
            claims = await introspect_oauth_token(token or "", "unigrok:connect")
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
                claims = None
        else:
            claims = await introspect_oauth_token(token or "", required)
        if claims is not None:
            scope["unigrok.oauth"] = claims
            logger.info(
                "oauth access_allowed path=%s scope=%s principal=%s",
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
