"""Cloud-first Forge identity bridge over the existing Control OAuth server."""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from unigrok_public import github_auth, server


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _access_token(login: str = "djtelicloud", github_id: int = 4994715) -> str:
    payload = {
        "aud": "https://mcp.grokmcp.org/mcp",
        "exp": 2_000_000_000,
        "githubId": github_id,
        "githubLogin": login,
        "iat": 1_700_000_000,
        "iss": "https://control.grokmcp.org",
        "jti": "test-jti",
        "kind": "user",
        "scope": ["unigrok:connect"],
        "sub": f"github:{github_id}",
        "v": 1,
    }
    return f"ugtoken.{_b64(json.dumps(payload).encode())}.{_b64(b'x' * 32)}"


def _request(
    path: str,
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> server.Request:
    request_headers = list(headers or [])
    if not any(name.lower() == b"host" for name, _ in request_headers):
        request_headers.insert(0, (b"host", b"127.0.0.1:4765"))
    return server.Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": request_headers,
            "query_string": query_string,
            "client": ("127.0.0.1", 40000),
            "server": ("127.0.0.1", 4765),
            "scheme": "http",
        }
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path):
    github_auth._CONTROL_FLOWS.clear()
    github_auth._CONTROL_SESSION_CACHE.clear()
    monkeypatch.setenv("UNIGROK_STATE_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("UNIGROK_CONTRIBUTOR_LOGINS", "djtelicloud")
    monkeypatch.setenv("UNIGROK_CONTRIBUTOR_TIER", "sky")
    yield
    github_auth._CONTROL_FLOWS.clear()
    github_auth._CONTROL_SESSION_CACHE.clear()


def test_cloud_first_pkce_flow_persists_only_scoped_control_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _access_token()
    calls: list[tuple[str, dict | None, dict | None, str | None]] = []

    async def fake_post(url, *, data=None, json_body=None, token=None):
        calls.append((url, data, json_body, token))
        if url == github_auth._CONTROL_REGISTER_URL:
            return {"client_id": "ugclient.test-registration"}
        if url == github_auth._CONTROL_TOKEN_URL:
            return {
                "access_token": access,
                "refresh_token": "ugrefresh.must-not-persist",
            }
        if url == github_auth._CONTROL_INTROSPECT_URL:
            return {"active": True, "sub": "github:4994715"}
        raise AssertionError(url)

    monkeypatch.setattr(github_auth, "_control_post", fake_post)
    callback = "http://127.0.0.1:4766/auth/control/callback"
    started = asyncio.run(github_auth.start_control_flow(callback))
    authorization = urlsplit(started["authorization_url"])
    query = parse_qs(authorization.query)
    assert authorization.geturl().startswith(
        "https://control.grokmcp.org/oauth/authorize?"
    )
    assert query["redirect_uri"] == [callback]
    assert query["scope"] == ["unigrok:connect"]
    assert query["code_challenge_method"] == ["S256"]

    state = query["state"][0]
    finished = asyncio.run(
        github_auth.finish_control_flow(state, "ugcode.test-authorization-code")
    )
    assert finished == {"login": "djtelicloud", "tier": "sky"}
    stored = github_auth._control_token_path().read_text()
    assert stored == access
    assert "ugrefresh" not in stored
    assert github_auth._control_token_path().stat().st_mode & 0o077 == 0
    assert calls[0][0] == github_auth._CONTROL_REGISTER_URL
    assert calls[1][0] == github_auth._CONTROL_TOKEN_URL
    assert calls[2][0] == github_auth._CONTROL_INTROSPECT_URL


def test_cloud_link_survives_process_cache_reset_and_rechecks_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _access_token()
    github_auth.store_control_token(token)
    checks = 0

    async def active(_token: str):
        nonlocal checks
        checks += 1
        return {"active": True, "sub": "github:4994715"}

    monkeypatch.setattr(github_auth, "_introspect_control_token", active)
    github_auth._CONTROL_SESSION_CACHE.clear()
    assert asyncio.run(github_auth.control_session_info(None)) == {
        "kind": "control",
        "login": "djtelicloud",
        "tier": "sky",
    }
    github_auth._CONTROL_SESSION_CACHE.clear()
    assert asyncio.run(github_auth.control_session_info(None))["login"] == "djtelicloud"
    assert checks == 2


def test_revoked_cloud_link_clears_durable_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_auth.store_control_token(_access_token())

    async def inactive(_token: str):
        return {"active": False}

    monkeypatch.setattr(github_auth, "_introspect_control_token", inactive)
    assert asyncio.run(github_auth.control_session_info(None)) is None
    assert not github_auth._control_token_path().exists()


def test_forge_identity_uses_durable_cloud_link_without_browser_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def linked(_token):
        return {"kind": "control", "login": "djtelicloud", "tier": "sky"}

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "control_session_info", linked)
    response = asyncio.run(server.forge_identity(_request("/api/me")))
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "kind": "control",
        "login": "djtelicloud",
        "tier": "sky",
    }


