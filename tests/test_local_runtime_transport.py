from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
        == "http://host.docker.internal:12434/engines/v1"
    )

    monkeypatch.setenv("UNIGROK_LOCAL_RUNTIME_URL", "http://runtime.test/v1/")
    assert server._resolve_local_runtime_url() == "http://runtime.test/v1/"


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
