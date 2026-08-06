"""Home-mirror reverse proxy — Cloud/edge UniGrok forwards work to Mac Docker.

When UNIGROK_HOME_MIRROR_URL is set, selected HTTP paths are proxied to the home
gateway (typically local Space via Tailscale Serve/Funnel). Modes:

  off     — never proxy (default)
  prefer  — try home first; on connect failure run local app (may still spend)
  require — home only; if home is down return 503 (no local API/CLI spend)

This closes the gap where Tailscale labels existed but Cloud Run still executed
as a full metered seat.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Paths that stay local on the edge (liveness / identity of this revision).
_LOCAL_ONLY_PREFIXES: tuple[str, ...] = (
    "/healthz",
)

# Paths always mirrored when mode is prefer|require.
_MIRROR_PREFIXES: tuple[str, ...] = (
    "/mcp",
    "/readyz",
    "/runtimez",
    "/benchmarkz",
    "/ui",
    "/.well-known/",
    "/docs/",
    "/v1/",
)


def home_mirror_mode() -> str:
    raw = os.environ.get("UNIGROK_HOME_MIRROR_MODE", "off").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "prefer"
    if raw in {"prefer", "require", "off"}:
        return raw
    return "off"


def home_mirror_url() -> str:
    return os.environ.get("UNIGROK_HOME_MIRROR_URL", "").strip().rstrip("/")


def home_mirror_enabled() -> bool:
    return home_mirror_mode() != "off" and bool(home_mirror_url())


def _path_should_mirror(path: str) -> bool:
    for p in _LOCAL_ONLY_PREFIXES:
        if path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p):
            # exact local-only (e.g. /healthz never mirrors)
            if path in {"/healthz"} or path.startswith("/healthz/"):
                return False
    for prefix in _MIRROR_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    # Default: mirror agent/MCP surface only; keep unknown local for edge diagnostics.
    return path in {"/", ""}


def _hop_by_hop() -> set[str]:
    return {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }


class HomeMirrorMiddleware:
    """ASGI middleware: reverse-proxy selected requests to home UniGrok."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.mode = home_mirror_mode()
        self.base = home_mirror_url()
        self.timeout = float(os.environ.get("UNIGROK_HOME_MIRROR_TIMEOUT", "120") or "120")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)),
                follow_redirects=False,
            )
        return self._client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not home_mirror_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if not _path_should_mirror(path):
            await self.app(scope, receive, send)
            return

        # Drain body (bytearray — avoid quadratic bytes += on large MCP frames)
        chunks = bytearray()
        while True:
            message = await receive()
            part = message.get("body", b"") or b""
            if part:
                chunks.extend(part)
            if not message.get("more_body"):
                break
        body = bytes(chunks)

        query = scope.get("query_string", b"").decode("latin-1")
        target = f"{self.base}{path}"
        if query:
            target = f"{target}?{query}"

        headers: list[tuple[str, str]] = []
        hop = _hop_by_hop()
        for key, value in scope.get("headers") or []:
            k = key.decode("latin-1")
            if k.lower() in hop:
                continue
            headers.append((k, value.decode("latin-1")))
        # Identify edge so home can log
        headers.append(("x-unigrok-home-mirror", "1"))
        headers.append(("x-unigrok-edge-runtime", os.environ.get("UNIGROK_RUNTIME", "edge")))

        method = scope.get("method", "GET")
        try:
            client = await self._get_client()
            req = client.build_request(method, target, headers=headers, content=body)
            resp = await client.send(req, stream=True)
        except Exception as exc:
            if self.mode == "prefer":
                # Re-inject body for local app via a synthetic receive
                async def _replay() -> Message:
                    return {"type": "http.request", "body": body, "more_body": False}

                await self.app(scope, _replay, send)
                return
            # require: fail closed — never spend on edge
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            payload = json.dumps(
                {
                    "status": "home_mirror_unavailable",
                    "mode": "require",
                    "error": type(exc).__name__,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.body",
                    "body": payload,
                    "more_body": False,
                }
            )
            return

        out_headers: list[tuple[bytes, bytes]] = []
        hop_b = {h.encode() for h in hop}
        for key, value in resp.headers.multi_items():
            if key.lower().encode() in hop_b:
                continue
            out_headers.append((key.encode("latin-1"), value.encode("latin-1")))
        out_headers.append((b"x-unigrok-home-mirror", b"proxied"))

        await send(
            {
                "type": "http.response.start",
                "status": resp.status_code,
                "headers": out_headers,
            }
        )
        try:
            async for chunk in resp.aiter_raw():
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await resp.aclose()


def mirror_status() -> dict[str, Any]:
    base = home_mirror_url()
    host = urlsplit(base).netloc if base else ""
    return {
        "enabled": home_mirror_enabled(),
        "mode": home_mirror_mode(),
        "home_host": host or None,
        "configured": bool(base),
    }
