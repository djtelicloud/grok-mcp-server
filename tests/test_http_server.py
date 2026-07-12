import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from src.http_server import (
    GatewayAuthMiddleware,
    MCPOriginMiddleware,
    ModeDialContextMiddleware,
    _ACTIVE_MODE_DIAL,
    _derive_http_caller,
    _resolve_bind_host,
    _resolve_bind_port,
    create_app,
    create_public_mcp,
    public_agent,
    run_http_server,
    stream_xai_chat,
)
from src.utils import MetaLayer


class FakeStreamResponse:
    def __init__(self, status_code=200, chunks=None, body=b""):
        self.status_code = status_code
        self._chunks = chunks or []
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeAsyncClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, *args, **kwargs):
        return self.response


def test_healthz_is_open(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        res = client.get("/healthz")

    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}


def test_webmcp_manifest_is_open(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        res = client.get("/.well-known/webmcp")

    assert res.status_code == 200
    manifest = res.json()
    assert manifest["webmcp_version"] == "0.1"
    assert manifest["name"] == "uni-grok-mcp-docs"
    assert len(manifest["tools"]) == 4


def test_public_discovery_is_sanitized_in_cloudrun(monkeypatch):
    xai_secret = "xai-must-never-appear"
    client_secret = "gateway-must-never-appear"
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("XAI_API_KEY", xai_secret)
    monkeypatch.setenv("UNIGROK_API_KEYS", client_secret)

    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        response = client.get("/.well-known/unigrok")

    assert response.status_code == 200
    payload = response.json()
    assert payload["transport"]["endpoint"] == "/mcp"
    assert payload["credentials"] == {
        "provider_credentials": "server-side-only",
        "remote_inference": "authentication-required",
    }
    assert payload["oauth"]["access_token_validation"] == "not-configured"
    assert "/runtimez" in payload["access"]["protected"]
    assert "/ui" in payload["access"]["protected"]
    assert xai_secret not in response.text
    assert client_secret not in response.text
    for internal_field in (
        "api_plane",
        "cli_plane",
        "workspace_attached",
        "setup_command",
        "client_tokens_configured",
    ):
        assert internal_field not in response.text


def test_oauth_protected_resource_metadata_is_active(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.grokmcp.org/mcp/")
    monkeypatch.setenv(
        "UNIGROK_OAUTH_AUTHORIZATION_SERVERS",
        "https://auth.grokmcp.org/, https://identity.example.com/tenant",
    )
    monkeypatch.setenv("UNIGROK_OAUTH_SCOPES", "unigrok:invoke, unigrok:read")
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://control.grokmcp.org/oauth/introspect",
    )

    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "https://mcp.grokmcp.org/mcp",
        "authorization_servers": [
            "https://auth.grokmcp.org",
            "https://identity.example.com/tenant",
        ],
        "scopes_supported": ["unigrok:invoke", "unigrok:read"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://grokmcp.org/",
        "x_unigrok_authorization_status": "active",
        "x_unigrok_access_token_validation": "remote-introspection",
    }
    assert response.headers["Cache-Control"] == "public, max-age=300"


@pytest.mark.parametrize(
    ("resource", "authorization_servers"),
    [
        ("", "https://auth.grokmcp.org"),
        ("http://mcp.grokmcp.org/mcp", "https://auth.grokmcp.org"),
        ("https://127.0.0.1/mcp", "https://auth.grokmcp.org"),
        ("https://10.0.0.1/mcp", "https://auth.grokmcp.org"),
        ("https://[::1]/mcp", "https://auth.grokmcp.org"),
        ("https://mcp.grokmcp.org/mcp", ""),
        ("https://mcp.grokmcp.org/mcp", "http://auth.grokmcp.org"),
        ("https://mcp.grokmcp.org/mcp", "https://127.0.0.1"),
        ("https://mcp.grokmcp.org/mcp", "https://auth.internal"),
    ],
)
def test_oauth_metadata_fails_closed_until_validly_configured(
    monkeypatch, resource, authorization_servers
):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", resource)
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", authorization_servers)

    with TestClient(create_app(), base_url="https://attacker-controlled.example") as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 503
    assert response.json()["code"] == "oauth_discovery_not_configured"
    assert "attacker-controlled.example" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("scope", ["bad scope", "bad\nscope", "x" * 129, "\""])
def test_oauth_metadata_rejects_malformed_scopes(monkeypatch, scope):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.grokmcp.org/mcp")
    monkeypatch.setenv(
        "UNIGROK_OAUTH_AUTHORIZATION_SERVERS", "https://auth.grokmcp.org"
    )
    monkeypatch.setenv("UNIGROK_OAUTH_SCOPES", scope)

    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 503
    assert response.json()["code"] == "oauth_discovery_not_configured"


def test_missing_mcp_ui_does_not_block_healthz(monkeypatch, tmp_path):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr(
        "src.http_server.PathResolver.get_service_root",
        staticmethod(lambda: tmp_path),
    )

    with TestClient(create_app()) as client:
        health = client.get("/healthz")
        ui = client.get("/ui/")

    assert health.status_code == 200
    assert health.json() == {"status": "healthy"}
    assert ui.status_code == 503
    assert ui.json()["status"] == "unavailable"


def test_docker_image_copies_mcp_ui_assets():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert "COPY mcp_ui/ ./mcp_ui/" in dockerfile
    assert "COPY docs/okf/ ./docs/okf/" in dockerfile
    assert "COPY .grok/ ./.grok/" in dockerfile
    assert "!docs/okf/**" in dockerignore
    assert "!.grok/**" in dockerignore


def test_cloudrun_requires_api_keys(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("UNIGROK_OAUTH_INTROSPECTION_URL", raising=False)

    with pytest.raises(RuntimeError, match="UNIGROK_API_KEYS"):
        create_app()


def test_cloudrun_accepts_oauth_introspection_without_static_keys(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://control.grokmcp.org/oauth/introspect",
    )

    create_app()


def test_oauth_introspection_enforces_surface_and_tool_scopes(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", "https://mcp.grokmcp.org/mcp")
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://control.grokmcp.org/oauth/introspect",
    )
    observed = []

    async def deny(_token, required_scope):
        observed.append(required_scope)
        return None

    monkeypatch.setattr("src.http_server._introspect_oauth_token", deny)
    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        metrics = client.get("/metrics", headers={"Authorization": "Bearer token-value"})
        review = client.post(
            "/mcp",
            headers={"Authorization": "Bearer token-value"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "review_pull_request", "arguments": {}},
            },
        )
        agent = client.post(
            "/mcp",
            headers={"Authorization": "Bearer token-value"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "agent", "arguments": {}},
            },
        )
        batch = client.post(
            "/mcp",
            headers={"Authorization": "Bearer token-value"},
            json=[
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "agent", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "review_pull_request", "arguments": {}},
                },
            ],
        )

    assert metrics.status_code == review.status_code == agent.status_code == batch.status_code == 401
    assert observed == [
        "unigrok:status",
        "unigrok:connect unigrok:review",
        "unigrok:connect unigrok:invoke",
        "unigrok:connect unigrok:invoke unigrok:review",
    ]
    assert 'scope="unigrok:connect unigrok:review"' in review.headers["WWW-Authenticate"]


