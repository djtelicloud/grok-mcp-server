import asyncio
import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional
from urllib.parse import urlsplit
from .models.results import AgentResult
from .metrics import build_metrics_snapshot, fetch_provider_api_usage

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .utils import (
    FALLBACK_XAI_LANGUAGE_MODELS,
    CLI_AUTH_SETUP_COMMAND,
    MetaLayer,
    PathResolver,
    close_xai_client,
    discover_xai_api_models,
    get_circuit_breaker_state,
    get_routing_advisor,
    get_runtime_stats,
    get_unigrok_runtime,
    get_request_id,
    get_xai_client,
    grok_cli_available,
    grok_cli_plane_status,
    is_cloudrun_runtime,
    new_request_id,
    normalize_caller,
    reset_active_caller,
    reset_request_id,
    redact_secrets,
    run_blocking,
    run_agent_turn,
    set_active_caller,
    set_request_id,
    store,
    telemetry_row_caller,
    _ACTIVE_CLIENT_ID,
    _ACTIVE_SESSION_ID,
    scoped_session,
)


UNIGROK_AGENT_MODEL = "unigrok-agent"
XAI_BASE_URL = "https://api.x.ai/v1"
FALLBACK_XAI_MODELS = FALLBACK_XAI_LANGUAGE_MODELS

logger = logging.getLogger("GrokMCP")


MODE_DIAL_PORTS: Dict[int, Literal["auto", "fast", "reasoning", "thinking", "research"]] = {
    2886: "auto",       # AUTO
    3278: "fast",       # FAST
    7327: "reasoning",  # REAS
    8465: "thinking",   # THNK
    7724: "research",   # RSCH
}
_ACTIVE_MODE_DIAL: contextvars.ContextVar[Optional[tuple[int, str]]] = contextvars.ContextVar(
    "unigrok_active_mode_dial", default=None
)


def _json_error(message: str, status_code: int = 400, code: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": code,
                "param": None,
                "code": code,
            }
        },
        status_code=status_code,
    )


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _max_http_body_bytes() -> int:
    return _bounded_env_int(
        "UNIGROK_MAX_REQUEST_BODY_BYTES", 2 * 1024 * 1024, 16 * 1024, 16 * 1024 * 1024
    )


def _max_chat_messages() -> int:
    return _bounded_env_int("UNIGROK_MAX_MESSAGES", 100, 1, 1000)


def _max_message_chars() -> int:
    return _bounded_env_int("UNIGROK_MAX_MESSAGE_CHARS", 64_000, 256, 1_000_000)


def _max_total_message_chars() -> int:
    return _bounded_env_int(
        "UNIGROK_MAX_TOTAL_MESSAGE_CHARS", 500_000, 1_024, 4_000_000
    )


def _request_error_message(default: str) -> str:
    request_id = get_request_id()
    return f"{default} Request ID: {request_id}." if request_id else default


