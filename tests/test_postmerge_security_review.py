from __future__ import annotations

import json
import secrets
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.responses import JSONResponse

from unigrok_public import remote_auth, server
from unigrok_public.local_auth import LocalMcpAuthMiddleware


async def _exchange(
    app: Any,
    token: str,
    *,
    preauthenticated: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
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

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": "/v1/models",
        "raw_path": b"/v1/models",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 40000),
        "server": ("127.0.0.1", 4765),
    }
    if preauthenticated is not None:
        scope["unigrok.oauth"] = preauthenticated
    await app(scope, receive, send)
    start = next(
        message
        for message in sent
        if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), body


@pytest.mark.asyncio
async def test_local_auth_claims_bypass_remote_introspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = secrets.token_urlsafe(32)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", token)
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://auth.example.test/oauth/introspect",
    )

    async def introspect(_token: str, _required: str) -> None:
        raise AssertionError("local authentication reached OAuth introspection")

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", introspect)
    observed: dict[str, Any] = {}

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        observed.update(scope.get("unigrok.oauth") or {})
        await JSONResponse({"ok": True})(scope, receive, send)

    app = LocalMcpAuthMiddleware(remote_auth.RemoteOAuthMiddleware(downstream))
    status, body = await _exchange(app, token)

    assert status == 200
    assert json.loads(body) == {"ok": True}
    assert observed["unigrok_auth"] == "local_token"
    assert observed["unigrok_principal"] == "local:operator"


def test_preauthenticated_local_claims_are_rebuilt_from_constants() -> None:
    raw = {
        "active": True,
        "token_type": "local",
        "scope": (
            "unigrok:connect unigrok:invoke unigrok:review "
            "unigrok:status unigrok:chat"
        ),
        "iss": "unigrok:local-token",
        "sub": "operator",
        "aud": "local-mcp",
        "unigrok_principal": "local:operator",
        "unigrok_auth": "local_token",
        "provider_metadata": "must-not-propagate",
    }

    claims = remote_auth._preauthenticated_local_claims({"unigrok.oauth": raw})

    assert claims == {
        "active": True,
        "token_type": "local",
        "scope": (
            "unigrok:connect unigrok:invoke unigrok:review "
            "unigrok:status unigrok:chat"
        ),
        "iss": "unigrok:local-token",
        "sub": "operator",
        "aud": "local-mcp",
        "unigrok_principal": "local:operator",
        "unigrok_auth": "local_token",
    }
    assert claims is not raw
    assert "provider_metadata" not in claims


@pytest.mark.parametrize(
    "scope_value",
    [
        (
            "unigrok:chat unigrok:status unigrok:review "
            "unigrok:invoke unigrok:connect"
        ),
        (
            "unigrok:connect unigrok:invoke unigrok:review "
            "unigrok:status unigrok:chat unigrok:chat"
        ),
    ],
)
def test_preauthenticated_local_claims_require_exact_scope(
    scope_value: str,
) -> None:
    raw = {
        "active": True,
        "token_type": "local",
        "scope": scope_value,
        "iss": "unigrok:local-token",
        "sub": "operator",
        "aud": "local-mcp",
        "unigrok_principal": "local:operator",
        "unigrok_auth": "local_token",
    }

    assert (
        remote_auth._preauthenticated_local_claims({"unigrok.oauth": raw})
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged",
    [
        {
            "active": True,
            "token_type": "oauth",
            "scope": "unigrok:chat",
            "iss": "https://attacker.example.test",
            "sub": "forged",
            "aud": "local-mcp",
            "unigrok_principal": "oauth:forged",
            "unigrok_auth": "oauth",
        },
        {
            "active": True,
            "token_type": "local",
            "scope": (
                "unigrok:connect unigrok:invoke unigrok:review "
                "unigrok:status unigrok:chat"
            ),
            "iss": "unigrok:local-token",
            "sub": "operator",
            "aud": "local-mcp",
            "unigrok_principal": "local:attacker",
            "unigrok_auth": "local_token",
        },
    ],
)
async def test_noncanonical_preauthenticated_claims_do_not_bypass_oauth(
    monkeypatch: pytest.MonkeyPatch,
    forged: dict[str, Any],
) -> None:
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://auth.example.test/oauth/introspect",
    )
    monkeypatch.delenv("UNIGROK_SERVICE_TOKENS", raising=False)
    monkeypatch.delenv("UNIGROK_SERVICE_TOKEN_SHA256", raising=False)
    calls: list[tuple[str, str]] = []

    async def introspect(token: str, required: str) -> None:
        calls.append((token, required))
        return None

    async def downstream(
        _scope: dict[str, Any], _receive: Any, _send: Any
    ) -> None:
        raise AssertionError("forged claims reached the protected application")

    monkeypatch.setattr(
        remote_auth,
        "introspect_oauth_token",
        introspect,
    )
    status, body = await _exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        "untrusted-bearer",
        preauthenticated=forged,
    )

    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}
    assert calls == [("untrusted-bearer", "unigrok:chat")]


def test_main_places_local_auth_outside_remote_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class App:
        def __init__(self) -> None:
            self.middleware: list[type[Any]] = []
            self.router = SimpleNamespace(lifespan_context=None)

        def add_middleware(self, middleware: type[Any]) -> None:
            self.middleware.append(middleware)

    app = App()
    monkeypatch.setattr(server.mcp, "streamable_http_app", lambda: app)
    monkeypatch.setattr(server, "validate_remote_configuration", lambda: None)
    monkeypatch.setattr(server, "validate_local_auth_configuration", lambda: None)
    monkeypatch.setattr(server, "validate_principal_key_configuration", lambda: None)
    monkeypatch.setattr(server, "validate_caller_budget_configuration", lambda: None)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    server.main()

    assert app.middleware.index(server.RemoteOAuthMiddleware) < app.middleware.index(
        server.LocalMcpAuthMiddleware
    )


@pytest.mark.asyncio
async def test_budget_cleanup_does_not_mask_breaker_admission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AdmissionError(RuntimeError):
        pass

    class CleanupError(RuntimeError):
        pass

    released: list[object] = []
    reservation = object()

    async def enforce(_store: object) -> None:
        return None

    async def reserve(_store: object) -> object:
        return reservation

    async def release(_store: object, current: object) -> None:
        released.append(current)
        raise CleanupError("cleanup failed")

    async def operation() -> dict[str, Any]:
        raise AssertionError("provider operation unexpectedly executed")

    monkeypatch.setattr(server, "enforce_caller_budget", enforce)
    monkeypatch.setattr(server, "reserve_local_budget", reserve)
    monkeypatch.setattr(server, "release_local_budget", release)
    monkeypatch.setattr(
        server,
        "_breaker_before_call",
        lambda _plane, _model: (_ for _ in ()).throw(AdmissionError("blocked")),
    )

    with pytest.raises(AdmissionError, match="blocked"):
        await server._guarded_provider_call("api", "grok-test", operation)
    assert released == [reservation]