def test_oauth_introspection_allows_valid_status_token(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv(
        "UNIGROK_OAUTH_INTROSPECTION_URL",
        "https://control.grokmcp.org/oauth/introspect",
    )

    async def allow(_token, required_scope):
        assert required_scope == "unigrok:status"
        return {"active": True, "scope": "unigrok:status", "sub": "github:42"}

    monkeypatch.setattr("src.http_server._introspect_oauth_token", allow)
    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer token-value"})

    assert response.status_code == 200


def test_oauth_subject_cannot_evade_budget_attribution_with_client_headers():
    scope = {
        "headers": [
            (b"x-client-id", b"rotating-client"),
            (b"x-caller", b"spoofed-caller"),
        ],
        "unigrok.oauth": {"sub": "github:42"},
    }

    assert _derive_http_caller(scope) == "oauth:github:42"


def test_cloudrun_forbids_unauthenticated_override(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "1")

    with pytest.raises(RuntimeError, match="forbidden in Cloud Run"):
        create_app()


def test_cloudrun_rejects_missing_auth(monkeypatch):
    """A missing bearer token gets 401 plus the RFC 6750 challenge header."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")

    with TestClient(create_app()) as client:
        res = client.get("/v1/models")

    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"


def test_local_okf_manifest_and_generated_api_reference_are_served(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        manifest = client.get("/docs/okf/okf-manifest.json")
        api_reference = client.get("/docs/okf/api-reference.md")

    assert manifest.status_code == 200
    assert "api-reference.md" in manifest.json()["files"]
    assert api_reference.status_code == 200
    assert "async def agent(" in api_reference.text


def test_cloudrun_protects_mcp_inference_and_operator_surfaces(monkeypatch):
    """Remote clients never inherit the localhost UI/runtime exemptions."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        responses = {
            "/mcp": client.get("/mcp"),
            "/v1/models": client.get("/v1/models"),
            "/runtimez": client.get("/runtimez"),
            "/metrics": client.get("/metrics"),
            "/ui/": client.get("/ui/"),
            "/docs/okf/manifest.json": client.get("/docs/okf/manifest.json"),
        }

    assert all(response.status_code == 401 for response in responses.values()), responses
    assert all(response.headers["WWW-Authenticate"] == "Bearer" for response in responses.values())


