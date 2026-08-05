from __future__ import annotations

import json
from typing import Any

import pytest

from unigrok_public import remote_auth

AUTHORIZATION_SERVER = "https://auth.example.test"
PUBLIC_RESOURCE = "https://mcp.example.test/mcp"
INTROSPECTION_URL = "https://auth.example.test/oauth/introspect"


async def _exchange(app: Any) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "https",
            "method": "GET",
            "path": "/v1/models",
            "raw_path": b"/v1/models",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer synthetic-status-token")],
            "client": ("203.0.113.10", 4444),
            "server": ("mcp.example.test", 443),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), headers, body


def _configure_remote_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", PUBLIC_RESOURCE)
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", AUTHORIZATION_SERVER)
    monkeypatch.setenv("UNIGROK_OAUTH_INTROSPECTION_URL", INTROSPECTION_URL)
    monkeypatch.setenv(
        "UNIGROK_OAUTH_SCOPES",
        "unigrok:connect,unigrok:invoke,unigrok:review,unigrok:status",
    )
    monkeypatch.delenv("UNIGROK_SERVICE_TOKENS", raising=False)
    monkeypatch.delenv("UNIGROK_SERVICE_TOKEN_SHA256", raising=False)
    remote_auth._oauth_introspection_tasks.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [401, 403])
async def test_introspection_authorization_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch, upstream_status: int
) -> None:
    _configure_remote_oauth(monkeypatch)

    class Response:
        content = b"{}"
        status_code = upstream_status

        def json(self) -> dict[str, Any]:
            return {}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Response:
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("unverified request reached the application")

    status, headers, body = await _exchange(
        remote_auth.RemoteOAuthMiddleware(downstream)
    )

    assert status == 503
    assert headers["retry-after"] == "1"
    assert headers["cache-control"] == "no-store"
    assert "www-authenticate" not in headers
    assert json.loads(body) == {"error": "authorization_service_unavailable"}


@pytest.mark.asyncio
async def test_explicit_inactive_token_remains_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_remote_oauth(monkeypatch)

    class Response:
        content = b'{"active":false}'
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"active": False}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Response:
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("inactive token reached the application")

    status, headers, body = await _exchange(
        remote_auth.RemoteOAuthMiddleware(downstream)
    )

    assert status == 401
    assert "www-authenticate" in headers
    assert json.loads(body) == {"error": "unauthorized"}
