from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from starlette.responses import JSONResponse

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
            "headers": [(b"authorization", b"Bearer shared-value")],
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


@pytest.mark.asyncio
async def test_cancelled_leader_preserves_retryable_outage_for_follower(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", PUBLIC_RESOURCE)
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", AUTHORIZATION_SERVER)
    monkeypatch.setenv("UNIGROK_OAUTH_INTROSPECTION_URL", INTROSPECTION_URL)
    monkeypatch.setenv(
        "UNIGROK_OAUTH_SCOPES",
        "unigrok:connect,unigrok:invoke,unigrok:review,unigrok:status",
    )
    remote_auth._oauth_introspection_tasks.clear()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            raise remote_auth.httpx.ConnectError("control unavailable")

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = remote_auth.RemoteOAuthMiddleware(downstream)
    leader = asyncio.create_task(_exchange(middleware))
    await started.wait()
    follower = asyncio.create_task(_exchange(middleware))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    release.set()
    status, headers, body = await follower

    assert calls == 1
    assert status == 503
    assert headers["retry-after"] == "1"
    assert headers["cache-control"] == "no-store"
    assert json.loads(body) == {"error": "authorization_service_unavailable"}