def test_cloudrun_well_known_allowlist_is_exact(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")

    with TestClient(create_app(), base_url="https://mcp.grokmcp.org") as client:
        known = client.get("/.well-known/unigrok")
        unknown = client.get("/.well-known/private-operator-metadata")
        wrong_oauth_resource = client.get(
            "/.well-known/oauth-protected-resource/private-admin"
        )

    assert known.status_code == 200
    assert unknown.status_code == 401
    assert wrong_oauth_resource.status_code == 401


def test_health_probes_are_exempt_from_auth(monkeypatch, tmp_path):
    """/healthz and /readyz stay reachable without credentials so load
    balancers work before any client key is provisioned."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_STATE_DIR", str(tmp_path))

    with TestClient(create_app()) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "healthy"}
    assert ready.status_code in (200, 503)
    assert "checks" in ready.json()


def test_runtimez_reports_no_secret_runtime_status(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "secret-value")
    monkeypatch.setattr(
        "src.http_server.grok_cli_plane_status",
        lambda **_: {
            "state": "ready",
            "ready": True,
            "binary": True,
            "auth": "oauth_verified",
            "setup_command": "grok login",
        },
    )

    with TestClient(create_app()) as client:
        res = client.get("/runtimez")

    assert res.status_code == 200
    payload = res.json()
    assert payload["api_plane"]["xai_api_key"] is True
    assert payload["cli_plane"]["binary"] is True
    assert payload["credential_planes"]["version"] == 1
    assert payload["credential_planes"]["api"]["available"] is True
    assert payload["credential_planes"]["notice_behavior"].startswith("Prompt once")
    assert payload["transport"] == "http"
    assert payload["service"]["requires_project_files"] is False
    assert payload["mode_dials"]["precedence"] == "explicit mode > dialed port > auto"
    assert payload["mode_dials"]["ports"]["7724"] == "research"
    assert "secret-value" not in res.text


def test_runtimez_reports_the_current_phoneword_dial(monkeypatch):
    monkeypatch.setenv("UNIGROK_MODE_DIALS", "1")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app(), base_url="http://localhost:7724") as client:
        payload = client.get("/runtimez").json()

    assert payload["mode_dials"]["request_dial"] == {
        "port": 7724,
        "default_mode": "research",
    }


def test_runtimez_stays_open_on_localhost_with_gateway_auth(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")

    with TestClient(
        create_app(),
        base_url="http://localhost:8080",
        client=("127.0.0.1", 50000),
    ) as client:
        res = client.get("/runtimez")

    assert res.status_code == 200


def test_local_unauthenticated_override_is_loopback_only(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "1")

    with TestClient(
        create_app(),
        base_url="http://localhost:8080",
        client=("127.0.0.1", 50000),
    ) as client:
        loopback = client.get("/metrics")
    with TestClient(
        create_app(),
        base_url="https://gateway.example.com",
        client=("203.0.113.20", 50000),
    ) as client:
        remote = client.get("/metrics")

    assert loopback.status_code == 200
    assert remote.status_code == 401


def test_spoofed_localhost_host_does_not_bypass_remote_auth(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.delenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", raising=False)

    with TestClient(
        create_app(),
        base_url="http://localhost:8080",
        client=("203.0.113.20", 50000),
    ) as client:
        runtime = client.get("/runtimez")
        ui = client.get("/ui/")
        inference = client.get("/v1/models")

    assert runtime.status_code == 401
    assert ui.status_code == 401
    assert inference.status_code == 401


def test_nonloopback_bind_disables_direct_local_operator_exemption(monkeypatch):
    """A same-host reverse proxy is not implicitly a local Control Center."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.delenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", raising=False)

    with TestClient(
        create_app(bound_host="0.0.0.0"),
        base_url="http://localhost:8080",
        client=("127.0.0.1", 50000),
    ) as client:
        runtime = client.get("/runtimez")
        ui = client.get("/ui/")

    assert runtime.status_code == 401
    assert ui.status_code == 401


def test_trusted_compose_proxy_never_bypasses_inference_auth(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "http")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", "1")

    with TestClient(
        create_app(bound_host="0.0.0.0"),
        base_url="http://localhost:4765",
        client=("172.18.0.1", 50000),
    ) as client:
        ui = client.get("/ui/")
        runtime = client.get("/runtimez")
        inference = client.get("/v1/models")
        metrics = client.get("/metrics")

    assert ui.status_code == 200
    assert runtime.status_code == 200
    assert inference.status_code == 401
    assert metrics.status_code == 401


def test_readyz_accepts_cli_auth_without_xai_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "src.http_server.grok_cli_plane_status",
        lambda **_: {
            "state": "ready",
            "ready": True,
            "binary": True,
            "auth": "oauth_verified",
            "setup_command": "grok login",
        },
    )
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "auth.json").write_text("{}", encoding="utf-8")

    with TestClient(create_app()) as client:
        res = client.get("/readyz")

    assert res.status_code == 200
    assert res.json()["checks"]["model_auth"] is True


def test_readyz_body_stays_boolean_on_failure(monkeypatch):
    """The auth-exempt probe must not disclose exception text or filesystem
    paths; failures are logged server-side and the body carries booleans only."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    def _boom():
        raise RuntimeError("/secret/container/path")

    monkeypatch.setattr("src.http_server.PathResolver.get_state_base_dir", _boom)

    with TestClient(create_app()) as client:
        res = client.get("/readyz")

    body = res.json()
    assert res.status_code == 503
    assert body["checks"]["state_dir_writable"] is False
    assert all(isinstance(value, bool) for value in body["checks"].values())
    assert "/secret/container/path" not in res.text


def test_xai_key_is_not_client_auth(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret,xai-secret")

    with TestClient(create_app()) as client:
        res = client.get("/v1/models", headers={"Authorization": "Bearer xai-secret"})

    assert res.status_code == 401


def test_non_ascii_bearer_token_gets_401_not_500(monkeypatch):
    """hmac.compare_digest raises TypeError on non-ASCII str, so the middleware
    compares bytes: a hostile token with a byte >= 0x80 must get a clean 401."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")

    with TestClient(create_app()) as client:
        # Raw latin-1 bytes, as a hostile curl client would send them.
        res = client.get("/v1/models", headers={b"Authorization": "Bearer caf\xe9token".encode("latin-1")})

    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"


def test_non_ascii_configured_key_still_authenticates(monkeypatch):
    """An operator-configured non-ASCII key must keep working (byte compare)."""
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_API_KEYS", "t\xf6ken-secret")
    monkeypatch.setattr("src.http_server.get_xai_model_ids", AsyncMock(return_value=["unigrok-agent"]))

    with TestClient(create_app()) as client:
        res = client.get("/v1/models", headers={b"Authorization": "Bearer t\xf6ken-secret".encode("latin-1")})

    assert res.status_code == 200


