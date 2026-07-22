from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from unigrok_public import gemmagrok_peer, server


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1:8081",
        "http://localhost:8081",
        "http://[::1]:8081",
        "http://host.docker.internal:8081",
    ),
)
def test_runtime_url_accepts_only_explicit_local_origins(url: str) -> None:
    assert gemmagrok_peer._runtime_url(url) == url


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:8081",
        "http://example.com:8081",
        "http://user:pass@127.0.0.1:8081",
        "http://127.0.0.1:8081/v1",
        "http://127.0.0.1",
    ),
)
def test_runtime_url_rejects_remote_credentialed_or_ambiguous_origins(url: str) -> None:
    with pytest.raises(RuntimeError):
        gemmagrok_peer._runtime_url(url)


@pytest.mark.asyncio
async def test_runtime_client_ignores_proxies_and_refuses_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": []}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: object) -> Response:
            captured["request"] = (method, url, kwargs)
            return Response()

    monkeypatch.setattr(gemmagrok_peer.httpx, "AsyncClient", Client)
    monkeypatch.setenv("GEMMAGROK_RUNTIME_URL", "http://127.0.0.1:8081")
    await gemmagrok_peer._runtime_request("GET", "/v1/models")

    assert captured["client"]["trust_env"] is False
    assert captured["client"]["follow_redirects"] is False
    assert captured["request"] == (
        "GET",
        "http://127.0.0.1:8081/v1/models",
        {},
    )


@pytest.mark.asyncio
async def test_model_selection_is_live_discovered_and_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def models() -> list[str]:
        return ["local-a", "local-b"]

    monkeypatch.setattr(gemmagrok_peer, "_served_models", models)
    monkeypatch.setenv("GEMMAGROK_MODEL_ID", "local-b")
    assert await gemmagrok_peer._resolve_model() == "local-b"

    monkeypatch.setenv("GEMMAGROK_MODEL_ID", "not-served")
    with pytest.raises(RuntimeError, match="not served"):
        await gemmagrok_peer._resolve_model()

    monkeypatch.delenv("GEMMAGROK_MODEL_ID", raising=False)
    with pytest.raises(RuntimeError, match="one served model"):
        await gemmagrok_peer._resolve_model()


@pytest.mark.asyncio
async def test_chat_calls_only_local_runtime_and_returns_honest_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    async def request(method: str, path: str, *, payload: dict | None = None) -> dict:
        calls.append((method, path, payload))
        if path == "/v1/models":
            return {"data": [{"id": "local-model"}]}
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "LOCAL_OK"},
                    "finish_reason": "stop",
                }
            ]
        }

    monkeypatch.setattr(gemmagrok_peer, "_runtime_request", request)
    monkeypatch.delenv("GEMMAGROK_MODEL_ID", raising=False)
    result = await gemmagrok_peer.chat(
        "Reply locally",
        system_prompt="Stay concise",
        max_tokens=64,
    )

    assert calls[0] == ("GET", "/v1/models", None)
    assert calls[1][0:2] == ("POST", "/v1/chat/completions")
    assert calls[1][2] == {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": "Stay concise"},
            {"role": "user", "content": "Reply locally"},
        ],
        "max_tokens": 64,
        "stream": False,
    }
    assert result == {
        "text": "LOCAL_OK",
        "model": "local-model",
        "source": "gemmagrok",
        "plane": "local",
        "degraded": True,
        "billing_class": "local_runtime",
        "cost_usd": 0.0,
        "finish_reason": "stop",
        "remote_fallback": False,
    }


def test_reasoning_only_response_is_not_exposed_as_a_final_answer() -> None:
    with pytest.raises(RuntimeError, match="empty final completion"):
        gemmagrok_peer._completion(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "reasoning": "private reasoning"},
                        "finish_reason": "length",
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_status_fails_closed_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable() -> str:
        raise RuntimeError("down")

    monkeypatch.setattr(gemmagrok_peer, "_resolve_model", unavailable)
    assert await gemmagrok_peer.status() == {
        "service": gemmagrok_peer.SERVICE_NAME,
        "runtime": "local",
        "ready": False,
        "model": None,
        "remote_fallback": False,
    }


@pytest.mark.asyncio
async def test_peer_surface_is_two_read_only_tools() -> None:
    tools = await gemmagrok_peer.mcp.list_tools()
    assert [tool.name for tool in tools] == ["chat", "status"]
    assert all(tool.annotations is not None for tool in tools)
    assert all(tool.annotations.readOnlyHint is True for tool in tools)


def test_mcp_transport_rejects_host_and_origin_rebinding() -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "boundary-test", "version": "1"},
        },
    }
    base_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(gemmagrok_peer.mcp.streamable_http_app()) as client:
        hostile_host = client.post(
            "/mcp",
            headers={**base_headers, "Host": "attacker.invalid"},
            json=initialize,
        )
        hostile_origin = client.post(
            "/mcp",
            headers={
                **base_headers,
                "Host": "127.0.0.1:4777",
                "Origin": "https://attacker.invalid",
            },
            json=initialize,
        )
        local = client.post(
            "/mcp",
            headers={
                **base_headers,
                "Host": "127.0.0.1:4777",
                "Origin": "http://127.0.0.1:4777",
            },
            json=initialize,
        )

    assert hostile_host.status_code == 421
    assert hostile_origin.status_code == 403
    assert local.status_code == 200
    assert f'"version":"{gemmagrok_peer.__version__}"' in local.text


def test_public_grok_server_does_not_import_or_route_to_gemmagrok() -> None:
    source = inspect.getsource(server)
    assert "gemmagrok_peer" not in source
    assert "GEMMAGROK" not in source
    assert "gemmagrok" not in server.PUBLIC_TOOL_NAMES


@pytest.mark.asyncio
async def test_public_grok_no_credentials_remains_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_credentials(*, refresh: bool = False) -> dict:
        assert refresh is False
        return {
            "cli": {"ready": False, "models": []},
            "api": {"ready": False, "models": []},
        }

    monkeypatch.setattr(server, "_catalogs", no_credentials)
    with pytest.raises(RuntimeError, match="Neither Grok credential plane is ready"):
        await server._resolve_plane("auto", None, requires_api=False)


def test_compose_helper_is_default_off_loopback_only_and_has_no_credentials() -> None:
    compose = (Path(__file__).parents[1] / "compose.yaml").read_text(encoding="utf-8")
    helper = compose.split("  gemmagrok-local:", maxsplit=1)[1].split("\nvolumes:", maxsplit=1)[0]
    assert 'profiles: ["offline"]' in helper
    assert '"127.0.0.1:${GEMMAGROK_PORT:-4777}:8080"' in helper
    assert "XAI_API_KEY" not in helper
    assert "grok-cli-auth" not in helper
    assert "GEMMAGROK_RUNTIME_URL" in helper
