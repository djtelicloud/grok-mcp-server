"""GitHub device-flow auth: real flow mechanics with GitHub faked at the seam.

Properties under test: no client secret anywhere, the GitHub token is never
stored, sessions are short-lived signed cookies, contributor tier comes from
the operator allowlist, and every failure keeps its honest name.
"""

import asyncio
import importlib

import pytest

from unigrok_public import github_auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    github_auth._FLOWS.clear()
    github_auth._SESSION_SECRET_CACHE = None
    monkeypatch.setenv("UNIGROK_GITHUB_CLIENT_ID", "Iv1.test-public-client")
    monkeypatch.setenv("UNIGROK_CONTRIBUTOR_LOGINS", "djtelicloud, friend")
    monkeypatch.setenv("UNIGROK_CONTRIBUTOR_TIER", "sky")
    monkeypatch.setenv("UNIGROK_FORGE_SESSION_SECRET", "s" * 48)
    yield
    github_auth._FLOWS.clear()
    github_auth._SESSION_SECRET_CACHE = None


def _fake_github(monkeypatch: pytest.MonkeyPatch, token_payloads: list[dict]):
    posts: list[tuple[str, dict]] = []

    async def fake_post(url: str, data: dict) -> dict:
        posts.append((url, dict(data)))
        if url == github_auth._DEVICE_CODE_URL:
            return {
                "device_code": "dev-secret-code",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 0,
                "expires_in": 900,
            }
        return token_payloads.pop(0)

    async def fake_user(token: str) -> dict:
        assert token == "gho_test_token"  # noqa: S105
        return {"login": "djtelicloud", "id": 4994715}

    monkeypatch.setattr(github_auth, "_github_post", fake_post)
    monkeypatch.setattr(github_auth, "_github_user", fake_user)
    return posts


def test_unconfigured_start_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_GITHUB_CLIENT_ID", "")
    result = asyncio.run(github_auth.start_flow())
    assert result == {"error": "github_oauth_not_configured"}


def test_full_flow_creates_session_and_discards_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts = _fake_github(
        monkeypatch,
        [{"error": "authorization_pending"}, {"access_token": "gho_test_token"}],
    )
    started = asyncio.run(github_auth.start_flow())
    assert started["user_code"] == "ABCD-1234"
    assert "device_code" not in started  # never leaves the server
    flow = started["flow"]
    github_auth._FLOWS[flow]["interval"] = 0  # test: no wall-clock throttle

    pending = asyncio.run(github_auth.poll_flow(flow))
    assert pending == {"status": "pending"}
    done = asyncio.run(github_auth.poll_flow(flow))
    assert done["login"] == "djtelicloud"
    assert done["tier"] == "sky"  # allowlisted contributor

    info = github_auth.session_info(done["session"])
    assert info == {"login": "djtelicloud", "tier": "sky", "kind": "github"}
    # The GitHub token appears nowhere in retained state or the signed session.
    assert "gho_test_token" not in repr(github_auth._FLOWS)
    assert "gho_test_token" not in done["session"]
    # No secret was ever sent — only the public client id.
    for _url, data in posts:
        assert "client_secret" not in data


def test_non_contributor_gets_public_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_github(monkeypatch, [{"access_token": "gho_test_token"}])

    async def other_user(token: str) -> dict:
        return {"login": "stranger", "id": 1}

    monkeypatch.setattr(github_auth, "_github_user", other_user)
    flow = asyncio.run(github_auth.start_flow())["flow"]
    done = asyncio.run(github_auth.poll_flow(flow))
    assert done["tier"] == "public"  # signed in, but the gate grants nothing


def test_denied_and_expired_flows_keep_honest_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_github(monkeypatch, [{"error": "access_denied"}])
    flow = asyncio.run(github_auth.start_flow())["flow"]
    assert asyncio.run(github_auth.poll_flow(flow)) == {"error": "denied"}
    assert asyncio.run(github_auth.poll_flow(flow)) == {"error": "flow_expired"}
    assert asyncio.run(github_auth.poll_flow("no-such-flow")) == {
        "error": "flow_expired"
    }


