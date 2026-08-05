from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse

from .remote_auth import is_cloudrun_runtime

_LOCAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{32,256}$")
_LOCAL_TOKEN_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_LOCAL_TOKEN_MAX = 256
_LOCAL_AUTH_WINDOW_SECONDS = 60.0
_LOCAL_AUTH_FAILURE_LIMIT = 12
_LOCAL_AUTH_PEER_LIMIT = 256
_LOCAL_SCOPES = " ".join(
    (
        "unigrok:connect",
        "unigrok:invoke",
        "unigrok:review",
        "unigrok:status",
        "unigrok:chat",
    )
)
_FORWARDING_HEADERS = frozenset(
    {
        b"forwarded",
        b"x-forwarded-for",
        b"x-forwarded-host",
        b"x-forwarded-port",
        b"x-forwarded-proto",
        b"x-real-ip",
    }
)
_LOCAL_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class LocalAuthConfigurationError(RuntimeError):
    pass


class _FailureLimiter:
    def __init__(
        self,
        *,
        window_seconds: float = _LOCAL_AUTH_WINDOW_SECONDS,
        failure_limit: int = _LOCAL_AUTH_FAILURE_LIMIT,
        peer_limit: int = _LOCAL_AUTH_PEER_LIMIT,
    ) -> None:
        self.window_seconds = window_seconds
        self.failure_limit = failure_limit
        self.peer_limit = peer_limit
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    def _events(self, peer: str, now: float) -> deque[float]:
        events = self._failures.get(peer)
        if events is None:
            if len(self._failures) >= self.peer_limit:
                self._failures.popitem(last=False)
            events = deque()
            self._failures[peer] = events
        else:
            self._failures.move_to_end(peer)
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def record(self, peer: str, now: float) -> bool:
        events = self._events(peer, now)
        if len(events) <= self.failure_limit:
            events.append(now)
        return len(events) > self.failure_limit

    def clear(self, peer: str) -> None:
        self._failures.pop(peer, None)

    def retry_after(self, peer: str, now: float) -> int:
        events = self._events(peer, now)
        if not events:
            return 1
        return max(1, int(self.window_seconds - (now - events[0]) + 0.999))


def local_auth_configured(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return bool(
        source.get("UNIGROK_LOCAL_MCP_TOKEN", "").strip()
        or source.get("UNIGROK_LOCAL_MCP_TOKEN_SHA256", "").strip()
    )


def _configured_digests(
    environ: Mapping[str, str] | None = None,
) -> frozenset[bytes]:
    source = os.environ if environ is None else environ
    digests: set[bytes] = set()
    raw_token = source.get("UNIGROK_LOCAL_MCP_TOKEN", "").strip()
    if raw_token:
        if _LOCAL_TOKEN_RE.fullmatch(raw_token) is None:
            raise LocalAuthConfigurationError(
                "UNIGROK_LOCAL_MCP_TOKEN must be 32-256 URL-safe ASCII characters"
            )
        digests.add(hashlib.sha256(raw_token.encode("ascii")).digest())
    raw_digest = source.get("UNIGROK_LOCAL_MCP_TOKEN_SHA256", "").strip()
    if raw_digest:
        if _LOCAL_TOKEN_DIGEST_RE.fullmatch(raw_digest) is None:
            raise LocalAuthConfigurationError(
                "UNIGROK_LOCAL_MCP_TOKEN_SHA256 must be one 64-character hex digest"
            )
        digests.add(bytes.fromhex(raw_digest))
    return frozenset(digests)


def validate_local_auth_configuration(
    environ: Mapping[str, str] | None = None,
) -> None:
    source = os.environ if environ is None else environ
    if not local_auth_configured(source):
        return
    if source.get("UNIGROK_RUNTIME", "").strip().lower() == "cloudrun":
        raise LocalAuthConfigurationError(
            "local MCP tokens are forbidden when UNIGROK_RUNTIME=cloudrun"
        )
    if not _configured_digests(source):
        raise LocalAuthConfigurationError("local MCP authentication has no valid token")


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
    if not token or len(token) > _LOCAL_TOKEN_MAX:
        return None
    return token


def _protected_path(path: str) -> bool:
    return path == "/mcp" or path == "/v1" or path.startswith("/v1/")


def _has_forwarding_headers(scope: dict[str, Any]) -> bool:
    return any(key.lower() in _FORWARDING_HEADERS for key, _ in scope.get("headers") or [])


def _peer(scope: dict[str, Any]) -> tuple[str, bool]:
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return "unknown", False
    raw = str(client[0] or "").strip()
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return "unknown", False
    allowed = address.is_loopback or address.is_link_local or any(
        address.version == network.version and address in network
        for network in _LOCAL_NETWORKS
    )
    return address.compressed, allowed


def _matches(token: str | None, digests: frozenset[bytes]) -> bool:
    if not token:
        return False
    try:
        candidate = hashlib.sha256(token.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    matched = False
    for digest in digests:
        if secrets.compare_digest(candidate, digest):
            matched = True
    return matched


def _claims() -> dict[str, Any]:
    return {
        "active": True,
        "token_type": "local",
        "scope": _LOCAL_SCOPES,
        "iss": "unigrok:local-token",
        "sub": "operator",
        "aud": "local-mcp",
        "unigrok_principal": "local:operator",
        "unigrok_auth": "local_token",
    }


def _response(payload: dict[str, str], status_code: int, **headers: str) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    for key, value in headers.items():
        response.headers[key.replace("_", "-")] = value
    return response


class LocalMcpAuthMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._limiter = _FailureLimiter()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or is_cloudrun_runtime()
            or not local_auth_configured()
            or not _protected_path(str(scope.get("path") or ""))
        ):
            await self.app(scope, receive, send)
            return
        try:
            digests = _configured_digests()
        except LocalAuthConfigurationError:
            await _response(
                {"error": "local_auth_configuration_invalid"}, 503, Retry_After="1"
            )(scope, receive, send)
            return
        peer, allowed_peer = _peer(scope)
        if not allowed_peer:
            await _response({"error": "local_peer_required"}, 403)(scope, receive, send)
            return
        if _has_forwarding_headers(scope):
            await _response({"error": "forwarded_headers_not_allowed"}, 403)(
                scope, receive, send
            )
            return
        token = _extract_bearer(_scope_header(scope, b"authorization"))
        if _matches(token, digests):
            self._limiter.clear(peer)
            scope["unigrok.oauth"] = _claims()
            await self.app(scope, receive, send)
            return
        now = time.monotonic()
        limited = self._limiter.record(peer, now)
        if limited:
            retry_after = str(self._limiter.retry_after(peer, now))
            await _response(
                {"error": "too_many_authentication_failures"},
                429,
                Retry_After=retry_after,
            )(scope, receive, send)
            return
        await _response(
            {"error": "unauthorized"},
            401,
            WWW_Authenticate='Bearer realm="unigrok-local-mcp"',
        )(scope, receive, send)
