from __future__ import annotations

import asyncio
from typing import Any

import pytest

from unigrok_public import remote_auth

AUTHORIZATION_SERVER = "https://auth.example.test"
PUBLIC_RESOURCE = "https://mcp.example.test/mcp"
INTROSPECTION_URL = "https://auth.example.test/oauth/introspect"


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
async def test_coalesced_oauth_claims_are_isolated_per_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_remote_oauth(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Response:
        status_code = 200
        content = b"{}"

        def json(self) -> dict[str, Any]:
            return {
                "active": True,
                "scope": "unigrok:chat",
                "iss": AUTHORIZATION_SERVER,
                "sub": "isolated-reviewer",
                "aud": PUBLIC_RESOURCE,
            }

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    first = asyncio.create_task(
        remote_auth.introspect_oauth_token("isolated-token", "unigrok:chat")
    )
    await started.wait()
    second = asyncio.create_task(
        remote_auth.introspect_oauth_token("isolated-token", "unigrok:chat")
    )
    await asyncio.sleep(0)
    release.set()
    first_claims, second_claims = await asyncio.gather(first, second)

    assert calls == 1
    assert first_claims is not None
    assert second_claims is not None
    assert first_claims == second_claims
    assert first_claims is not second_claims
    first_claims["scope"] = "mutated"
    assert second_claims["scope"] == "unigrok:chat"
