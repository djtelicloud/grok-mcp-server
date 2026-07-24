"""P1 forge-surface hooks: default-unchanged, runtime UI mount, control-plane skeleton.

Acceptance sheet: docs acceptance sheet rows P1-P7 — surface flag defaulting,
UNIGROK_UI_ROOT runtime mount with traversal safety, X-Forwarded-For never
trusted for loopback, /ui never bearer-authed, forge skeleton 401s, telemetry
contract parity.
"""

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request

from unigrok_public import server


def _request(
    path: str = "/ui/",
    *,
    client: tuple[str, int] | None = ("127.0.0.1", 55001),
    headers: list[tuple[bytes, bytes]] | None = None,
    path_params: dict[str, str] | None = None,
) -> Request:
    request_headers = list(headers or [])
    if not any(name.lower() == b"host" for name, _ in request_headers):
        request_headers.insert(0, (b"host", b"127.0.0.1:4766"))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": request_headers,
        "client": client,
        "path_params": path_params or {},
    }
    request = Request(scope)
    if path_params:
        request.scope["path_params"] = path_params
    return request


def test_surface_defaults_to_public() -> None:
    # No env in the test run → the import-time default must be the public surface
    # with no UI override: byte-for-byte today's 4765.
    assert server.SURFACE == "public"
    assert server.UI_ROOT_OVERRIDE == ""


def test_ui_serves_baked_dashboard_by_default() -> None:
    response = asyncio.run(server.control_center(_request()))
    assert response.status_code == 200
    assert b"<script nonce=" in response.body
    assert b'const configuredSurface="public"' in response.body
    assert b"__UNIGROK_SURFACE_JSON__" not in response.body
    csp = response.headers["content-security-policy"]
    assert "script-src 'self' 'nonce-" in csp
    assert "connect-src 'self'" in csp
    assert "127.0.0.1:4768" not in csp
    assert "127.0.0.1:4769" not in csp


def test_ui_injects_trusted_forge_surface_before_runtime_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "SURFACE", "forge")
    response = asyncio.run(server.control_center(_request()))
    assert response.status_code == 200
    assert b'const configuredSurface="forge"' in response.body
    assert b"__UNIGROK_SURFACE_JSON__" not in response.body


def test_ui_ignores_authorization_header() -> None:
    # /ui is never bearer-authed on any surface: a bogus Authorization header
    # must change nothing.
    response = asyncio.run(
        server.control_center(
            _request(headers=[(b"authorization", b"Bearer forged-junk-token")])
        )
    )
    assert response.status_code == 200


def test_ui_root_override_serves_mounted_console(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<html><body>forge console</body></html>")
    (tmp_path / "app.js").write_text("console.log('forge')")
    monkeypatch.setattr(server, "UI_ROOT_OVERRIDE", str(tmp_path))

    index = asyncio.run(server.control_center(_request()))
    assert index.status_code == 200
    assert b"forge console" in index.body

    asset = asyncio.run(
        server.ui_asset(_request("/ui/app.js", path_params={"asset_path": "app.js"}))
    )
    assert asset.status_code == 200
    assert b"forge" in asset.body
    assert "javascript" in asset.headers["content-type"]


def test_ui_root_override_missing_index_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(server, "UI_ROOT_OVERRIDE", str(tmp_path))
    response = asyncio.run(server.control_center(_request()))
    assert response.status_code == 200
    assert b"<script nonce=" in response.body  # baked dashboard, not a 500


@pytest.mark.parametrize(
    "asset_path",
    ["../secrets.txt", "..", ".env", ".git/config", "a/../../escape", ""],
)
def test_ui_asset_blocks_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, asset_path: str
) -> None:
    (tmp_path / "index.html").write_text("ok")
    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("secret")
    monkeypatch.setattr(server, "UI_ROOT_OVERRIDE", str(tmp_path))
    response = asyncio.run(
        server.ui_asset(_request(f"/ui/{asset_path}", path_params={"asset_path": asset_path}))
    )
    assert response.status_code == 404


