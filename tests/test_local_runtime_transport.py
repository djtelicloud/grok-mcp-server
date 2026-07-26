from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from unigrok_public import local_plane_loader, server


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://runtime.test", "http://runtime.test/v1"),
        ("http://runtime.test/", "http://runtime.test/v1"),
        ("http://runtime.test/v1", "http://runtime.test/v1"),
        ("http://runtime.test/v1/", "http://runtime.test/v1"),
        (
            "http://host.docker.internal:12434/engines/v1/",
            "http://host.docker.internal:12434/engines/v1",
        ),
        ("http://runtime.test/prefix", "http://runtime.test/prefix/v1"),
    ],
)
def test_openai_compat_api_base(configured: str, expected: str) -> None:
    assert local_plane_loader.openai_compat_api_base(configured) == expected


def test_openai_compat_api_base_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        local_plane_loader.openai_compat_api_base("  ")


def test_default_local_runtime_is_dmr_openai_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNIGROK_LOCAL_RUNTIME_URL", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_AUTO", raising=False)

    assert (
        server._resolve_local_runtime_url()
        == "http://model-runner.docker.internal/engines/v1"
    )

    monkeypatch.setenv("UNIGROK_LOCAL_RUNTIME_URL", "http://127.0.0.1:8081/v1/")
    assert server._resolve_local_runtime_url() == "http://127.0.0.1:8081/v1"


@pytest.mark.parametrize(
    ("configured", "expected"),
    (
        ("http://localhost:11434/", "http://localhost:11434"),
        ("http://[::1]:8081/v1/", "http://[::1]:8081/v1"),
        (
            "http://host.docker.internal:12434/engines/v1",
            "http://host.docker.internal:12434/engines/v1",
        ),
        (
            "http://model-runner.docker.internal/engines/v1",
            "http://model-runner.docker.internal/engines/v1",
        ),
    ),
)
def test_explicit_local_runtime_accepts_only_local_hosts(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: str,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_RUNTIME_URL", configured)
    assert server._resolve_local_runtime_url() == expected


@pytest.mark.parametrize(
    "configured",
    (
        "https://127.0.0.1:8081",
        "http://example.com:8081",
        "http://user:pass@127.0.0.1:8081",
        "http://127.0.0.1:8081/v1?mode=unsafe",
        "http://127.0.0.1:bad-port",
    ),
)
def test_explicit_local_runtime_rejects_remote_or_credentialed_urls(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_RUNTIME_URL", configured)
    with pytest.raises(ValueError):
        server._resolve_local_runtime_url()


def test_local_http_clients_ignore_proxies_and_refuse_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[dict[str, object]] = []

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [{"id": "local-model"}],
                "models": [{"name": "local-model"}],
                "choices": [
                    {
                        "message": {"content": "local answer"},
                        "finish_reason": "stop",
                    }
                ],
            }

    class Client:
        def __init__(self, **kwargs: object) -> None:
            clients.append(kwargs)

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> Response:
            return Response()

        async def post(self, url: str, **kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    local = "http://127.0.0.1:8081"
    asyncio.run(local_plane_loader.OpenAICompatProbe().list_models(local, 1.0))
    asyncio.run(local_plane_loader.OllamaProbe().list_models(local, 1.0))
    asyncio.run(local_plane_loader.MLXProbe().list_models(local, 1.0))
    asyncio.run(
        server._openai_compat_chat(
            local,
            "local-model",
            [{"role": "user", "content": "hello"}],
            max_tokens=32,
            timeout=1.0,
        )
    )

    assert len(clients) == 4
    assert all(client["trust_env"] is False for client in clients)
    assert all(client["follow_redirects"] is False for client in clients)


class _OpenAIHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(("GET", self.path))
        if self.path != "/engines/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        self._json({"data": [{"id": "ai/gemma3"}]})

    def do_POST(self) -> None:  # noqa: N802
        type(self).requests.append(("POST", self.path))
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if (
            self.path != "/engines/v1/chat/completions"
            or payload.get("model") != "ai/gemma3"
        ):
            self.send_response(404)
            self.end_headers()
            return
        self._json(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "local answer"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_dmr_versioned_base_drives_probe_and_chat() -> None:
    _OpenAIHandler.requests = []
    httpd = HTTPServer(("127.0.0.1", 0), _OpenAIHandler)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    api_base = f"http://{host}:{port}/engines/v1/"
    try:
        probed = asyncio.run(
            local_plane_loader.OpenAICompatProbe().list_models(api_base, timeout=2.0)
        )
        chatted = asyncio.run(
            server._openai_compat_chat(
                api_base,
                "ai/gemma3",
                [{"role": "user", "content": "hello"}],
                max_tokens=64,
                timeout=2.0,
            )
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)

    assert probed.runtime_up is True
    assert [model.model_id for model in probed.models] == ["ai/gemma3"]
    assert chatted == {"text": "local answer", "stop_reason": "stop"}
    assert _OpenAIHandler.requests == [
        ("GET", "/engines/v1/models"),
        ("POST", "/engines/v1/chat/completions"),
    ]