def _message_content_size(content: Any) -> int:
    try:
        return len(json.dumps(content, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(content or ""))


def _validate_chat_payload(payload: Any) -> Optional[JSONResponse]:
    if not isinstance(payload, dict):
        return _json_error("Request body must be a JSON object.")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return _json_error("Field 'messages' must be an array.", status_code=400)
    if len(messages) > _max_chat_messages():
        return _json_error(
            _request_error_message(f"Too many messages; maximum is {_max_chat_messages()}"),
            status_code=413,
            code="request_too_large",
        )
    total_chars = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            return _json_error(f"Message at index {index} must be an object.")
        content_size = _message_content_size(message.get("content", ""))
        if content_size > _max_message_chars():
            return _json_error(
                _request_error_message(
                    f"Message at index {index} exceeds the {_max_message_chars()} character limit"
                ),
                status_code=413,
                code="request_too_large",
            )
        total_chars += content_size
    if total_chars > _max_total_message_chars():
        return _json_error(
            _request_error_message(
                f"Message content exceeds the {_max_total_message_chars()} character limit"
            ),
            status_code=413,
            code="request_too_large",
        )
    return None


def _api_keys() -> set[str]:
    raw = os.environ.get("UNIGROK_API_KEYS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _auth_is_active() -> bool:
    return is_cloudrun_runtime() or bool(_api_keys())


def _allow_unauthenticated() -> bool:
    return os.environ.get("UNIGROK_ALLOW_UNAUTHENTICATED", "").lower() in ("1", "true", "yes")


def _trusted_loopback_proxy() -> bool:
    """Whether a deployment explicitly declares loopback-only publication.

    Docker Compose binds the application inside the container to 0.0.0.0,
    while publishing the host port only on 127.0.0.1. The application cannot
    inspect that host-side port mapping, so Compose sets this declaration.
    It is intentionally separate from UNIGROK_ALLOW_UNAUTHENTICATED: when
    client keys are configured, normal bearer auth remains active.
    """
    return (
        get_unigrok_runtime() == "http"
        and os.environ.get("UNIGROK_TRUSTED_LOOPBACK_PROXY", "").lower()
        in ("1", "true", "yes")
    )


def _is_loopback_bind_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    scheme, _, token = (auth_header or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _tokens_match(candidate: str, expected: str) -> bool:
    # hmac.compare_digest raises TypeError on non-ASCII str inputs, so a
    # hostile bearer token with any byte >= 0x80 would crash the middleware
    # into a 500. Compare bytes instead for a clean constant-time 401.
    #
    # The candidate is the latin-1 decode of the raw header bytes, so its
    # latin-1 re-encode recovers the exact wire bytes; checking those AND the
    # UTF-8 text form against the configured key accepts clients that send a
    # non-ASCII key as UTF-8 (curl, httpx) or as latin-1 alike.
    expected_bytes = expected.encode("utf-8", "surrogateescape")
    try:
        wire_bytes = candidate.encode("latin-1")
    except UnicodeEncodeError:
        wire_bytes = b""
    text_bytes = candidate.encode("utf-8", "surrogateescape")
    return hmac.compare_digest(wire_bytes, expected_bytes) or hmac.compare_digest(text_bytes, expected_bytes)


def _token_is_allowed(token: Optional[str]) -> bool:
    if not token:
        return False
    xai_key = os.environ.get("XAI_API_KEY", "")
    # The upstream xAI key is never a valid client credential: leaking it into
    # a client config must not grant gateway access.
    if xai_key and _tokens_match(token, xai_key):
        return False
    return any(_tokens_match(token, key) for key in _api_keys())


def _scope_header(scope: Dict[str, Any], name: bytes) -> Optional[str]:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _mode_dials_enabled() -> bool:
    return os.environ.get("UNIGROK_MODE_DIALS", "").strip().lower() in ("1", "true", "yes")


def _host_port(scope: Dict[str, Any]) -> Optional[int]:
    host = (_scope_header(scope, b"host") or "").strip()
    if not host:
        return None
    try:
        return urlsplit(f"//{host}").port
    except ValueError:
        return None


def _mode_dial_for_scope(scope: Dict[str, Any]) -> Optional[tuple[int, str]]:
    if not _mode_dials_enabled():
        return None
    port = _host_port(scope)
    mode = MODE_DIAL_PORTS.get(port) if port is not None else None
    return (port, mode) if port is not None and mode is not None else None


class ModeDialContextMiddleware:
    """Bind an optional phoneword-port default to this request.

    Docker preserves the caller's original ``Host`` port when several host
    ports map to the same internal listener. The dial is only a default:
    ``agent(mode=...)`` remains authoritative.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _ACTIVE_MODE_DIAL.set(_mode_dial_for_scope(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _ACTIVE_MODE_DIAL.reset(token)


# Health probes stay reachable without credentials so load balancers and
# uptime checks work before any key is provisioned.
_AUTH_EXEMPT_PATHS = ("/healthz", "/readyz", "/runtimez")
_AUTH_EXEMPT_PREFIXES = ("/ui", "/.well-known", "/docs")


class GatewayAuthMiddleware:
    """Static bearer auth as pure ASGI middleware.

    Deliberately NOT Starlette's BaseHTTPMiddleware: its response-buffering
    wrapper is known to interfere with SSE client disconnects on the
    streamable-HTTP /mcp mount.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _AUTH_EXEMPT_PATHS or path.startswith(_AUTH_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        if not _auth_is_active() or _allow_unauthenticated():
            await self.app(scope, receive, send)
            return
        if _token_is_allowed(_extract_bearer_token(_scope_header(scope, b"authorization"))):
            await self.app(scope, receive, send)
            return
        response = _json_error("Unauthorized", status_code=401, code="unauthorized")
        # RFC 6750: advertise the expected auth scheme on every 401.
        response.headers["WWW-Authenticate"] = "Bearer"
        await response(scope, receive, send)


_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def _allowed_origins() -> set[str]:
    raw = os.environ.get("UNIGROK_ALLOWED_ORIGINS", "")
    return {part.strip().rstrip("/") for part in raw.split(",") if part.strip()}


def _origin_is_allowed(origin: Optional[str]) -> bool:
    # No Origin header means a non-browser client; the DNS-rebinding attack
    # this guards against requires a browser, so those are allowed through.
    if not origin:
        return True
    origin = origin.strip()
    if origin.rstrip("/") in _allowed_origins():
        return True
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


# Every surface that can reach the agent backend is origin-guarded; health
# probes stay open (probes send no Origin, so they pass regardless).
_ORIGIN_GUARDED_PREFIXES = ("/mcp", "/v1")


class MCPOriginMiddleware:
    """Origin validation on /mcp and /v1 (MCP-spec DNS-rebinding protection).

    /v1/chat/completions reaches the same agent backend as /mcp, so a rebound
    browser page must not be able to drive it either.

    Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware.
    Loopback origins and the UNIGROK_ALLOWED_ORIGINS allowlist pass; any other
    browser origin is rejected with 403.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith(_ORIGIN_GUARDED_PREFIXES):
            if not _origin_is_allowed(_scope_header(scope, b"origin")):
                response = _json_error("Origin not allowed.", status_code=403, code="forbidden")
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _caller_key_alias(token: Optional[str]) -> Optional[str]:
    """Stable, non-reversible alias for the configured API key a bearer token
    matched: 'key-' + the first 8 hex chars of the key's SHA-256. Keeps
    per-key attribution without ever echoing key material into telemetry.
    None when the token matches no configured key."""
    if not token:
        return None
    for key in _api_keys():
        if _tokens_match(token, key):
            digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:8]
            return f"key-{digest}"
    return None


def _derive_client_id(scope: Dict[str, Any]) -> Optional[str]:
    """The optional X-Client-ID header: an IDE self-identifying (vscode,
    claude, codex, antigravity, ...). Distinct from X-Caller in that it also
    scopes session names, so each IDE gets its own conversation namespace."""
    return normalize_caller(_scope_header(scope, b"x-client-id"))


def _derive_http_caller(scope: Dict[str, Any]) -> str:
    """Caller identity for one gateway request: X-Client-ID wins (IDE
    identity, also used for session scoping), then the X-Caller header (any
    agent can self-identify), else the matched auth-key alias as
    'http:key-<sha8>', else 'http:anon'."""
    client_id = _derive_client_id(scope)
    if client_id:
        return client_id
    explicit = normalize_caller(_scope_header(scope, b"x-caller"))
    if explicit:
        return explicit
    alias = _caller_key_alias(_extract_bearer_token(_scope_header(scope, b"authorization")))
    return f"http:{alias}" if alias else "http:anon"


def _scoped_session(session: Optional[str]) -> Optional[str]:
    """Prefix an explicit session name with the requesting client id so each
    IDE keeps its own history ('vscode:main'). No client id, or no session,
    leaves the name untouched."""
    return scoped_session(session)


def _derive_session_id(scope: Dict[str, Any]) -> Optional[str]:
    return normalize_caller(_scope_header(scope, b"x-session-id"))


class CallerContextMiddleware:
    """Binds the request's caller identity to the current async context so
    run_agent_turn/orchestrate attribute telemetry, session metadata, and
    per-caller budgets without threading a parameter through every route —
    including the /mcp mount, whose stateless server task is spawned from the
    request context and therefore inherits the contextvar.

    Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware;
    innermost so origin and auth checks have already passed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = set_active_caller(_derive_http_caller(scope))
        client_token = _ACTIVE_CLIENT_ID.set(_derive_client_id(scope))
        session_token = _ACTIVE_SESSION_ID.set(_derive_session_id(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _ACTIVE_SESSION_ID.reset(session_token)
            _ACTIVE_CLIENT_ID.reset(client_token)
            reset_active_caller(token)


# W3C traceparent (https://www.w3.org/TR/trace-context/):
# version "-" trace-id(32 lowercase hex) "-" parent-id(16 hex) "-" flags(2 hex).
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def _request_id_from_traceparent(header: Optional[str]) -> Optional[str]:
    """The trace-id of a well-formed incoming traceparent header, so gateway
    logs/telemetry correlate with the caller's own tracing. Malformed,
    absent, or all-zero (the spec's invalid sentinel) -> None."""
    if not header:
        return None
    match = _TRACEPARENT_RE.match(header.strip().lower())
    if not match:
        return None
    trace_id = match.group(1)
    if trace_id == "0" * 32:
        return None
    return trace_id


class RequestIdMiddleware:
    """Per-request correlation id as pure ASGI middleware (same SSE-disconnect
    tombstone as the other gateway middleware).

    Outermost of the stack so even origin/auth rejections carry the id: binds
    the incoming traceparent's trace-id (or a fresh short id) to the
    request-id contextvar — run_agent_turn/orchestrate respect the inherited
    value, and the logging filter stamps it on every line — and echoes it
    back as an X-Request-Id header on every response, /mcp mount included.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = (
            _request_id_from_traceparent(_scope_header(scope, b"traceparent"))
            or new_request_id()
        )
        token = set_request_id(request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies before Starlette buffers or parses them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = _max_http_body_bytes()
        content_length = _scope_header(scope, b"content-length")
        try:
            declared_length = int(content_length) if content_length else None
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > limit:
            response = _json_error(
                _request_error_message("Request body exceeds the configured size limit"),
                status_code=413,
                code="request_too_large",
            )
            await response(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise ValueError("request body limit exceeded")
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except ValueError as exc:
            if str(exc) != "request body limit exceeded" or response_started:
                raise
            response = _json_error(
                _request_error_message("Request body exceeds the configured size limit"),
                status_code=413,
                code="request_too_large",
            )
            await response(scope, receive, send)


class CSPMiddleware:
    """ASGI middleware that injects a strict Content-Security-Policy (CSP) header
    into all HTTP responses.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                csp_val = (
                    "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
                    "script-src 'self'; "
                    "frame-ancestors 'none';"
                )
                headers.append((b"content-security-policy", csp_val.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_csp)


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy"})


async def readyz(_: Request) -> JSONResponse:
    # The probe is auth-exempt, so the body stays boolean-only: exception text
    # (absolute paths, sqlite errors) is logged server-side, never disclosed.
    try:
        cli_plane = await run_blocking(
            grok_cli_plane_status,
            timeout_sec=5.0,
            timeout=6.0,
        )
    except Exception as exc:
        logger.warning(f"readyz CLI-plane probe failed: {exc}")
        cli_plane = {"ready": False}
    checks: Dict[str, bool] = {
        "model_auth": bool(os.environ.get("XAI_API_KEY", "").strip())
        or bool(cli_plane.get("ready")),
        "state_dir_writable": False,
        "database": False,
    }
    try:
        state_dir = PathResolver.get_state_base_dir() or PathResolver.get_service_root()
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".unigrok-ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["state_dir_writable"] = True
    except Exception as exc:
        logger.warning(f"readyz state-dir probe failed: {exc}")

    try:
        await store._ensure_initialized()
        checks["database"] = True
    except Exception as exc:
        logger.warning(f"readyz database probe failed: {exc}")

    status = "ready" if all(checks.values()) else "not_ready"
    return JSONResponse({"status": status, "checks": checks}, status_code=200 if status == "ready" else 503)


async def runtimez(request: Request) -> JSONResponse:
    try:
        cli_plane = await run_blocking(
            grok_cli_plane_status,
            timeout_sec=5.0,
            timeout=6.0,
        )
    except Exception:
        cli_plane = {
            "state": "unreachable",
            "ready": False,
            "binary": grok_cli_available(),
            "auth": "probe_failed",
            "setup_command": CLI_AUTH_SETUP_COMMAND,
        }
    request_dial = _mode_dial_for_scope(request.scope)
    return JSONResponse(
        {
            "runtime": get_unigrok_runtime(),
            "transport": "http",
            "service": {
                "mode": "contributor" if PathResolver.contributor_mode() else "stable",
                "workspace_attached": PathResolver.get_workspace_root() is not None,
                "requires_project_files": False,
            },
            "mode_dials": {
                "enabled": _mode_dials_enabled(),
                "ports": {str(port): mode for port, mode in MODE_DIAL_PORTS.items()},
                "precedence": "explicit mode > dialed port > auto",
                "request_dial": (
                    {"port": request_dial[0], "default_mode": request_dial[1]}
                    if request_dial
                    else None
                ),
            },
            "api_plane": {
                "xai_api_key": bool(os.environ.get("XAI_API_KEY", "").strip()),
            },
            "gateway_auth": {
                "enabled": _auth_is_active() and not _allow_unauthenticated(),
                "client_tokens_configured": bool(_api_keys()),
            },
            "cli_plane": {
                "binary": bool(cli_plane["binary"]),
                "state": cli_plane["state"],
                "ready": bool(cli_plane["ready"]),
                "auth": cli_plane["auth"],
                "setup_command": cli_plane["setup_command"],
            },
        }
    )


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _aggregate_telemetry_planes(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-plane request counts, success rate, avg/p95 latency, and cost
    totals over the telemetry table rows."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        plane = str(row.get("chosen_plane") or "unknown")
        grouped.setdefault(plane, []).append(row)
    planes: Dict[str, Dict[str, Any]] = {}
    for plane, entries in sorted(grouped.items()):
        latencies = sorted(
            float(entry["latency"]) for entry in entries if entry.get("latency") is not None
        )
        successes = sum(1 for entry in entries if entry.get("success") == 1)
        planes[plane] = {
            "requests": len(entries),
            "success_rate": round(successes / len(entries), 4) if entries else 0.0,
            "avg_latency_sec": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
            "p95_latency_sec": round(_percentile(latencies, 0.95), 4),
            "total_cost_usd": round(
                sum(float(entry["cost"]) for entry in entries if entry.get("cost") is not None), 6
            ),
        }
    return planes


# Per-caller /metrics breakdown stays bounded no matter how many distinct
# caller strings accumulate (X-Caller is free text).
_METRICS_TOP_CALLERS = 20


def _aggregate_telemetry_callers(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-caller request counts, success rate, and cost totals over the
    telemetry rows. Only attributed rows count — pre-v8 rows and anonymous
    traffic carry no caller metadata — and the result is bounded to the
    busiest _METRICS_TOP_CALLERS callers."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        caller = telemetry_row_caller(row)
        if not caller:
            continue
        grouped.setdefault(caller, []).append(row)
    ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    callers: Dict[str, Dict[str, Any]] = {}
    for caller, entries in ranked[:_METRICS_TOP_CALLERS]:
        successes = sum(1 for entry in entries if entry.get("success") == 1)
        callers[caller] = {
            "requests": len(entries),
            "success_rate": round(successes / len(entries), 4) if entries else 0.0,
            "total_cost_usd": round(
                sum(float(entry["cost"]) for entry in entries if entry.get("cost") is not None), 6
            ),
        }
    return callers


def _prom_escape(value: Any) -> str:
    """Escape a label value per the Prometheus text exposition format."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_labels(labels: Dict[str, Any]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{name}="{_prom_escape(value)}"' for name, value in labels.items())
    return "{" + inner + "}"


def _render_prometheus_metrics(snapshot: Dict[str, Any]) -> str:
    """Render the /metrics JSON snapshot as Prometheus text exposition 0.0.4.

    Stdlib string building by design — no prometheus_client dependency. Each
    family gets HELP/TYPE lines; series are labeled by plane, caller, or
    model. The routing advisor's nested view is flattened to the numeric
    series that map onto the format (per-model success-rate/sample gauges
    and the borderline planning-preference flag); the full nested view stays
    on the JSON default.
    """
    lines: List[str] = []

    def family(name: str, kind: str, help_text: str, series: List[tuple]):
        if not series:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in series:
            lines.append(f"{name}{_prom_labels(labels)} {value}")

    planes: Dict[str, Dict[str, Any]] = snapshot.get("planes") or {}
    family(
        "unigrok_plane_requests_total", "counter",
        "Requests recorded in telemetry, by execution plane.",
        [({"plane": plane}, entry.get("requests", 0)) for plane, entry in planes.items()],
    )
    family(
        "unigrok_plane_success_rate", "gauge",
        "Success rate over recorded telemetry, by execution plane.",
        [({"plane": plane}, entry.get("success_rate", 0.0)) for plane, entry in planes.items()],
    )
    family(
        "unigrok_plane_avg_latency_seconds", "gauge",
        "Average request latency in seconds, by execution plane.",
        [({"plane": plane}, entry.get("avg_latency_sec", 0.0)) for plane, entry in planes.items()],
    )
    family(
        "unigrok_plane_p95_latency_seconds", "gauge",
        "95th percentile request latency in seconds, by execution plane.",
        [({"plane": plane}, entry.get("p95_latency_sec", 0.0)) for plane, entry in planes.items()],
    )
    family(
        "unigrok_plane_cost_usd_total", "counter",
        "Total recorded cost in USD, by execution plane.",
        [({"plane": plane}, entry.get("total_cost_usd", 0.0)) for plane, entry in planes.items()],
    )

    callers: Dict[str, Dict[str, Any]] = snapshot.get("callers") or {}
    family(
        "unigrok_caller_requests_total", "counter",
        "Attributed requests, by caller identity (bounded to the busiest callers).",
        [({"caller": caller}, entry.get("requests", 0)) for caller, entry in callers.items()],
    )
    family(
        "unigrok_caller_success_rate", "gauge",
        "Success rate of attributed requests, by caller identity.",
        [({"caller": caller}, entry.get("success_rate", 0.0)) for caller, entry in callers.items()],
    )
    family(
        "unigrok_caller_cost_usd_total", "counter",
        "Total recorded cost in USD, by caller identity.",
        [({"caller": caller}, entry.get("total_cost_usd", 0.0)) for caller, entry in callers.items()],
    )

    runtime: Dict[str, Any] = snapshot.get("runtime") or {}
    family(
        "unigrok_timed_threads_in_flight", "gauge",
        "Dedicated timed SDK-bridge threads currently in flight.",
        [({}, runtime.get("timed_threads_in_flight", 0))],
    )
    family(
        "unigrok_timed_threads_peak", "gauge",
        "Peak concurrent timed SDK-bridge threads since process start.",
        [({}, runtime.get("timed_threads_peak", 0))],
    )

    breakers: Dict[str, Dict[str, Any]] = snapshot.get("circuit_breakers") or {}
    family(
        "unigrok_circuit_breaker_open", "gauge",
        "1 while the per-model circuit breaker is open, by model.",
        [({"model": model}, 1 if entry.get("open") else 0) for model, entry in breakers.items()],
    )
    family(
        "unigrok_circuit_breaker_consecutive_failures", "gauge",
        "Consecutive upstream failures counted toward the breaker, by model.",
        [({"model": model}, entry.get("consecutive_failures", 0)) for model, entry in breakers.items()],
    )
    family(
        "unigrok_circuit_breaker_trips_total", "counter",
        "Times the circuit breaker has opened since process start, by model.",
        [({"model": model}, entry.get("trips", 0)) for model, entry in breakers.items()],
    )

    advisor: Optional[Dict[str, Any]] = snapshot.get("routing_advisor")
    if isinstance(advisor, dict):
        model_series_rate: List[tuple] = []
        model_series_samples: List[tuple] = []
        for role in ("planning", "coding"):
            model = advisor.get(f"{role}_model")
            view = advisor.get(role)
            if model and isinstance(view, dict):
                model_series_rate.append(({"model": model}, view.get("success_rate", 0.0)))
                model_series_samples.append(({"model": model}, view.get("samples", 0)))
        family(
            "unigrok_routing_model_success_rate", "gauge",
            "Routing advisor's observed success rate, by model.",
            model_series_rate,
        )
        family(
            "unigrok_routing_model_samples", "gauge",
            "Samples behind the routing advisor's view, by model.",
            model_series_samples,
        )
        family(
            "unigrok_routing_prefers_planning", "gauge",
            "1 when borderline prompts currently route to the planning model.",
            [({}, 1 if advisor.get("borderline_choice") == "planning" else 0)],
        )

        # Task-memory RAG (UNIGROK_TASK_RAG): all families UNLABELED — the
        # mode string and nested detail stay JSON-only like the advisor's
        # nested view. `le` below is a histogram mechanic, not an identity
        # label (fixed cardinality of 6 series).
        task_rag: Optional[Dict[str, Any]] = advisor.get("task_rag")
        if isinstance(task_rag, dict):
            ready = task_rag.get("ready")
            family(
                "unigrok_task_rag_ready", "gauge",
                "1 when the task-memory collection mirror last probed ready (cached, no network).",
                [] if ready is None else [({}, 1 if ready else 0)],
            )
            unsynced = task_rag.get("unsynced")
            family(
                "unigrok_task_rag_unsynced_rows", "gauge",
                "Task-memory rows awaiting collection sync (outbox depth).",
                [] if unsynced is None else [({}, unsynced)],
            )
            family(
                "unigrok_task_rag_remote_calls_total", "counter",
                "Collection semantic searches attempted.",
                [({}, task_rag.get("remote_calls", 0))],
            )
            family(
                "unigrok_task_rag_remote_failures_total", "counter",
                "Collection semantic searches that failed (decision fell open to the baseline).",
                [({}, task_rag.get("remote_failures", 0))],
            )
            family(
                "unigrok_task_rag_shadow_flips_total", "counter",
                "Shadow-mode semantic verdicts that disagreed with the baseline (never applied).",
                [({}, task_rag.get("shadow_flips", 0))],
            )
            family(
                "unigrok_task_rag_applied_flips_total", "counter",
                "Active-mode semantic verdicts that changed the borderline route.",
                [({}, task_rag.get("applied_flips", 0))],
            )
            buckets = task_rag.get("fused_score_buckets") or []
            bounds = task_rag.get("fused_score_bucket_bounds") or []
            if buckets and len(buckets) == len(bounds) + 1:
                lines.append(
                    "# HELP unigrok_task_rag_fused_score "
                    "Top fused evidence score per semantic borderline decision."
                )
                lines.append("# TYPE unigrok_task_rag_fused_score histogram")
                cumulative = 0
                for bound, count in zip(bounds, buckets):
                    cumulative += int(count)
                    lines.append(
                        f"unigrok_task_rag_fused_score_bucket{_prom_labels({'le': str(bound)})} {cumulative}"
                    )
                cumulative += int(buckets[-1])
                lines.append(
                    f"unigrok_task_rag_fused_score_bucket{_prom_labels({'le': '+Inf'})} {cumulative}"
                )
                lines.append(
                    f"unigrok_task_rag_fused_score_sum {task_rag.get('fused_score_sum', 0.0)}"
                )
                lines.append(
                    f"unigrok_task_rag_fused_score_count {task_rag.get('fused_score_count', 0)}"
                )

    return "\n".join(lines) + "\n"


async def metrics(request: Request) -> Response:
    """Operational metrics: plain JSON by default, Prometheus text exposition
    with ?format=prometheus.

    The JSON shape needs no extra dependencies and any JSON-capable collector
    can scrape it; the Prometheus variant renders the SAME snapshot as text
    exposition 0.0.4 via stdlib string building (see
    _render_prometheus_metrics). Auth-protected like every non-probe route
    (the auth middleware only exempts /healthz and /readyz). Combines the
    telemetry table (per-plane and per-caller aggregates) with in-process
    runtime state: circuit breakers, the timed-thread gauge, and the routing
    advisor's current view.
    """
    try:
        rows = await store.get_telemetry_stats()
    except Exception as exc:
        logger.warning(f"/metrics telemetry read failed: {exc}")
        rows = []

    advisor_view: Optional[Dict[str, Any]] = None
    try:
        advisor_view = await get_routing_advisor().status_view(store)
    except Exception as exc:
        logger.warning(f"/metrics advisor view failed: {exc}")

    provider_api = await fetch_provider_api_usage()
    snapshot = build_metrics_snapshot(
        rows,
        runtime=get_runtime_stats(),
        circuit_breakers=get_circuit_breaker_state(),
        routing_advisor=advisor_view,
        provider_api=provider_api,
        caller_limit=_METRICS_TOP_CALLERS,
    )
    if request.query_params.get("format", "").strip().lower() == "prometheus":
        return Response(
            _render_prometheus_metrics(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return JSONResponse(snapshot)


async def get_xai_model_ids() -> List[str]:
    discovery = await discover_xai_api_models()
    names = [item["id"] for item in discovery["models"] if item.get("id")]
    return sorted({UNIGROK_AGENT_MODEL, *names})


async def models(_: Request) -> JSONResponse:
    created = int(time.time())
    data = [
        {"id": model_id, "object": "model", "created": created, "owned_by": "unigrok" if model_id == UNIGROK_AGENT_MODEL else "xai"}
        for model_id in await get_xai_model_ids()
    ]
    return JSONResponse({"object": "list", "data": data})


def _session_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    session = payload.get("user") or payload.get("session")
    if isinstance(session, str) and session.strip():
        return session.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("session")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _openai_finish_reason(layer: MetaLayer) -> str:
    # Truncation-shaped outcomes map to OpenAI's "length"; everything else,
    # including fallback-recovered answers, reads as a normal stop.
    if layer.finish_reason in ("budget_exhausted", "depth_exhausted"):
        return "length"
    return "stop"


_AGENT_MODES = ("auto", "fast", "reasoning", "thinking")


def _resolve_agent_model(payload: Dict[str, Any]) -> Optional[str]:
    """Map the virtual agent model to auto-routing.

    An explicit real slug in `xai_model` pins the model; unset (or the virtual
    `unigrok-agent` name) returns None so orchestrate() auto-selects.
    """
    explicit = payload.get("xai_model")
    if isinstance(explicit, str):
        explicit = explicit.strip()
        if explicit and explicit != UNIGROK_AGENT_MODEL:
            return explicit
    return None


def _agent_turn_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shared run_agent_turn kwargs for the OpenAI-compatible agent branch.

    Accepts the extension fields `xai_model`, `mode`, and `thinking_mode` so
    remote clients keep the full routing surface (same mode mapping as the
    stdio `agent` tool).
    """
    mode = payload.get("mode")
    mode = mode.strip().lower() if isinstance(mode, str) else ""
    if mode not in _AGENT_MODES:
        mode = "auto"
    return {
        "session": _scoped_session(_session_from_payload(payload)),
        "messages": payload.get("messages") or [],
        "model": _resolve_agent_model(payload),
        "mode": "reasoning" if mode == "reasoning" else "auto",
        "thinking_mode": mode == "thinking" or bool(payload.get("thinking_mode")),
        "enable_agentic": mode != "fast",
    }


def _layer_to_chat_completion(layer: MetaLayer, model: str) -> Dict[str, Any]:
    created = int(time.time())
    content = layer.generation or ""
    total_tokens = int(layer.tokens or 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": _openai_finish_reason(layer),
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": total_tokens,
        },
        "unigrok": {
            "plane": layer.plane,
            "route": layer.route,
            "profile": layer.profile,
            "policy_mode": layer.policy_mode,
            "finish_reason": layer.finish_reason,
            "cost_usd": layer.cost_usd,
            "latency": layer.latency,
            "context_id": layer.context_id,
            "request_id": layer.request_id,
        },
    }


def _sse(payload: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")


def _sse_error(message: str, error_type: str = "upstream_error", status_code: Optional[int] = None) -> bytes:
    payload: Dict[str, Any] = {"error": {"message": message, "type": error_type}}
    if status_code is not None:
        payload["error"]["status_code"] = status_code
    return _sse(payload)


async def _stream_agent(payload: Dict[str, Any], model: str) -> AsyncIterator[bytes]:
    """Stream an agent turn as OpenAI-compatible SSE chunks.

    Real streaming, not post-hoc chunking: run_agent_turn's on_event callback
    feeds a queue that this generator drains while the turn is in flight.
    Fast-plane turns emit true content deltas from chat.stream(); agentic runs
    emit progress events (depth advanced, tool start/end, cost-so-far) as
    empty-delta chunks carrying a `unigrok.event` extension block, with the
    final answer arriving as content chunks at the end. Standard OpenAI
    clients ignore the extension chunks, so the wire format stays compatible.
    """
    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def _chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None, event: Optional[Dict[str, Any]] = None) -> bytes:
        chunk: Dict[str, Any] = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if event is not None:
            chunk["unigrok"] = {"event": event}
        return _sse(chunk)

    yield _chunk({"role": "assistant"})

    queue: asyncio.Queue = asyncio.Queue()

    def _on_event(event: Dict[str, Any]) -> None:
        queue.put_nowait(("event", event))

    task = asyncio.create_task(run_agent_turn(**_agent_turn_kwargs(payload), on_event=_on_event))

    def _finalize(t: asyncio.Task) -> None:
        if not t.cancelled():
            # Retrieve now so a client disconnect never leaves an
            # unretrieved-exception warning; result() below still raises.
            t.exception()
        queue.put_nowait(("done", t))

    task.add_done_callback(_finalize)

    streamed_parts: List[str] = []
    try:
        while True:
            kind, item = await queue.get()
            if kind == "done":
                break
            if item.get("type") == "content_delta":
                text = str(item.get("text") or "")
                if text:
                    streamed_parts.append(text)
                    yield _chunk({"content": text})
            else:
                yield _chunk({}, event=item)
        layer = task.result()
        content = layer.generation or ""
        streamed_text = "".join(streamed_parts)
        if content and content != streamed_text:
            # Whatever the deltas delivered is not the authoritative answer:
            # agentic runs (and non-streamable planes) stream nothing, and a
            # fast-plane stream that failed mid-way leaves partial deltas
            # behind while orchestrate recovers via fallback (or surfaces
            # 'CLI recovery failed'). SSE cannot retract sent bytes, so
            # emit the unsent remainder when the final answer extends what
            # streamed, else the full recovered answer after a hard break —
            # never silently truncate.
            if content.startswith(streamed_text):
                remainder = content[len(streamed_text):]
            else:
                if streamed_text:
                    yield _chunk({"content": "\n\n"})
                remainder = content
            for idx in range(0, len(remainder), 1200):
                yield _chunk({"content": remainder[idx : idx + 1200]})
        yield _chunk({}, finish_reason=_openai_finish_reason(layer))
        yield b"data: [DONE]\n\n"
    except Exception:
        yield _sse({"error": {"message": _request_error_message("Agent request failed."), "type": "server_error"}})
        yield b"data: [DONE]\n\n"
    finally:
        # Client disconnect (GeneratorExit) or error: stop the in-flight turn.
        if not task.done():
            task.cancel()


def _xai_headers() -> Dict[str, str]:
    key = os.environ.get("XAI_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _xai_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "model",
        "messages",
        "stream",
        "temperature",
        "top_p",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "n",
        "response_format",
        "seed",
        "tools",
        "tool_choice",
    }
    return {key: value for key, value in payload.items() if key in allowed}


async def post_xai_chat(payload: Dict[str, Any]) -> Response:
    if not os.environ.get("XAI_API_KEY"):
        return _json_error("XAI_API_KEY is not configured.", status_code=503, code="service_unavailable")
    url = os.environ.get("XAI_API_BASE_URL", XAI_BASE_URL).rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=_xai_payload(payload), headers=_xai_headers())
        if response.status_code >= 400:
            return _json_error(
                _request_error_message("Upstream request failed."),
                status_code=response.status_code,
                code="upstream_error",
            )

        try:
            return JSONResponse(response.json())
        except Exception:
            return JSONResponse(
                {"error": {"message": _request_error_message("Upstream returned invalid JSON."), "type": "bad_gateway"}},
                status_code=502
            )
    except Exception:
        return JSONResponse(
            {"error": {"message": _request_error_message("Upstream transport error."), "type": "bad_gateway"}},
            status_code=502
        )


async def stream_xai_chat(payload: Dict[str, Any]) -> AsyncIterator[bytes]:
    if not os.environ.get("XAI_API_KEY"):
        yield _sse_error("XAI_API_KEY is not configured.", "service_unavailable")
        yield b"data: [DONE]\n\n"
        return
    url = os.environ.get("XAI_API_BASE_URL", XAI_BASE_URL).rstrip("/") + "/chat/completions"
    stream_timeout = httpx.Timeout(
        connect=10.0,
        read=_bounded_env_float(
            "UNIGROK_XAI_STREAM_IDLE_TIMEOUT_SEC", 180.0, 10.0, 3600.0
        ),
        write=30.0,
        pool=10.0,
    )
    try:
        async with httpx.AsyncClient(timeout=stream_timeout) as client:
            async with client.stream("POST", url, json=_xai_payload(payload), headers=_xai_headers()) as response:
                if response.status_code >= 400:
                    await response.aread()
                    message = _request_error_message("Upstream request failed.")
                    yield _sse_error(message, "upstream_error", response.status_code)
                    yield b"data: [DONE]\n\n"
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except httpx.HTTPError:
        yield _sse_error(_request_error_message("Upstream transport error."), "upstream_error")
        yield b"data: [DONE]\n\n"


def _upstream_error_message(body: bytes) -> str:
    # Preserve the helper for compatibility while never echoing arbitrary
    # upstream response bodies into a public response.
    return "Upstream request failed."


async def chat_completions(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("Request body must be valid JSON.")

    payload_error = _validate_chat_payload(payload)
    if payload_error is not None:
        return payload_error

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return _json_error("Field 'model' is required.", status_code=400)
    if not isinstance(payload.get("messages"), list):
        return _json_error("Field 'messages' must be an array.", status_code=400)

    stream = bool(payload.get("stream"))
    if model == UNIGROK_AGENT_MODEL:
        if stream:
            return StreamingResponse(_stream_agent(payload, model), media_type="text/event-stream")
        try:
            layer = await run_agent_turn(**_agent_turn_kwargs(payload))
            return JSONResponse(_layer_to_chat_completion(layer, model))
        except Exception:
            return _json_error(
                _request_error_message("Agent request failed."),
                status_code=500,
                code="server_error",
            )

    known_models = set(await get_xai_model_ids())
    if model not in known_models:
        return _json_error(f"Unknown model '{model}'.", status_code=400)
    if stream:
        return StreamingResponse(stream_xai_chat(payload), media_type="text/event-stream")
    return await post_xai_chat(payload)


async def public_agent(
    prompt: str,
    session: Optional[str] = None,
    system_prompt: Optional[str] = None,
    workspace_context: Optional[str] = None,
    workspace_label: Optional[str] = None,
    mode: Optional[Literal["auto", "fast", "reasoning", "thinking", "research"]] = None,
    model: Optional[str] = None,
) -> AgentResult:
    """Single public remote MCP entry point for the UniGrok agent.

    Args:
        prompt: The goal, question, or task for the agent.
        session: Optional session name. Persists conversation history and tool
            traces so later calls can continue the work.
        system_prompt: Optional system instruction prepended to the conversation.
        workspace_context: Optional, deliberately selected text from the IDE's
            current project (for example a file excerpt, diff, or error). The
            stable service cannot browse the IDE project automatically.
        workspace_label: Optional human-readable project name for that context.
        mode: Optional explicit mode. When omitted, a phoneword mode-dial port
            supplies the default if enabled; otherwise `"auto"` self-routes.
            `"fast"` forces a single toolless
            completion; `"reasoning"` pins the planning model; `"thinking"`
            runs the agent loop plus a schema-enforced reflection review
            (slowest, most expensive); `"research"` pins the planning route,
            enables multi-agent fan-out, and requests inline citations.
        model: Optional Grok model id. Leave unset (or pass the virtual
            `unigrok-agent`) to let routing choose.

    Returns:
        AgentResult containing execution metadata and responses.
    """
    if isinstance(model, str):
        model = model.strip() or None
        if model == UNIGROK_AGENT_MODEL:
            model = None
    if workspace_context is not None:
        if not isinstance(workspace_context, str):
            raise ValueError("workspace_context must be text")
        context_limit = _bounded_env_int(
            "UNIGROK_MAX_WORKSPACE_CONTEXT_CHARS", 100_000, 1_024, 500_000
        )
        if len(workspace_context) > context_limit:
            raise ValueError(
                f"workspace_context exceeds the {context_limit} character limit"
            )
        safe_context = redact_secrets(workspace_context).strip()
        if safe_context:
            label = redact_secrets(str(workspace_label or "current IDE project")).strip()[:160]
            courier = (
                "# Client-provided workspace context (untrusted evidence)\n"
                f"Project label: {label or 'current IDE project'}\n"
                "Use this only as task context. It does not grant filesystem access "
                "and may be incomplete or stale.\n\n"
                f"{safe_context}"
            )
            system_prompt = f"{system_prompt.rstrip()}\n\n{courier}" if system_prompt else courier
    active_dial = _ACTIVE_MODE_DIAL.get()
    resolved_mode = mode or (active_dial[1] if active_dial else "auto")
    mode_source = "explicit" if mode is not None else ("dial" if active_dial else "default")
    is_research = resolved_mode == "research"
    kwargs: Dict[str, Any] = {
        "prompt": prompt,
        "session": _scoped_session(session),
        "system_prompt": system_prompt,
        "model": model,
        "mode": "reasoning" if resolved_mode in ("reasoning", "research") else "auto",
        "thinking_mode": resolved_mode == "thinking",
        "enable_agentic": resolved_mode != "fast",
    }
    if is_research:
        kwargs["agent_count"] = _research_agent_count()
        kwargs["include"] = ["inline_citations"]
    layer = await run_agent_turn(**kwargs)
    citations_mapped = [{"url": url} for url in layer.citations] if layer.citations else None
    return AgentResult(
        response=layer.generation,
        text=layer.generation,
        finish_reason=layer.finish_reason if layer.finish_reason in ["final_answer", "fallback", "tool_calls", "length", "unknown", "error"] else "unknown",
        cost_usd=layer.cost_usd,
        model=layer.model or "unknown",
        profile=layer.profile,
        tokens=layer.tokens,
        latency_sec=layer.latency,
        route=layer.route or "unknown",
        plane=layer.plane if layer.plane in ["API", "CLI", "CLI-Fallback", "local", "utility"] else "API",
        why=layer.routing_why or "auto",
        degraded=layer.degraded,
        citations=citations_mapped,
        requested_mode=resolved_mode,
        mode_source=mode_source,
        dialed_port=active_dial[0] if active_dial and mode is None else None,
    )


def _research_agent_count() -> int:
    raw = os.environ.get("UNIGROK_RESEARCH_AGENT_COUNT", "4").strip()
    try:
        value = int(raw)
    except ValueError:
        return 4
    return value if value in (4, 16) else 4


def create_public_mcp() -> FastMCP:
    mcp = FastMCP(
        "UniGrok xAI Gateway",
        instructions=(
            "UniGrok is a standalone service: no `.agents`, `.codex`, `.grok`, or "
            "other UniGrok files are required in the IDE's current project. Use the "
            "`agent` tool as the public entry point. It routes requests "
            "through UniGrok's xAI-backed single-agent harness, auto-selects a Grok "
            "model unless one is pinned, and returns the answer under `response` "
            "alongside route/cost/finish_reason metadata. The stable service is "
            "workspace-neutral: include deliberately selected project material in "
            "`workspace_context` when Grok needs it. Call `grok_mcp_discover_self` "
            "for the canonical 4765 GROK endpoint, optional phoneword mode dials, "
            "and exact onboarding guidance."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
    )
    mcp.add_tool(public_agent, name="agent")

    # Expose status and onboarding helper tools to the HTTP /mcp endpoint
    from .tools.system import grok_mcp_status, grok_mcp_discover_self, grok_mcp_restart_container
    mcp.add_tool(grok_mcp_status, name="grok_mcp_status")
    mcp.add_tool(grok_mcp_discover_self, name="grok_mcp_discover_self")
    mcp.add_tool(grok_mcp_restart_container, name="grok_mcp_restart_container")

    # Repository evidence is useful to IDE agents developing UniGrok, but it
    # must never become part of the globally registered stable service or a
    # downstream project's implied requirements.
    if PathResolver.contributor_mode():
        from .tools.workspace_memory import register_workspace_memory_tools

        register_workspace_memory_tools(mcp)

    return mcp


async def missing_ui(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "unavailable",
            "detail": "mcp_ui static assets are not installed in this runtime.",
        },
        status_code=503,
    )


def create_ui_app():
    ui_dir = PathResolver.get_service_root() / "mcp_ui"
    if ui_dir.is_dir():
        return StaticFiles(directory=ui_dir, html=True)
    logger.warning("MCP UI static directory missing: %s", ui_dir)
    return Starlette(routes=[
        Route("/", missing_ui, methods=["GET"]),
        Route("/{path:path}", missing_ui, methods=["GET"]),
    ])


def create_docs_app():
    docs_dir = PathResolver.get_service_root() / "docs"
    if docs_dir.is_dir():
        return StaticFiles(directory=docs_dir, html=False)
    logger.warning("Docs directory missing: %s", docs_dir)
    return Starlette(routes=[
        Route("/{path:path}", missing_ui, methods=["GET"]),
    ])


async def webmcp_manifest(_: Request) -> JSONResponse:
    manifest = {
        "webmcp_version": "0.1",
        "name": "uni-grok-mcp-docs",
        "title": "UniGrok MCP Documentation & Tools",
        "description": "Agent-callable documentation and verification helper tools for the UniGrok MCP gateway.",
        "tools": [
            {
                "name": "get_schema",
                "description": "Returns the Pydantic/JSON schema of a given UniGrok tool."
            },
            {
                "name": "example_call",
                "description": "Returns a JSON template payload/example call for a given UniGrok mode."
            },
            {
                "name": "simulate_reasoning_guard",
                "description": "Simulates checking if a model meets the required reasoning level."
            },
            {
                "name": "fetch_okf_bundle",
                "description": "Returns the metadata, manifest, and topic URLs in the OKF bundle."
            }
        ]
    }
    return JSONResponse(manifest)


def create_app() -> Starlette:
    if is_cloudrun_runtime() and not _allow_unauthenticated() and not _api_keys():
        raise RuntimeError("UNIGROK_API_KEYS must be set in Cloud Run runtime.")

    public_mcp = create_public_mcp()
    mcp_app = public_mcp.streamable_http_app()

    @asynccontextmanager
    async def app_lifespan(_: Starlette):
        async with public_mcp.session_manager.run():
            try:
                yield
            finally:
                close_xai_client()
                await store.close()

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/runtimez", runtimez, methods=["GET"]),
            Route("/metrics", metrics, methods=["GET"]),
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/.well-known/webmcp", webmcp_manifest, methods=["GET"]),
            Mount(
                "/docs",
                app=create_docs_app(),
                name="docs",
            ),
            Mount(
                "/ui",
                app=create_ui_app(),
                name="mcp-ui",
            ),
            Mount("/", app=mcp_app),
        ],
        # Request-id binding runs outermost so every response — including
        # origin/auth rejections — carries X-Request-Id and every log line is
        # correlatable; origin validation next so DNS-rebinding attempts are
        # rejected before auth is even consulted; caller binding runs
        # innermost so only origin- and auth-approved requests are attributed.
        middleware=[
            Middleware(RequestIdMiddleware),
            Middleware(RequestBodyLimitMiddleware),
            Middleware(CSPMiddleware),
            Middleware(MCPOriginMiddleware),
            Middleware(GatewayAuthMiddleware),
            Middleware(ModeDialContextMiddleware),
            Middleware(CallerContextMiddleware),
        ],
        lifespan=app_lifespan,
    )


def _resolve_bind_host() -> str:
    """Pick the bind address: loopback for local runs; 0.0.0.0 only for a
    deliberate HTTP deployment or an explicit UNIGROK_HOST override."""
    explicit = os.environ.get("UNIGROK_HOST", "").strip()
    if explicit:
        logger.info(f"HTTP gateway binding to {explicit} (explicit UNIGROK_HOST override).")
        return explicit
    runtime = get_unigrok_runtime()
    if runtime in ("cloudrun", "http"):
        logger.info(f"HTTP gateway binding to 0.0.0.0 (UNIGROK_RUNTIME={runtime}).")
        # Container/cloud binds are guarded by the non-loopback auth check in
        # run_http_server; Docker Compose publishes this on host loopback.
        return "0.0.0.0"  # nosec B104
    logger.info(
        "HTTP gateway binding to 127.0.0.1 (local runtime; set UNIGROK_HOST or "
        "UNIGROK_RUNTIME=http to expose it beyond this machine)."
    )
    return "127.0.0.1"


def _resolve_bind_port(port: Optional[int] = None) -> int:
    if port is not None:
        return port
    raw = os.environ.get("PORT", "4765").strip() or "4765"
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid PORT value {raw!r}; falling back to 4765.")
        return 4765


def _log_cli_plane_availability():
    """Say up front whether the local CLI plane exists here.

    A missing or unauthenticated `grok` binary silently failing at request time
    would masquerade as an API error; a startup log makes the verified service
    credential state explicit instead."""
    cli_path = PathResolver.get_grok_cli_path()
    status = grok_cli_plane_status(timeout_sec=5.0)
    if status["ready"]:
        logger.info(f"local CLI plane: ready ({cli_path}; verified grok.com OAuth).")
    elif status["binary"]:
        logger.warning(
            "local CLI plane: %s (%s). Authenticate the global service with `%s`; "
            "API-plane service remains available.",
            status["state"],
            status["auth"],
            status["setup_command"],
        )
    else:
        logger.warning(
            "local CLI plane: UNAVAILABLE (no grok binary at "
            f"{cli_path}). This runtime is API-only; CLI fallback requests "
            "will fail fast rather than recover."
        )


def run_http_server(host: Optional[str] = None, port: Optional[int] = None):
    import uvicorn

    selected_host = host or _resolve_bind_host()
    selected_port = _resolve_bind_port(port)
    if (
        not _is_loopback_bind_host(selected_host)
        and not _auth_is_active()
        and not _allow_unauthenticated()
        and not _trusted_loopback_proxy()
    ):
        raise RuntimeError(
            f"HTTP gateway bind host '{selected_host}' is not loopback and "
            "UNIGROK_API_KEYS is not configured. Set client keys before "
            "exposing the gateway, or explicitly declare a loopback-only "
            "container proxy with UNIGROK_TRUSTED_LOOPBACK_PROXY=1."
        )
    _log_cli_plane_availability()
    workspace_root = os.environ.get("WORKSPACE_ROOT", "").strip()
    if workspace_root:
        logger.info(f"Workspace root override active: {workspace_root} (WORKSPACE_ROOT).")
    logger.info(f"MCP streamable-HTTP endpoint: http://{selected_host}:{selected_port}/mcp")
    if not _is_loopback_bind_host(selected_host) and not _auth_is_active():
        logger.warning(
            f"HTTP gateway is binding {selected_host} without bearer auth; "
            "this is allowed only because the deployment declared a trusted "
            "loopback-only proxy. Remove that declaration before exposing the port."
        )
    uvicorn.run(create_app(), host=selected_host, port=selected_port)