def test_ui_asset_404_without_override() -> None:
    # Default surface: every /ui subpath stays 404, identical to the routeless
    # contract it replaces.
    response = asyncio.run(
        server.ui_asset(_request("/ui/app.js", path_params={"asset_path": "app.js"}))
    )
    assert response.status_code == 404


def test_control_plane_404_on_public_surface() -> None:
    response = asyncio.run(server.forge_identity(_request("/api/me")))
    assert response.status_code == 404


def test_public_404_is_byte_identical_to_unregistered_routes() -> None:
    # Anti-fingerprinting oracle: on the public surface the control-plane paths
    # (and /ui subpaths) must be indistinguishable from paths that were never
    # registered — same status, content-type, and body through the real app.
    from starlette.testclient import TestClient

    client = TestClient(server.mcp.streamable_http_app())
    baseline = client.get("/definitely-not-registered")
    for path in (
        "/api/me",
        "/control",
        "/auth/github",
        "/auth/control/start",
        "/auth/control/callback",
        "/ui/app.js",
    ):
        probe = client.get(path)
        assert probe.status_code == baseline.status_code == 404
        assert probe.headers.get("content-type") == baseline.headers.get("content-type")
        assert probe.content == baseline.content


def test_ui_asset_blocks_symlink_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A symlink planted inside the mounted console must not read outside the
    # jail: resolve() follows it and the relative_to() check fails closed.
    root = tmp_path / "console"
    root.mkdir()
    (root / "index.html").write_text("ok")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("secret")
    (root / "leak.txt").symlink_to(secret)
    monkeypatch.setattr(server, "UI_ROOT_OVERRIDE", str(root))
    response = asyncio.run(
        server.ui_asset(_request("/ui/leak.txt", path_params={"asset_path": "leak.txt"}))
    )
    assert response.status_code == 404


def test_control_plane_401_on_forge_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_control_session(_: object) -> None:
        return None

    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setattr(server.github_auth, "control_session_info", no_control_session)
    response = asyncio.run(server.forge_identity(_request("/api/me")))
    assert response.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in response.headers}


def test_forge_auth_rejects_non_loopback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "SURFACE", "forge")
    response = asyncio.run(
        server.forge_identity(
            _request("/api/me", headers=[(b"host", b"attacker.example:4766")])
        )
    )
    assert response.status_code == 403


def test_forge_auth_origin_is_independent_from_public_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "SURFACE", "forge")
    monkeypatch.setenv("UNIGROK_PUBLIC_URL", "http://127.0.0.1:4765")
    monkeypatch.setenv("UNIGROK_FORGE_URL", "http://127.0.0.1:4766")

    assert (
        server._forge_control_callback_url()
        == "http://127.0.0.1:4766/auth/control/callback"
    )
    assert server._forge_auth_guard(_request("/api/me")) is None
    rejected = server._forge_auth_guard(
        _request("/api/me", headers=[(b"host", b"127.0.0.1:4765")])
    )
    assert rejected is not None
    assert rejected.status_code == 403


def test_loopback_check_ignores_forwarded_for() -> None:
    # A spoofed X-Forwarded-For must never widen the loopback exemption; only
    # the direct TCP peer counts.
    spoofed = _request(
        client=("203.0.113.9", 40000),
        headers=[(b"x-forwarded-for", b"127.0.0.1")],
    )
    assert server._client_is_loopback(spoofed) is False

    genuine = _request(
        client=("127.0.0.1", 40000),
        headers=[(b"x-forwarded-for", b"203.0.113.9")],
    )
    assert server._client_is_loopback(genuine) is True

    no_client = _request(client=None)
    assert server._client_is_loopback(no_client) is False


def test_telemetry_contract_identical_across_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_health = asyncio.run(server.healthz(_request("/healthz")))
    monkeypatch.setattr(server, "SURFACE", "forge")
    forge_health = asyncio.run(server.healthz(_request("/healthz")))
    assert public_health.body == forge_health.body