def test_models_lists_unigrok_agent(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr("src.http_server.get_xai_model_ids", AsyncMock(return_value=["unigrok-agent", "grok-4.3"]))

    with TestClient(create_app()) as client:
        res = client.get("/v1/models")

    assert res.status_code == 200
    ids = {item["id"] for item in res.json()["data"]}
    assert {"unigrok-agent", "grok-4.3"}.issubset(ids)


def test_agent_chat_completion_uses_shared_harness(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    mock_run = AsyncMock(return_value=MetaLayer(generation="agent answer", plane="API", tokens=12, context_id="ctx-test"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["choices"][0]["message"]["content"] == "agent answer"
    mock_run.assert_awaited_once()


def test_http_rejects_oversized_body_with_request_id(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("UNIGROK_MAX_REQUEST_BODY_BYTES", "16384")

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={
                "model": "unigrok-agent",
                "messages": [{"role": "user", "content": "x" * 20_000}],
            },
        )

    assert res.status_code == 413
    assert res.json()["error"]["code"] == "request_too_large"
    assert res.headers["X-Request-Id"]


def test_chat_completion_rejects_message_limits(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("UNIGROK_MAX_MESSAGES", "1")

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={
                "model": "unigrok-agent",
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ],
            },
        )

    assert res.status_code == 413
    assert res.json()["error"]["code"] == "request_too_large"


def test_agent_http_errors_do_not_expose_exception_text(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr(
        "src.http_server.run_agent_turn",
        AsyncMock(side_effect=RuntimeError("/private/secret/path and bearer super-secret-token")),
    )

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert res.status_code == 500
    assert "private/secret" not in res.text
    assert "super-secret-token" not in res.text
    assert "Agent request failed." in res.json()["error"]["message"]


def test_agent_chat_completion_streams_done(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr(
        "src.http_server.run_agent_turn",
        AsyncMock(return_value=MetaLayer(generation="streamed answer", plane="API")),
    )

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
        ) as res:
            body = "".join(res.iter_text())

    assert res.status_code == 200
    assert "streamed answer" in body
    assert '"role":"assistant"' in body
    assert "data: [DONE]" in body


def test_direct_unknown_model_returns_400(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr("src.http_server.get_xai_model_ids", AsyncMock(return_value=["unigrok-agent", "grok-4.3"]))

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "not-real", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert res.status_code == 400
    assert "Unknown model" in res.json()["error"]["message"]


def test_direct_xai_model_proxies(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr("src.http_server.get_xai_model_ids", AsyncMock(return_value=["unigrok-agent", "grok-4.3"]))
    mock_post = AsyncMock(return_value=JSONResponse({"id": "upstream", "choices": []}))
    monkeypatch.setattr("src.http_server.post_xai_chat", mock_post)

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "grok-4.3", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert res.status_code == 200
    assert res.json()["id"] == "upstream"
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_xai_streaming_success_passes_through(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "server-key")
    monkeypatch.setenv("UNIGROK_XAI_STREAM_IDLE_TIMEOUT_SEC", "45")
    response = FakeStreamResponse(
        status_code=200,
        chunks=[b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"],
    )
    client_kwargs = {}

    def fake_client(**kwargs):
        client_kwargs.update(kwargs)
        return FakeAsyncClient(response)

    monkeypatch.setattr("src.http_server.httpx.AsyncClient", fake_client)

    chunks = [
        chunk
        async for chunk in stream_xai_chat(
            {"model": "grok-4.3", "stream": True, "messages": [{"role": "user", "content": "hello"}]}
        )
    ]

    assert chunks == [b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"]
    assert client_kwargs["timeout"].connect == 10.0
    assert client_kwargs["timeout"].read == 45.0
    assert client_kwargs["timeout"].write == 30.0
    assert client_kwargs["timeout"].pool == 10.0


@pytest.mark.asyncio
async def test_direct_xai_streaming_failure_returns_valid_sse(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "server-key")
    response = FakeStreamResponse(status_code=401, body=b'{"error":{"message":"bad key"}}')
    monkeypatch.setattr("src.http_server.httpx.AsyncClient", lambda **kwargs: FakeAsyncClient(response))

    chunks = [
        chunk
        async for chunk in stream_xai_chat(
            {"model": "grok-4.3", "stream": True, "messages": [{"role": "user", "content": "hello"}]}
        )
    ]
    body = b"".join(chunks).decode("utf-8")
    event_lines = [line for line in body.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]

    assert body.endswith("data: [DONE]\n\n")
    parsed = json.loads(event_lines[0][len("data: ") :])
    assert parsed["error"]["message"].startswith("Upstream request failed.")
    assert parsed["error"]["status_code"] == 401


@pytest.mark.asyncio
async def test_public_mcp_exposes_only_agent():
    mcp = create_public_mcp()
    tools = await mcp.list_tools()
    assert [tool.name for tool in tools] == [
        "agent",
        "review_pull_request",
        "grok_mcp_status",
        "grok_mcp_discover_self",
        "grok_mcp_restart_container",
    ]


def test_mcp_streamable_http_mount_exists(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        res = client.get("/mcp")

    assert res.status_code != 404


def test_review_tool_returns_the_hosted_broker_structured_shape(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    result = type(
        "Result",
        (),
        {
            "response": "No blocking findings.",
            "model": "grok-4.5",
            "resolved_plane": "API",
            "plane": "API",
            "route": "agentic",
            "cost_usd": 0.01,
            "degraded": False,
        },
    )()
    monkeypatch.setattr("src.http_server.public_agent", AsyncMock(return_value=result))
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer client-secret",
                "MCP-Protocol-Version": "2025-06-18",
            },
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "review_pull_request",
                    "arguments": {
                        "repository": "owner/repo",
                        "pull_number": 7,
                        "title": "PR",
                        "diff": "+ safe change",
                        "plane": "api",
                    },
                },
            },
        )

    assert response.status_code == 200
    data_line = next(line for line in response.text.splitlines() if line.startswith("data:"))
    structured = json.loads(data_line[5:].strip())["result"]["structuredContent"]
    assert structured["review"] == "No blocking findings."
    assert structured["plane"] == "API"


def test_middleware_is_pure_asgi():
    """Tombstone: BaseHTTPMiddleware interferes with SSE client disconnects on
    the /mcp mount, so the gateway middleware must stay pure ASGI."""
    from starlette.middleware.base import BaseHTTPMiddleware

    assert not issubclass(GatewayAuthMiddleware, BaseHTTPMiddleware)
    assert not issubclass(MCPOriginMiddleware, BaseHTTPMiddleware)
    assert not issubclass(ModeDialContextMiddleware, BaseHTTPMiddleware)


def test_mcp_rejects_untrusted_origin(monkeypatch):
    """DNS-rebinding protection: a non-loopback browser Origin on /mcp is 403."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_ALLOWED_ORIGINS", raising=False)

    with TestClient(create_app()) as client:
        res = client.get("/mcp", headers={"Origin": "http://evil.example"})

    assert res.status_code == 403
    assert "Origin" in res.json()["error"]["message"]


def test_mcp_allows_missing_origin(monkeypatch):
    """Non-browser clients send no Origin header and must pass the check."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        res = client.get("/mcp")

    assert res.status_code not in (403, 404)


def test_mcp_allows_loopback_origin(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        localhost = client.get("/mcp", headers={"Origin": "http://localhost:3000"})
        loopback = client.get("/mcp", headers={"Origin": "http://127.0.0.1:8080"})

    assert localhost.status_code != 403
    assert loopback.status_code != 403


def test_mcp_allows_env_allowlisted_origin(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("UNIGROK_ALLOWED_ORIGINS", "https://app.example.com, https://other.example.com")

    with TestClient(create_app()) as client:
        allowed = client.get("/mcp", headers={"Origin": "https://app.example.com"})
        rejected = client.get("/mcp", headers={"Origin": "https://evil.example.com"})

    assert allowed.status_code != 403
    assert rejected.status_code == 403


def test_origin_check_covers_v1_surface(monkeypatch):
    """/v1 reaches the same agent backend as /mcp, so DNS-rebinding protection
    rejects an untrusted browser Origin there too; health probes stay open."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_ALLOWED_ORIGINS", raising=False)

    with TestClient(create_app()) as client:
        v1 = client.get("/v1/models", headers={"Origin": "http://evil.example"})
        health = client.get("/healthz", headers={"Origin": "http://evil.example"})

    assert v1.status_code == 403
    assert health.status_code == 200


def test_resolve_bind_host_defaults_to_loopback(monkeypatch, caplog):
    """Local/unset runtime binds 127.0.0.1 so `python main.py --http` does not
    expose an unauthenticated agent to the whole LAN."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_HOST", raising=False)

    with caplog.at_level("INFO", logger="GrokMCP"):
        assert _resolve_bind_host() == "127.0.0.1"
    assert "127.0.0.1" in caplog.text


@pytest.mark.parametrize("runtime", ["cloudrun", "http"])
def test_resolve_bind_host_deployment_runtimes_bind_all(monkeypatch, runtime):
    monkeypatch.setenv("UNIGROK_RUNTIME", runtime)
    monkeypatch.delenv("UNIGROK_HOST", raising=False)

    assert _resolve_bind_host() == "0.0.0.0"


def test_resolve_bind_host_env_override_wins(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_HOST", "192.168.1.50")

    assert _resolve_bind_host() == "192.168.1.50"


def test_resolve_bind_port_handles_bad_env(monkeypatch, caplog):
    monkeypatch.setenv("PORT", "$PORT")

    with caplog.at_level("WARNING", logger="GrokMCP"):
        assert _resolve_bind_port() == 4765

    assert "Invalid PORT value" in caplog.text


def test_resolve_bind_port_explicit_argument_wins(monkeypatch):
    monkeypatch.setenv("PORT", "$PORT")

    assert _resolve_bind_port(9090) == 9090


def test_run_http_server_rejects_exposed_without_auth(monkeypatch):
    """Direct non-loopback binds fail closed when no client auth is set."""
    import uvicorn

    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="UNIGROK_API_KEYS"):
        run_http_server(host="0.0.0.0")


def test_run_http_server_ignores_unauthenticated_override_for_exposed_bind(monkeypatch):
    """The development flag cannot turn a non-loopback listener public."""
    import uvicorn

    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.delenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="UNIGROK_API_KEYS"):
        run_http_server(host="0.0.0.0")


def test_run_http_server_allows_authenticated_exposure(monkeypatch):
    """Configured bearer keys permit a deliberate non-loopback bind."""
    import uvicorn

    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_API_KEYS", "client-secret")
    monkeypatch.delenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    run_http_server(host="0.0.0.0")


def test_run_http_server_allows_declared_loopback_proxy(monkeypatch):
    """Compose's internal all-interface bind is allowed only with its
    explicit host-publication declaration."""
    import uvicorn

    monkeypatch.setenv("UNIGROK_RUNTIME", "http")
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv("UNIGROK_TRUSTED_LOOPBACK_PROXY", "1")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    run_http_server(host="0.0.0.0")


@pytest.mark.asyncio
async def test_public_agent_returns_structured_payload(monkeypatch):
    """public_agent surfaces agent metadata instead of a bare string."""
    layer = MetaLayer(
        generation="structured answer",
        plane="API",
        route="agentic",
        model="grok-build-0.1",
        routing_why="cost",
        degraded=False,
        profile="grok-build-0.1",
        finish_reason="final_answer",
        tokens=42,
        cost_usd=0.012,
        latency=1.5,
    )
    monkeypatch.setattr("src.http_server.run_agent_turn", AsyncMock(return_value=layer))

    result = await public_agent("do the thing")

    assert result.response == "structured answer"
    assert result.route == "agentic"
    assert result.plane == "API"
    assert result.model == "grok-build-0.1"
    assert result.why == "cost"
    assert result.degraded is False
    assert result.profile == "grok-build-0.1"
    assert result.finish_reason == "final_answer"
    assert result.tokens == 42
    assert result.cost_usd == 0.012
    assert result.latency_sec == 1.5
    assert result.requested_mode == "auto"
    assert result.mode_source == "default"
    assert result.dialed_port is None


@pytest.mark.asyncio
async def test_phoneword_dial_supplies_default_mode(monkeypatch):
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)
    token = _ACTIVE_MODE_DIAL.set((7724, "research"))
    try:
        result = await public_agent("research this")
    finally:
        _ACTIVE_MODE_DIAL.reset(token)

    assert mock_run.await_args.kwargs["mode"] == "research"
    assert mock_run.await_args.kwargs["agent_count"] == 4
    assert result.requested_mode == "research"
    assert result.mode_source == "dial"
    assert result.dialed_port == 7724


@pytest.mark.asyncio
async def test_explicit_mode_overrides_phoneword_dial(monkeypatch):
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)
    token = _ACTIVE_MODE_DIAL.set((7724, "research"))
    try:
        result = await public_agent("be quick", mode="fast")
    finally:
        _ACTIVE_MODE_DIAL.reset(token)

    assert mock_run.await_args.kwargs["enable_agentic"] is False
    assert "agent_count" not in mock_run.await_args.kwargs
    assert result.requested_mode == "fast"
    assert result.mode_source == "explicit"
    assert result.dialed_port is None


@pytest.mark.asyncio
async def test_mode_dial_middleware_uses_host_port_only_when_enabled(monkeypatch):
    seen = []

    async def app(scope, receive, send):
        seen.append(_ACTIVE_MODE_DIAL.get())

    scope = {"type": "http", "headers": [(b"host", b"localhost:8465")]}
    middleware = ModeDialContextMiddleware(app)

    monkeypatch.delenv("UNIGROK_MODE_DIALS", raising=False)
    await middleware(scope, None, None)
    monkeypatch.setenv("UNIGROK_MODE_DIALS", "1")
    await middleware(scope, None, None)

    assert seen == [None, (8465, "thinking")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("auto", {"mode": "auto", "thinking_mode": False, "enable_agentic": True}),
        ("fast", {"mode": "auto", "thinking_mode": False, "enable_agentic": False}),
        ("reasoning", {"mode": "reasoning", "thinking_mode": False, "enable_agentic": True}),
        ("thinking", {"mode": "auto", "thinking_mode": True, "enable_agentic": True}),
        (
            "research",
            {
                "mode": "research",
                "thinking_mode": False,
                "enable_agentic": True,
                "agent_count": 4,
                "include": ["inline_citations"],
            },
        ),
    ],
)
async def test_public_agent_mode_mapping(monkeypatch, mode, expected):
    """The remote agent tool maps its mode enum exactly like the stdio one."""
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    await public_agent("task", mode=mode)

    kwargs = mock_run.await_args.kwargs
    for key, value in expected.items():
        assert kwargs[key] == value


@pytest.mark.asyncio
async def test_public_agent_research_surfaces_citations(monkeypatch):
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok", citations=["https://example.test/source"]))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    result = await public_agent("task", mode="research")

    assert result.citations == [{"url": "https://example.test/source"}]


@pytest.mark.asyncio
async def test_public_agent_virtual_model_auto_routes(monkeypatch):
    """The virtual `unigrok-agent` name (or unset) means model=None; a real
    slug pins the model."""
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    await public_agent("task", model="unigrok-agent")
    assert mock_run.await_args.kwargs["model"] is None

    await public_agent("task")
    assert mock_run.await_args.kwargs["model"] is None

    await public_agent("task", model="grok-4.3")
    assert mock_run.await_args.kwargs["model"] == "grok-4.3"


def test_agent_chat_completion_auto_routes_by_default(monkeypatch):
    """The OpenAI facade no longer pins a model: without xai_model the harness
    gets model=None so orchestrate() auto-selects."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    with TestClient(create_app()) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hello"}]},
        )

    kwargs = mock_run.await_args.kwargs
    assert kwargs["model"] is None
    assert kwargs["mode"] == "auto"
    assert kwargs["thinking_mode"] is False
    assert kwargs["enable_agentic"] is True


def test_agent_chat_completion_pins_explicit_xai_model(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    with TestClient(create_app()) as client:
        client.post(
            "/v1/chat/completions",
            json={
                "model": "unigrok-agent",
                "xai_model": "grok-4.3",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert mock_run.await_args.kwargs["model"] == "grok-4.3"


def test_agent_chat_completion_passes_mode_extensions(monkeypatch):
    """The facade accepts `mode`/`thinking_mode` extension fields and maps them
    like the stdio agent tool."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.http_server.run_agent_turn", mock_run)

    with TestClient(create_app()) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "mode": "thinking", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert mock_run.await_args.kwargs["thinking_mode"] is True

        client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "mode": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert mock_run.await_args.kwargs["enable_agentic"] is False

        client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "mode": "reasoning", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert mock_run.await_args.kwargs["mode"] == "reasoning"


def test_agent_chat_completion_maps_finish_reason(monkeypatch):
    """Budget/depth exhaustion reads as OpenAI's 'length'; the raw
    finish_reason and usage come through in the response."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    layer = MetaLayer(generation="truncated", finish_reason="budget_exhausted", tokens=99)
    monkeypatch.setattr("src.http_server.run_agent_turn", AsyncMock(return_value=layer))

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hello"}]},
        )

    body = res.json()
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["unigrok"]["finish_reason"] == "budget_exhausted"
    assert body["usage"]["total_tokens"] == 99


def test_agent_chat_completion_stream_maps_finish_reason(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    layer = MetaLayer(generation="truncated stream", finish_reason="depth_exhausted")
    monkeypatch.setattr("src.http_server.run_agent_turn", AsyncMock(return_value=layer))

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
        ) as res:
            body = "".join(res.iter_text())

    assert '"finish_reason":"length"' in body
    assert "data: [DONE]" in body


def _sse_chunks(body: str):
    """Parse an SSE body into JSON chunks, asserting the [DONE] terminator."""
    lines = [line[len("data: "):] for line in body.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def test_agent_stream_emits_progress_event_chunks(monkeypatch):
    """Agentic streaming: progress events arrive live as empty-delta chunks
    carrying a unigrok.event block, the final answer as content chunks, and
    every chunk keeps the OpenAI chat.completion.chunk shape."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    async def fake_run_agent_turn(**kwargs):
        on_event = kwargs["on_event"]
        on_event({"type": "depth", "depth": 1, "max_depth": 8, "cost_usd": 0.0})
        on_event({"type": "tool_start", "tool": "web_search", "cost_usd": 0.0})
        on_event({"type": "tool_end", "tool": "web_search", "success": True, "elapsed": 0.5, "cost_usd": 0.01})
        return MetaLayer(generation="agentic answer", finish_reason="final_answer")

    monkeypatch.setattr("src.http_server.run_agent_turn", fake_run_agent_turn)

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "messages": [{"role": "user", "content": "go"}]},
        ) as res:
            body = "".join(res.iter_text())

    chunks = _sse_chunks(body)
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert all(len(chunk["choices"]) == 1 and "delta" in chunk["choices"][0] for chunk in chunks)

    events = [chunk["unigrok"]["event"] for chunk in chunks if "unigrok" in chunk]
    assert [event["type"] for event in events] == ["depth", "tool_start", "tool_end"]
    # Progress chunks carry an empty delta so standard OpenAI clients skip them.
    for chunk in chunks:
        if "unigrok" in chunk:
            assert chunk["choices"][0]["delta"] == {}

    contents = [chunk["choices"][0]["delta"].get("content") for chunk in chunks]
    assert "agentic answer" in "".join(text for text in contents if text)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_agent_stream_real_deltas_replace_final_block(monkeypatch):
    """Fast-plane content_delta events become real content chunks and the
    final answer is NOT re-chunked afterwards (no duplicated text)."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    async def fake_run_agent_turn(**kwargs):
        on_event = kwargs["on_event"]
        on_event({"type": "content_delta", "text": "Hello "})
        on_event({"type": "content_delta", "text": "world"})
        return MetaLayer(generation="Hello world", finish_reason="final_answer")

    monkeypatch.setattr("src.http_server.run_agent_turn", fake_run_agent_turn)

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "mode": "fast", "messages": [{"role": "user", "content": "hi"}]},
        ) as res:
            body = "".join(res.iter_text())

    chunks = _sse_chunks(body)
    contents = [
        chunk["choices"][0]["delta"]["content"]
        for chunk in chunks
        if chunk["choices"][0]["delta"].get("content")
    ]
    assert contents == ["Hello ", "world"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_agent_stream_fallback_recovery_replaces_partial_deltas(monkeypatch):
    """A fast-plane stream that dies mid-way leaves partial deltas behind
    while orchestrate recovers via fallback: the recovered answer must still
    reach the client (after a hard break) instead of the stream ending
    truncated with only the failed attempt's prefix."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    async def fake_run_agent_turn(**kwargs):
        on_event = kwargs["on_event"]
        on_event({"type": "content_delta", "text": "Partial ans"})
        # Stream died; orchestrate recovered via the CLI fallback.
        return MetaLayer(generation="Recovered complete answer", finish_reason="fallback")

    monkeypatch.setattr("src.http_server.run_agent_turn", fake_run_agent_turn)

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "mode": "fast", "messages": [{"role": "user", "content": "hi"}]},
        ) as res:
            body = "".join(res.iter_text())

    chunks = _sse_chunks(body)
    text = "".join(
        chunk["choices"][0]["delta"].get("content") or "" for chunk in chunks
    )
    assert text == "Partial ans\n\nRecovered complete answer"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_agent_stream_completes_unsent_suffix(monkeypatch):
    """When the final answer extends what already streamed, only the unsent
    remainder is emitted — no duplicated text, no truncation."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    async def fake_run_agent_turn(**kwargs):
        on_event = kwargs["on_event"]
        on_event({"type": "content_delta", "text": "Hello "})
        return MetaLayer(generation="Hello world", finish_reason="final_answer")

    monkeypatch.setattr("src.http_server.run_agent_turn", fake_run_agent_turn)

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "mode": "fast", "messages": [{"role": "user", "content": "hi"}]},
        ) as res:
            body = "".join(res.iter_text())

    chunks = _sse_chunks(body)
    contents = [
        chunk["choices"][0]["delta"]["content"]
        for chunk in chunks
        if chunk["choices"][0]["delta"].get("content")
    ]
    assert contents == ["Hello ", "world"]


def test_agent_stream_error_still_terminates_with_done(monkeypatch):
    """A failing turn yields an SSE error payload followed by [DONE]."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
    monkeypatch.setattr(
        "src.http_server.run_agent_turn",
        AsyncMock(side_effect=RuntimeError("agent exploded")),
    )

    with TestClient(create_app()) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "unigrok-agent", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as res:
            body = "".join(res.iter_text())

    assert "Agent request failed." in body
    assert body.rstrip().endswith("data: [DONE]")