@pytest.mark.parametrize(
    "callback",
    [
        "https://127.0.0.1:4766/auth/control/callback",
        "http://example.com:4766/auth/control/callback",
        "http://127.0.0.1:4766/other",
        "http://user@127.0.0.1:4766/auth/control/callback",
    ],
)
def test_cloud_bridge_rejects_noncanonical_callbacks(callback: str) -> None:
    assert asyncio.run(github_auth.start_control_flow(callback)) == {
        "error": "invalid_callback"
    }


def test_device_cookie_remains_available_when_control_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(_token):
        return {"error": "authorization_unavailable"}

    sid = github_auth._encode_session("djtelicloud", 4994715)
    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "control_session_info", unavailable)
    response = asyncio.run(
        server.forge_identity(
            _request(
                "/api/me",
                headers=[
                    (
                        b"cookie",
                        f"{github_auth.SESSION_COOKIE}={sid}".encode("ascii"),
                    )
                ],
            )
        )
    )
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "kind": "github",
        "login": "djtelicloud",
        "tier": "sky",
    }


def test_control_outage_without_device_cookie_is_reconnect_not_relogin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(_token):
        return {"error": "authorization_unavailable"}

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "control_session_info", unavailable)
    response = asyncio.run(server.forge_identity(_request("/api/me")))
    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "authorization_unavailable"}


def test_control_callback_redirects_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def completed(state: str, code: str):
        assert state == "expected-state"
        assert code == "ugcode.test"
        return {"login": "djtelicloud", "tier": "sky"}

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "finish_control_flow", completed)
    response = asyncio.run(
        server.forge_control_callback(
            _request(
                "/auth/control/callback",
                query_string=b"state=expected-state&code=ugcode.test",
            )
        )
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/ui/"


def test_control_start_failure_returns_to_device_fallback_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(_callback: str):
        return {"error": "control_unreachable"}

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "start_control_flow", unavailable)
    response = asyncio.run(
        server.forge_control_start(_request("/auth/control/start"))
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/ui/?control=unavailable"


def test_logout_requires_non_simple_same_loopback_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_auth.store_control_token(_access_token())
    monkeypatch.setattr(server, "SURFACE", "forge")

    missing = asyncio.run(
        server.forge_logout(_request("/auth/logout", method="POST"))
    )
    assert missing.status_code == 403
    assert github_auth._control_token_path().exists()

    cross_site = asyncio.run(
        server.forge_logout(
            _request(
                "/auth/logout",
                method="POST",
                headers=[
                    (b"x-unigrok-csrf", b"1"),
                    (b"origin", b"https://attacker.example"),
                ],
            )
        )
    )
    assert cross_site.status_code == 403
    assert github_auth._control_token_path().exists()

    accepted = asyncio.run(
        server.forge_logout(
            _request(
                "/auth/logout",
                method="POST",
                headers=[
                    (b"x-unigrok-csrf", b"1"),
                    (b"origin", b"http://127.0.0.1:4765"),
                ],
            )
        )
    )
    assert accepted.status_code == 200
    assert not github_auth._control_token_path().exists()
