from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from starlette.responses import JSONResponse

from unigrok_public import local_auth


@pytest.fixture(autouse=True)
def local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_MCP_TOKEN", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_MCP_TOKEN_SHA256", raising=False)


async def _exchange(
    app: Callable[..., Any],
    *,
    path: str = "/mcp",
    headers: tuple[tuple[bytes, bytes], ...] = (),
    client: tuple[str, int] | None = ("127.0.0.1", 4400),
) -> tuple[int, dict[str, str], bytes]:
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
            "scheme": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": list(headers),
            "client": client,
            "server": ("127.0.0.1", 4765),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, body


async def _claims_response(scope: dict[str, Any], receive: Any, send: Any) -> None:
    await JSONResponse(scope.get("unigrok.oauth") or {})(scope, receive, send)


@pytest.mark.asyncio
async def test_unconfigured_local_auth_preserves_loopback_default() -> None:
    status, _, body = await _exchange(local_auth.LocalMcpAuthMiddleware(_claims_response))

    assert status == 200
    assert json.loads(body) == {}


@pytest.mark.asyncio
async def test_plaintext_local_token_authenticates_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "a" * 48
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", credential)
    local_auth.validate_local_auth_configuration()

    status, headers, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(_claims_response),
        headers=((b"authorization", f"Bearer {credential}".encode()),),
    )

    assert status == 200
    assert "www-authenticate" not in headers
    claims = json.loads(body)
    assert claims["unigrok_auth"] == "local_token"
    assert claims["unigrok_principal"] == "local:operator"
    assert "unigrok:invoke" in claims["scope"]


@pytest.mark.asyncio
async def test_digest_local_token_authenticates_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "b" * 48
    digest = hashlib.sha256(credential.encode()).hexdigest()
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN_SHA256", digest)

    status, _, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(_claims_response),
        path="/v1/models",
        headers=((b"authorization", f"Bearer {credential}".encode()),),
    )

    assert status == 200
    assert json.loads(body)["unigrok_auth"] == "local_token"


@pytest.mark.asyncio
async def test_missing_local_token_is_denied_without_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", "c" * 48)
    reached = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal reached
        reached = True
        await JSONResponse({"ok": True})(scope, receive, send)

    status, headers, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(downstream)
    )

    assert reached is False
    assert status == 401
    assert headers["cache-control"] == "no-store"
    assert headers["www-authenticate"] == 'Bearer realm="unigrok-local-mcp"'
    assert json.loads(body) == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_health_endpoint_remains_public_when_local_auth_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", "d" * 48)

    status, _, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(_claims_response), path="/healthz"
    )

    assert status == 200
    assert json.loads(body) == {}


@pytest.mark.asyncio
async def test_forwarded_headers_are_rejected_before_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "e" * 48
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", credential)

    status, _, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(_claims_response),
        headers=(
            (b"authorization", f"Bearer {credential}".encode()),
            (b"x-forwarded-for", b"127.0.0.1"),
        ),
    )

    assert status == 403
    assert json.loads(body) == {"error": "forwarded_headers_not_allowed"}


@pytest.mark.asyncio
async def test_global_peer_is_rejected_even_with_valid_local_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "f" * 48
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", credential)

    status, _, body = await _exchange(
        local_auth.LocalMcpAuthMiddleware(_claims_response),
        headers=((b"authorization", f"Bearer {credential}".encode()),),
        client=("8.8.8.8", 4400),
    )

    assert status == 403
    assert json.loads(body) == {"error": "local_peer_required"}


@pytest.mark.asyncio
async def test_auth_failure_limit_is_bounded_and_valid_token_still_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "g" * 48
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", credential)
    middleware = local_auth.LocalMcpAuthMiddleware(_claims_response)

    responses = [await _exchange(middleware) for _ in range(13)]
    assert [item[0] for item in responses[:12]] == [401] * 12
    assert responses[12][0] == 429
    assert int(responses[12][1]["retry-after"]) >= 1

    status, _, body = await _exchange(
        middleware,
        headers=((b"authorization", f"Bearer {credential}".encode()),),
    )
    assert status == 200
    assert json.loads(body)["unigrok_auth"] == "local_token"


def test_failure_limiter_evicts_oldest_peer() -> None:
    limiter = local_auth._FailureLimiter(peer_limit=2)
    limiter.record("peer-a", 1.0)
    limiter.record("peer-b", 1.0)
    limiter.record("peer-c", 1.0)

    assert list(limiter._failures) == ["peer-b", "peer-c"]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("UNIGROK_LOCAL_MCP_TOKEN", "short", "32-256"),
        ("UNIGROK_LOCAL_MCP_TOKEN_SHA256", "not-a-digest", "64-character"),
    ],
)
def test_invalid_local_auth_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(local_auth.LocalAuthConfigurationError, match=message):
        local_auth.validate_local_auth_configuration()


def test_local_token_is_forbidden_in_cloudrun(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", "h" * 48)
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")

    with pytest.raises(local_auth.LocalAuthConfigurationError, match="forbidden"):
        local_auth.validate_local_auth_configuration()