def test_metrics_is_auth_protected(monkeypatch):
    """/metrics sits behind the bearer auth like every non-probe route: 401
    without a token, 200 with a configured key."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_API_KEYS", "metrics-secret")
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

    with TestClient(create_app()) as client:
        denied = client.get("/metrics")
        allowed = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["format"] == "unigrok-json-v1"


def test_metrics_aggregates_planes_and_runtime(monkeypatch):
    """/metrics computes per-plane counts/success rate/avg+p95 latency/cost
    from telemetry rows and includes runtime, breaker, and advisor views.
    Plain JSON by design — not the Prometheus exposition format."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    rows = [
        {"chosen_plane": "API", "success": 1, "latency": 1.0, "cost": 0.01},
        {"chosen_plane": "API", "success": 0, "latency": 3.0, "cost": 0.02},
        {"chosen_plane": "CLI", "success": 1, "latency": 0.5, "cost": 0.0},
    ]
    import src.http_server as http_module

    monkeypatch.setattr(http_module.store, "get_telemetry_stats", AsyncMock(return_value=rows))

    with TestClient(create_app()) as client:
        res = client.get("/metrics")

    assert res.status_code == 200
    payload = res.json()
    api = payload["planes"]["API"]
    assert api["requests"] == 2
    assert api["success_rate"] == 0.5
    assert api["avg_latency_sec"] == 2.0
    assert api["p95_latency_sec"] == 3.0
    assert api["total_cost_usd"] == pytest.approx(0.03)
    assert payload["planes"]["CLI"]["requests"] == 1
    assert "timed_threads_in_flight" in payload["runtime"]
    assert "circuit_breakers" in payload
    # Advisor view degrades to the static prior under hermetic testing.
    assert payload["routing_advisor"]["borderline_choice"] == "coding (static prior)"


