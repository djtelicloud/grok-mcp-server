"""Public-safe home-mirror mode routing (off | prefer | require)."""

from __future__ import annotations

import pytest

from unigrok_public import home_mirror


def test_mode_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIGROK_HOME_MIRROR_MODE", raising=False)
    monkeypatch.delenv("UNIGROK_HOME_MIRROR_URL", raising=False)
    assert home_mirror.home_mirror_mode() == "off"
    assert home_mirror.home_mirror_enabled() is False


def test_mode_prefer_and_require(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_URL", "http://127.0.0.1:4765")
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "prefer")
    assert home_mirror.home_mirror_mode() == "prefer"
    assert home_mirror.home_mirror_enabled() is True
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "require")
    assert home_mirror.home_mirror_mode() == "require"
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "true")
    assert home_mirror.home_mirror_mode() == "prefer"


def test_healthz_never_mirrored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "prefer")
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_URL", "http://127.0.0.1:4765")
    assert home_mirror._path_should_mirror("/healthz") is False
    assert home_mirror._path_should_mirror("/healthz/") is False
    assert home_mirror._path_should_mirror("/mcp") is True
    assert home_mirror._path_should_mirror("/readyz") is True
    assert home_mirror._path_should_mirror("/ui/index.html") is True


def test_mirror_status_reports_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "require")
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_URL", "https://home.example:8443/")
    status = home_mirror.mirror_status()
    assert status["enabled"] is True
    assert status["mode"] == "require"
    assert status["configured"] is True
    assert status["home_host"] == "home.example:8443"


@pytest.mark.asyncio
async def test_prefer_falls_back_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "prefer")
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_URL", "http://127.0.0.1:1")

    local_hit: list[bool] = []

    async def local_app(scope, receive, send):  # noqa: ANN001
        local_hit.append(True)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"local", "more_body": False})

    mw = home_mirror.HomeMirrorMiddleware(local_app)
    # Force client to raise on send
    class _BoomClient:
        is_closed = False

        def build_request(self, *a, **k):  # noqa: ANN001, ANN002
            return object()

        async def send(self, *a, **k):  # noqa: ANN001, ANN002
            raise ConnectionError("home down")

    async def _fake_client() -> _BoomClient:
        return _BoomClient()

    mw._get_client = _fake_client  # type: ignore[method-assign]

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/mcp",
        "query_string": b"",
        "headers": [],
    }

    async def receive():  # noqa: ANN201
        return {"type": "http.request", "body": b"", "more_body": False}

    messages: list[dict] = []

    async def send(message):  # noqa: ANN001
        messages.append(message)

    await mw(scope, receive, send)
    assert local_hit == [True]
    assert any(m.get("type") == "http.response.start" and m.get("status") == 200 for m in messages)


@pytest.mark.asyncio
async def test_require_returns_503_when_home_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_MODE", "require")
    monkeypatch.setenv("UNIGROK_HOME_MIRROR_URL", "http://127.0.0.1:1")

    async def local_app(scope, receive, send):  # noqa: ANN001
        raise AssertionError("local app must not run in require mode")

    mw = home_mirror.HomeMirrorMiddleware(local_app)

    class _BoomClient:
        is_closed = False

        def build_request(self, *a, **k):  # noqa: ANN001, ANN002
            return object()

        async def send(self, *a, **k):  # noqa: ANN001, ANN002
            raise ConnectionError("home down")

    async def _fake_client() -> _BoomClient:
        return _BoomClient()

    mw._get_client = _fake_client  # type: ignore[method-assign]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": b"",
        "headers": [],
    }

    async def receive():  # noqa: ANN201
        return {"type": "http.request", "body": b"{}", "more_body": False}

    messages: list[dict] = []
    _allowed = frozenset({"http.response.start", "http.response.body"})

    async def send(message):  # noqa: ANN001
        # Strict ASGI: reject invalid types (e.g. legacy http.response.end).
        assert message.get("type") in _allowed, message
        messages.append(message)

    await mw(scope, receive, send)
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    bodies = [m for m in messages if m.get("type") == "http.response.body"]
    assert starts and starts[0]["status"] == 503
    assert bodies, "require-mode 503 must send http.response.body"
    assert all(m.get("type") in _allowed for m in messages)
    assert not any(m.get("type") == "http.response.end" for m in messages)
    payload = b"".join(m.get("body", b"") for m in bodies)
    assert b"home_mirror_unavailable" in payload
    # Final body chunk must terminate the response.
    assert bodies[-1].get("more_body") is False
