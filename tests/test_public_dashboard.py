"""Public dashboard and removed-runtime boundary regression tests."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.testclient import TestClient

from unigrok_public import server


def _request(
    path: str = "/ui/",
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    request_headers = list(headers or [])
    if not any(name.lower() == b"host" for name, _ in request_headers):
        request_headers.insert(0, (b"host", b"127.0.0.1:4765"))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": request_headers,
            "client": ("127.0.0.1", 55001),
        }
    )


def test_ui_serves_baked_public_dashboard() -> None:
    response = asyncio.run(server.control_center(_request()))

    assert response.status_code == 200
    assert b"UniGrok Core" in response.body
    assert b"<script nonce=" in response.body
    assert "script-src 'self' 'nonce-" in response.headers["content-security-policy"]


def test_ui_ignores_authorization_header() -> None:
    response = asyncio.run(
        server.control_center(
            _request(headers=[(b"authorization", b"Bearer forged-junk-token")])
        )
    )

    assert response.status_code == 200
    assert b"UniGrok Core" in response.body


def test_removed_runtime_routes_are_ordinary_404s() -> None:
    client = TestClient(server.mcp.streamable_http_app())
    baseline = client.get("/definitely-not-registered")

    for path in (
        "/api/me",
        "/control",
        "/auth/github",
        "/auth/github/start",
        "/auth/github/poll",
        "/auth/control/start",
        "/auth/control/callback",
        "/auth/logout",
        "/ui/app.js",
    ):
        probe = client.get(path)
        assert probe.status_code == baseline.status_code == 404
        assert probe.headers.get("content-type") == baseline.headers.get("content-type")
        assert probe.content == baseline.content