def test_metrics_survives_telemetry_read_failure(monkeypatch):
    """A failing telemetry read degrades to empty plane aggregates instead of
    a 500 — /metrics must stay usable while the store is unhappy."""
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

    import src.http_server as http_module

    monkeypatch.setattr(
        http_module.store, "get_telemetry_stats",
        AsyncMock(side_effect=RuntimeError("db offline")),
    )

    with TestClient(create_app()) as client:
        res = client.get("/metrics")

    assert res.status_code == 200
    assert res.json()["planes"] == {}


def test_run_http_server_port_parsing_fallback(monkeypatch):
    """Verify that run_http_server handles an invalid non-numeric PORT environment
    variable by falling back to 4765 and logging a warning."""
    import uvicorn

    called_port = None
    def mock_run(app, host, port):
        nonlocal called_port
        called_port = port

    monkeypatch.setattr(uvicorn, "run", mock_run)
    monkeypatch.setenv("PORT", "invalid_non_numeric_port")

    run_http_server(port=None)
    assert called_port == 4765


@pytest.mark.asyncio
async def test_post_xai_chat_502_bad_gateway_on_errors(monkeypatch):
    from src.http_server import post_xai_chat
    import httpx
    import json

    monkeypatch.setenv("XAI_API_KEY", "dummy-key")

    # 1. Simulate a connection error
    async def mock_post_raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_raise)

    res = await post_xai_chat({"model": "grok-4.3", "messages": []})
    assert res.status_code == 502
    body = json.loads(res.body.decode())
    assert body["error"]["type"] == "bad_gateway"
    assert body["error"]["message"].startswith("Upstream transport error.")

    # 2. Simulate JSON decoding error
    class MockResponse:
        status_code = 200
        def json(self):
            raise ValueError("Invalid JSON payload")

    async def mock_post_ok(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_ok)

    res_json = await post_xai_chat({"model": "grok-4.3", "messages": []})
    assert res_json.status_code == 502
    body_json = json.loads(res_json.body.decode())
    assert body_json["error"]["type"] == "bad_gateway"
    assert body_json["error"]["message"].startswith("Upstream returned invalid JSON.")