def test_signed_session_survives_process_state_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_github(monkeypatch, [{"access_token": "gho_test_token"}])
    flow = asyncio.run(github_auth.start_flow())["flow"]
    sid = asyncio.run(github_auth.poll_flow(flow))["session"]
    assert github_auth.session_info(sid) is not None
    github_auth._SESSION_SECRET_CACHE = None
    importlib.reload(github_auth)
    assert github_auth.session_info(sid) == {
        "login": "djtelicloud",
        "tier": "sky",
        "kind": "github",
    }


def test_signed_session_rejects_tampering_and_expiry() -> None:
    sid = github_auth._encode_session("djtelicloud", 4994715, now=1_000)
    assert github_auth._decode_session(sid, now=1_001) is not None
    encoded, signature = sid.split(".")
    replacement = "A" if signature[-1] != "A" else "B"
    tampered = f"{encoded}.{signature[:-1]}{replacement}"
    assert github_auth._decode_session(tampered, now=1_001) is None
    assert github_auth._decode_session(sid, now=1_000 + 12 * 3600) is None


def test_session_key_file_survives_cache_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("UNIGROK_FORGE_SESSION_SECRET")
    monkeypatch.setenv("UNIGROK_STATE_PATH", str(tmp_path / "state.db"))
    github_auth._SESSION_SECRET_CACHE = None
    first = github_auth._session_secret()
    key_path = tmp_path / ".unigrok-forge-session-key"
    assert key_path.exists()
    assert key_path.stat().st_mode & 0o077 == 0
    github_auth._SESSION_SECRET_CACHE = None
    assert github_auth._session_secret() == first


def test_corrupt_session_key_fails_signed_cookie_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("UNIGROK_FORGE_SESSION_SECRET")
    monkeypatch.setenv("UNIGROK_STATE_PATH", str(tmp_path / "state.db"))
    (tmp_path / ".unigrok-forge-session-key").write_text("too-short")
    github_auth._SESSION_SECRET_CACHE = None
    assert github_auth._decode_session("payload.signature") is None
    assert github_auth.session_info("payload.signature") is None


def test_device_cookie_round_trip_survives_server_cache_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    from unigrok_public import server

    sid = github_auth._encode_session("djtelicloud", 4994715)

    async def completed(_flow: str) -> dict:
        return {"session": sid, "login": "djtelicloud", "tier": "sky"}

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(github_auth, "poll_flow", completed)
    monkeypatch.setattr(server, "_client_is_loopback", lambda _request: True)
    with TestClient(
        server.mcp.streamable_http_app(),
        base_url="http://127.0.0.1:4765",
    ) as client:
        response = client.post(
            "/auth/github/poll",
            json={"flow": "test"},
            headers={"X-UniGrok-CSRF": "1"},
        )
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        github_auth._SESSION_SECRET_CACHE = None
        identity = client.get("/api/me")
    assert identity.status_code == 200
    assert identity.json() == {
        "kind": "github",
        "login": "djtelicloud",
        "tier": "sky",
    }


def test_identity_routes_gate_on_forge_surface() -> None:
    # Route-layer contract: every identity endpoint 404s off-forge, and the
    # poll handler sets the HttpOnly session cookie, never a token.
    from pathlib import Path

    source = Path("src/unigrok_public/server.py").read_text(encoding="utf-8")
    section = source[
        source.index("def _forge_control_callback_url") :
        source.index("async def forge_logout")
    ]
    assert "def _forge_auth_guard" in section
    assert section.count("guard = _forge_auth_guard(request)") >= 5
    assert "httponly=True" in section
    assert "samesite=\"lax\"" in section
    assert "access_token" not in section
