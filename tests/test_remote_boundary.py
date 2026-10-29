from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from unigrok_public import remote_auth, server, xai_api
from unigrok_public.identity import (
    principal_label,
    public_state_name,
    reset_active_principal,
    scoped_scope,
    scoped_session,
    set_active_principal,
    tenant_prefix,
)
from unigrok_public.principal_xai import (
    PrincipalXAIConfigurationError,
    load_principal_key_table,
    resolve_xai_api_key,
)
from unigrok_public.state import PublicStateStore

AUTHORIZATION_SERVER = "https://auth.example.test"
PUBLIC_RESOURCE = "https://mcp.example.test/mcp"
INTROSPECTION_URL = "https://auth.example.test/oauth/introspect"
REQUIRED_SCOPES = ",".join(
    (
        "unigrok:connect",
        "unigrok:invoke",
        "unigrok:review",
        "unigrok:status",
    )
)


@pytest.fixture
def remote_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", PUBLIC_RESOURCE)
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", AUTHORIZATION_SERVER)
    monkeypatch.setenv("UNIGROK_OAUTH_INTROSPECTION_URL", INTROSPECTION_URL)
    monkeypatch.setenv("UNIGROK_OAUTH_SCOPES", REQUIRED_SCOPES)
    monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)


def _canonical_principal(subject: str) -> str:
    principal = remote_auth.canonical_oauth_principal(AUTHORIZATION_SERVER, subject)
    assert principal is not None
    return principal


def _active_introspection_payload(subject: str = "reviewer") -> dict[str, Any]:
    return {
        "active": True,
        "scope": "unigrok:connect unigrok:invoke unigrok:review unigrok:status",
        "iss": AUTHORIZATION_SERVER,
        "sub": subject,
        "aud": [PUBLIC_RESOURCE],
    }


class _JSONResponseStream:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = json.dumps(payload).encode()

    async def __aenter__(self) -> _JSONResponseStream:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        yield self._body


@pytest.fixture
def clean_oauth_introspection_state() -> Iterator[None]:
    with remote_auth._oauth_introspection_state_lock:
        remote_auth._oauth_introspection_inflight.clear()
        remote_auth._oauth_negative_cache.clear()
    yield
    with remote_auth._oauth_introspection_state_lock:
        remote_auth._oauth_introspection_inflight.clear()
        remote_auth._oauth_negative_cache.clear()


async def _asgi_exchange(
    app: Callable[..., Any],
    *,
    path: str,
    method: str = "GET",
    body_chunks: tuple[bytes, ...] = (b"",),
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> tuple[int, dict[str, str], bytes]:
    requests = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    request_index = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_index
        if request_index < len(requests):
            message = requests[request_index]
            request_index += 1
            return message
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "https",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": list(headers),
            "client": ("203.0.113.10", 4444),
            "server": ("mcp.example.test", 443),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


def test_cloudrun_oauth_configuration_is_fail_closed(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_auth.validate_remote_configuration()

    monkeypatch.delenv("UNIGROK_OAUTH_INTROSPECTION_URL")
    with pytest.raises(RuntimeError, match="INTROSPECTION"):
        remote_auth.validate_remote_configuration()

    monkeypatch.setenv("UNIGROK_OAUTH_INTROSPECTION_URL", INTROSPECTION_URL)
    monkeypatch.setenv("UNIGROK_ALLOW_UNAUTHENTICATED", "true")
    with pytest.raises(RuntimeError, match="forbidden"):
        remote_auth.validate_remote_configuration()


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("UNIGROK_LOCAL_MCP_TOKEN", "too-short"),
        ("UNIGROK_LOCAL_MCP_TOKEN", f"{'a' * 32},bad"),
        ("UNIGROK_LOCAL_MCP_TOKEN", " "),
        ("UNIGROK_LOCAL_MCP_TOKEN_SHA256", "0" * 63),
        ("UNIGROK_LOCAL_MCP_TOKEN_SHA256", f"{'0' * 64},"),
    ),
)
def test_local_token_configuration_rejects_every_malformed_nonempty_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_MCP_TOKEN", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_MCP_TOKEN_SHA256", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(remote_auth.LocalMCPTokenConfigurationError, match=name):
        remote_auth.validate_remote_configuration()


def test_local_token_configuration_and_matching_are_secret_safe() -> None:
    plaintext = "local-operator-token-aaaaaaaaaaaaaaaaaaaaaaaa"  # noqa: S105
    digest_token = "local-digest-token-bbbbbbbbbbbbbbbbbbbbbbbbb"  # noqa: S105
    environ = {
        "UNIGROK_LOCAL_MCP_TOKEN": plaintext,
        "UNIGROK_LOCAL_MCP_TOKEN_SHA256": hashlib.sha256(digest_token.encode("utf-8")).hexdigest(),
    }

    remote_auth.validate_local_mcp_token_configuration(environ)
    assert remote_auth.local_mcp_token_configured(environ) is True
    for token in (plaintext, digest_token):
        claims = remote_auth.match_local_mcp_token(token, environ)
        assert claims is not None
        assert claims["unigrok_auth"] == "local_mcp_token"
        assert claims["unigrok_principal"] == "local:operator"
        assert token not in json.dumps(claims)
    assert remote_auth.match_local_mcp_token("x" * 32, environ) is None


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("UNIGROK_SERVICE_TOKENS", ""),
        ("UNIGROK_SERVICE_TOKENS", "service-secret-marker"),
        ("UNIGROK_SERVICE_TOKENS", f"{'a' * 32},"),
        ("UNIGROK_SERVICE_TOKENS", " "),
        ("UNIGROK_SERVICE_TOKEN_SHA256", ""),
        ("UNIGROK_SERVICE_TOKEN_SHA256", "0" * 63),
        ("UNIGROK_SERVICE_TOKEN_SHA256", f"{'0' * 64},"),
        ("UNIGROK_SERVICE_TOKEN_SCOPES", ""),
        ("UNIGROK_SERVICE_TOKEN_SCOPES", "unigrok:connect,"),
        ("UNIGROK_SERVICE_TOKEN_SCOPES", 'unigrok:connect,"scope-secret-marker'),
    ),
)
def test_service_token_configuration_rejects_malformed_and_empty_entries_without_leaks(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    for setting in (
        "UNIGROK_RUNTIME",
        "UNIGROK_SERVICE_TOKENS",
        "UNIGROK_SERVICE_TOKEN_SHA256",
        "UNIGROK_SERVICE_TOKEN_SCOPES",
    ):
        monkeypatch.delenv(setting, raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(remote_auth.ServiceTokenConfigurationError) as raised:
        remote_auth.validate_remote_configuration()

    assert name in str(raised.value)
    for marker in ("service-secret-marker", "scope-secret-marker"):
        assert marker not in str(raised.value)


def test_service_token_configuration_rejects_duplicate_credentials_and_scopes() -> None:
    plaintext = "service-duplicate-token-aaaaaaaaaaaaaaaaaaaa"  # noqa: S105
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    configurations = (
        {"UNIGROK_SERVICE_TOKENS": f"{plaintext},{plaintext}"},
        {"UNIGROK_SERVICE_TOKEN_SHA256": f"{digest},{digest}"},
        {
            "UNIGROK_SERVICE_TOKENS": plaintext,
            "UNIGROK_SERVICE_TOKEN_SHA256": digest,
        },
        {"UNIGROK_SERVICE_TOKEN_SCOPES": "unigrok:connect,unigrok:connect"},
    )

    for environ in configurations:
        with pytest.raises(remote_auth.ServiceTokenConfigurationError) as raised:
            remote_auth.validate_service_token_configuration(environ)
        assert plaintext not in str(raised.value)
        assert digest not in str(raised.value)


def test_service_token_scope_default_applies_only_when_setting_is_absent() -> None:
    token = "service-scope-token-bbbbbbbbbbbbbbbbbbbbbbbb"  # noqa: S105
    default_environ = {"UNIGROK_SERVICE_TOKENS": token}
    restricted_environ = {
        "UNIGROK_SERVICE_TOKENS": token,
        "UNIGROK_SERVICE_TOKEN_SCOPES": "unigrok:connect unigrok:review",
    }

    remote_auth.validate_service_token_configuration(default_environ)
    remote_auth.validate_service_token_configuration(restricted_environ)
    assert remote_auth.service_token_scopes(default_environ) == list(
        remote_auth._DEFAULT_SERVICE_SCOPES
    )
    assert remote_auth.service_token_scopes(restricted_environ) == [
        "unigrok:connect",
        "unigrok:review",
    ]


def test_transport_security_settings_follow_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "UNIGROK_PUBLIC_MCP_URL", "https://mcp.example.test:8443/mcp"
    )
    monkeypatch.setenv(
        "UNIGROK_ALLOWED_ORIGINS",
        "https://control.example.test/,http://localhost:9000,not-an-origin",
    )
    settings = remote_auth.transport_security_settings()

    assert settings.enable_dns_rebinding_protection is True
    assert {"localhost", "127.0.0.1:*", "grok-mcp:*"}.issubset(settings.allowed_hosts)
    assert {
        "mcp.example.test",
        "mcp.example.test:*",
        "mcp.example.test:8443",
    }.issubset(settings.allowed_hosts)
    assert {
        "http://localhost:*",
        "https://mcp.example.test:8443",
        "https://control.example.test",
        "http://localhost:9000",
    }.issubset(settings.allowed_origins)
    assert "not-an-origin" not in settings.allowed_origins


@pytest.mark.asyncio
async def test_transport_security_enforces_runtime_host_and_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", PUBLIC_RESOURCE)
    monkeypatch.setenv("UNIGROK_ALLOWED_ORIGINS", "https://control.example.test")
    middleware = TransportSecurityMiddleware(remote_auth.transport_security_settings())

    async def validate(host: str, origin: str) -> int | None:
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "https",
                "method": "POST",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": [
                    (b"host", host.encode()),
                    (b"origin", origin.encode()),
                    (b"content-type", b"application/json"),
                ],
                "client": ("203.0.113.10", 4444),
                "server": ("mcp.example.test", 443),
            }
        )
        response = await middleware.validate_request(request, is_post=True)
        return None if response is None else response.status_code

    assert await validate("mcp.example.test", "https://control.example.test") is None
    assert await validate("attacker.example", "https://control.example.test") == 421
    assert await validate("mcp.example.test", "https://attacker.example") == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("/ui", "/runtimez", "/benchmarkz"))
async def test_app_wide_transport_security_rejects_attacker_host_on_custom_routes(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.delenv("UNIGROK_PUBLIC_MCP_URL", raising=False)
    app = server._secured_streamable_http_app()

    status, _headers, body = await _asgi_exchange(
        app,
        path=path,
        headers=((b"host", b"attacker.example:4765"),),
    )

    assert status == 421
    assert json.loads(body) == {"error": "host_not_allowed"}


@pytest.mark.parametrize(
    "headers",
    (
        ((b"host", b"localhost:4765"), (b"host", b"attacker.example:4765")),
        ((b"host", b"attacker.example:4765"), (b"host", b"localhost:4765")),
    ),
)
async def test_app_wide_transport_security_rejects_duplicate_host_headers(
    monkeypatch: pytest.MonkeyPatch,
    headers: tuple[tuple[bytes, bytes], ...],
) -> None:
    monkeypatch.delenv("UNIGROK_PUBLIC_MCP_URL", raising=False)
    app = server._secured_streamable_http_app()

    status, _response_headers, body = await _asgi_exchange(
        app,
        path="/healthz",
        headers=headers,
    )

    assert status == 421
    assert json.loads(body) == {"error": "host_not_allowed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "host"),
    (
        ("/healthz", "127.0.0.1:8080"),
        ("/ui", "localhost:4765"),
        ("/healthz", "[::1]:4765"),
        ("/healthz", "mcp.example.test"),
        ("/healthz", "mcp.example.test:443"),
    ),
)
async def test_app_wide_transport_security_allows_loopback_and_configured_hosts(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    host: str,
) -> None:
    monkeypatch.setenv("UNIGROK_PUBLIC_MCP_URL", PUBLIC_RESOURCE)
    app = server._secured_streamable_http_app()

    status, _headers, body = await _asgi_exchange(
        app,
        path=path,
        headers=((b"host", host.encode()),),
    )

    assert status == 200
    assert body


def test_transport_security_helpers_retain_only_defaults_for_invalid_public_url() -> None:
    hosts = remote_auth.fastmcp_allowed_hosts(
        {"UNIGROK_PUBLIC_MCP_URL": "http://private.invalid/mcp"}
    )
    origins = remote_auth.fastmcp_allowed_origins(
        {"UNIGROK_PUBLIC_MCP_URL": "http://private.invalid/mcp"}
    )
    assert "localhost:*" in hosts
    assert "private.invalid" not in hosts
    assert "http://localhost:*" in origins
    assert "http://private.invalid" not in origins


def test_oauth_metadata_is_current_and_does_not_link_stale_setup(
    remote_oauth_env: None,
) -> None:
    payload, status, _headers = remote_auth.oauth_metadata()
    assert status == 200
    assert payload["resource"] == PUBLIC_RESOURCE
    assert payload["authorization_servers"] == [AUTHORIZATION_SERVER]
    assert "resource_documentation" not in payload


def test_cloudrun_discovery_and_onboarding_are_runtime_accurate(
    remote_oauth_env: None,
) -> None:
    catalogs = {
        "cli": {"ready": False, "models": [], "default_model": None},
        "api": {
            "ready": True,
            "configured": True,
            "models": [{"id": "grok-test"}],
            "image_models": [],
            "default_model": "grok-test",
        },
    }
    description = server._live_self_description(catalogs)
    planes = description["credential_planes"]
    assert planes["policy"] == "api_only"
    assert planes["preferred_plane"] == "api"
    assert planes["effective_plane"] == "api"
    assert planes["degraded"] is False
    assert planes["cli"]["disabled_by_policy"] is True
    assert "CLI-first" not in description["capability_defaults"]["agent"]["note"]
    assert description["team_harness"]["durable_knowledge"] is False
    assert description["team_harness"]["state_lifetime"] == "instance_local"
    assert "ui" not in description["surfaces"]
    tools = {item["name"]: item for item in description["tools"]}
    for name in ("agent", "review_pull_request", "chat"):
        assert tools[name]["plane"] == "xAI API"
        assert tools[name]["billing_class"] == "metered"
    assert tools["agent_result"]["plane"] == "gateway job state"

    plan = server._client_onboarding_plan("cursor", "global")
    assert plan["connection"] == {
        "mode": "oauth_remote",
        "mcp_url": PUBLIC_RESOURCE,
        "authentication": "oauth_discovery",
        "client_labels_are_authentication": False,
    }
    assert plan["mcp_server"]["entry"]["mcpServers"]["grok"]["url"] == PUBLIC_RESOURCE
    assert plan["automatic_tool_approval_offered"] is False
    assert "hooks" not in plan
    assert "auto_approve" not in plan
    assert not any(
        item["path"].endswith("before-unigrok-agent.py") for item in plan["files"]
    )
    assert plan["files"] == []
    assert "Cursor Settings > Rules > User Rules" in plan["client_settings_instruction"]
    assert "instance-local" in plan["client_settings_instruction"]
    assert "durable facts" not in plan["client_settings_instruction"]
    assert description["team_harness"]["state_persistence"] is False
    assert description["team_harness"]["completion_recovery"] == (
        "one_same_plane_retry; no_cross_plane_available"
    )
    for client in server.CLIENT_ADAPTERS:
        client_plan = server._client_onboarding_plan(client, "global")
        assert client_plan["automatic_tool_approval_offered"] is False
        assert client_plan["runtime_contract"] == {
            "execution_policy": "api_only",
            "inference_billing": "metered",
            "state_lifetime": "instance_local",
        }
        assert "auto_approve" not in client_plan
    instructions = server._service_instructions()
    assert "status=pending" in instructions
    assert "poll agent_result with the same job_id" in instructions
    assert "never start a duplicate request" in instructions
    assert "CLI-first" not in instructions


@pytest.mark.asyncio
async def test_cloudrun_webmcp_labels_public_and_authenticated_surfaces(
    remote_oauth_env: None,
) -> None:
    manifest = json.loads((await server.webmcp_manifest(None)).body)
    assert manifest["mcp"]["authentication"] == "oauth"
    assert manifest["public_surfaces"]["oauth"].endswith(
        "/.well-known/oauth-protected-resource/mcp"
    )
    assert manifest["authenticated_surfaces"] == {
        "runtime": "/runtimez",
        "benchmarks": "/benchmarkz",
    }
    assert manifest["surfaces"]["health"] == "/healthz"
    assert manifest["surfaces"]["runtime"] == "/runtimez"
    assert manifest["authorization_server"] == AUTHORIZATION_SERVER
    assert manifest["control_ui"] == AUTHORIZATION_SERVER
    assert manifest["state_lifetime"] == "instance_local"
    assert "ui" not in manifest["public_surfaces"]
    assert "ui" not in manifest["authenticated_surfaces"]


@pytest.mark.asyncio
async def test_cloudrun_runtimez_reports_instance_local_state(
    remote_oauth_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def telemetry_summary(
        _self: object, *, limit: int, caller: str | None
    ) -> dict[str, Any]:
        assert limit == 1000
        assert caller is None
        return {
            "sample_size": 0,
            "verified_samples": 0,
            "verified_success_rate": None,
            "latency_ms": {},
            "cost_usd": 0.0,
            "callers": {},
            "models": {},
            "routes": {},
            "planes": {},
            "kinds": {},
            "fallbacks": {},
        }

    monkeypatch.setattr(type(server.STATE), "telemetry_summary", telemetry_summary)
    payload = json.loads((await server.runtimez(None)).body)
    assert payload["state_persistence"] is False
    assert payload["state_lifetime"] == "instance_local"
    assert payload["completion_recovery"] == (
        "one_same_plane_retry; no_cross_plane_available"
    )
    assert payload["routing_advisor"]["policy"].startswith("live_discovered_lead")


def test_cloudrun_onboarding_fails_closed_without_public_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("UNIGROK_PUBLIC_MCP_URL", raising=False)
    with pytest.raises(RuntimeError, match="UNIGROK_PUBLIC_MCP_URL"):
        server._client_onboarding_plan("cursor", "global")


def test_router_policy_does_not_pin_a_model_version() -> None:
    assert "Grok 4.5" not in server.ROUTER_SYSTEM_PROMPT


def test_cloudrun_media_unavailable_result_respects_remote_boundary(
    remote_oauth_env: None,
) -> None:
    result = server._media_unavailable_result("image")
    assert result["plane"] == "api"
    assert result["resolved_plane"] == "api"
    assert result["degraded"] is True
    assert "Contact the service operator" in result["text"]
    assert "XAI_API_KEY" not in result["text"]
    assert ".env" not in result["text"]


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    (
        ("review_pull_request", "unigrok:connect unigrok:review"),
        ("agent_result", "unigrok:connect"),
        ("grok_mcp_status", "unigrok:connect unigrok:status"),
        ("agent", "unigrok:connect unigrok:invoke"),
        ("unknown_future_tool", "unigrok:connect unigrok:invoke"),
    ),
)
def test_mcp_tool_scope_mapping_is_least_privilege(tool_name: str, expected: str) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
    ).encode()
    assert remote_auth.required_scope("/mcp", body) == expected


def test_mcp_batch_requires_union_of_tool_scopes() -> None:
    body = json.dumps(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "review_pull_request", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "agent", "arguments": {}},
            },
        ]
    ).encode()
    assert (
        remote_auth.required_scope("/mcp", body)
        == "unigrok:connect unigrok:invoke unigrok:review"
    )


async def test_oauth_middleware_denies_and_advertises_exact_scope(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[str, str]] = []

    async def deny(token: str, required: str) -> None:
        observed.append((token, required))
        return None

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", deny)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("unauthenticated request reached the MCP application")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "review_pull_request", "arguments": {}},
        }
    ).encode()
    status, headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path="/mcp",
        method="POST",
        body_chunks=(body[:20], body[20:]),
    )

    assert status == 401
    assert json.loads(response_body) == {"error": "unauthorized"}
    # Anonymous callers are rejected before the gateway buffers their MCP body.
    assert observed == [("", "unigrok:connect")]
    assert headers["www-authenticate"] == (
        'Bearer resource_metadata="https://mcp.example.test/'
        '.well-known/oauth-protected-resource/mcp", '
        'scope="unigrok:connect"'
    )


async def test_oauth_middleware_rejects_authenticated_token_without_tool_scope(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _canonical_principal("reviewer")

    async def connect_only(bearer: str, required: str) -> dict[str, Any]:
        assert bearer == "opaque-value"
        assert required == "unigrok:connect"
        return {"unigrok_principal": principal, "scope": "unigrok:connect"}

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", connect_only)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("under-scoped token reached the MCP application")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "review_pull_request", "arguments": {}},
        }
    ).encode()
    status, headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=((b"authorization", b"Bearer opaque-value"),),
    )

    assert status == 401
    assert json.loads(response_body) == {"error": "unauthorized"}
    assert headers["www-authenticate"].endswith(
        'scope="unigrok:connect unigrok:review"'
    )


async def test_oauth_middleware_replays_body_and_propagates_canonical_claims(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _canonical_principal("reviewer:42")
    observed: dict[str, Any] = {}

    async def allow(token: str, required: str) -> dict[str, Any]:
        observed["introspection"] = (token, required)
        return {"unigrok_principal": principal, "scope": required}

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", allow)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        observed["body"] = b"".join(chunks)
        observed["claims"] = scope.get("unigrok.oauth")
        await JSONResponse({"ok": True})(scope, receive, send)

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    status, _, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path="/mcp",
        method="POST",
        body_chunks=(body[:9], body[9:]),
        headers=((b"authorization", b"Bearer opaque-token"),),
    )

    assert status == 200
    assert json.loads(response_body) == {"ok": True}
    assert observed["introspection"] == ("opaque-token", "unigrok:connect")
    assert observed["body"] == body
    assert observed["claims"]["unigrok_principal"] == principal


async def test_oauth_introspection_allowlists_claims_from_hostile_payload(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "hostile-secret-marker"
    original_scope = "unigrok:status unigrok:connect"
    payload = {
        "active": True,
        "scope": original_scope,
        "iss": AUTHORIZATION_SERVER,
        "sub": "reviewer:42",
        "aud": [PUBLIC_RESOURCE],
        "unigrok_auth": marker,
        "access_token": marker,
        "client_secret": marker,
        "provider_metadata": {"private": marker},
    }

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> _JSONResponseStream:
            return _JSONResponseStream(payload)

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    claims = await remote_auth.introspect_oauth_token(
        "opaque-token", "unigrok:connect"
    )

    assert claims == {
        "active": True,
        "scope": original_scope,
        "unigrok_principal": _canonical_principal("reviewer:42"),
        "unigrok_auth": "oauth",
    }
    assert marker not in json.dumps(claims, sort_keys=True)


async def test_oauth_negative_cache_avoids_repeat_control_calls_and_keeps_tokens_secret(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_token = "invalid-cache-secret-marker-aaaaaaaaaaaaaaaa"  # noqa: S105
    valid_token = "valid-cache-secret-marker-bbbbbbbbbbbbbbbbb"  # noqa: S105
    posted: list[str] = []
    client_options: list[dict[str, Any]] = []

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> _JSONResponseStream:
            token = str(kwargs["headers"]["Authorization"]).removeprefix("Bearer ")
            posted.append(token)
            payload = {"active": False} if token == bad_token else _active_introspection_payload()
            return _JSONResponseStream(payload)

    def client_factory(**kwargs: Any) -> Client:
        client_options.append(kwargs)
        return Client()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", client_factory)
    caplog.set_level(logging.DEBUG, logger=remote_auth.__name__)

    assert await remote_auth.introspect_oauth_token(bad_token, "unigrok:connect") is None
    assert await remote_auth.introspect_oauth_token(bad_token, "unigrok:connect") is None
    valid = await remote_auth.introspect_oauth_token(valid_token, "unigrok:connect")

    assert valid is not None
    assert valid["unigrok_principal"] == _canonical_principal("reviewer")
    assert posted == [bad_token, valid_token]
    assert client_options == [
        {"timeout": 5.0, "follow_redirects": False, "trust_env": False},
        {"timeout": 5.0, "follow_redirects": False, "trust_env": False},
    ]
    retained_state = repr(
        (remote_auth._oauth_negative_cache, remote_auth._oauth_introspection_inflight)
    )
    assert bad_token not in retained_state
    assert valid_token not in retained_state
    assert bad_token not in caplog.text
    assert valid_token not in caplog.text


async def test_oauth_negative_cache_is_partitioned_by_required_scope(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_scopes: list[str] = []

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> _JSONResponseStream:
            posted_scopes.append(str(kwargs["data"]["required_scope"]))
            return _JSONResponseStream({"active": False})

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    token = "scope-partitioned-invalid-token"  # noqa: S105

    assert await remote_auth.introspect_oauth_token(token, "unigrok:connect") is None
    assert await remote_auth.introspect_oauth_token(token, "unigrok:connect") is None
    assert await remote_auth.introspect_oauth_token(token, "unigrok:status") is None

    assert posted_scopes == ["unigrok:connect", "unigrok:status"]
    assert len(remote_auth._oauth_negative_cache) == 2


async def test_oauth_introspection_concurrency_is_bounded_for_distinct_lookups(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_auth, "_OAUTH_INTROSPECTION_MAX_INFLIGHT", 2)
    release = asyncio.Event()
    two_started = asyncio.Event()
    posted: list[str] = []
    active = 0
    maximum_active = 0

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            nonlocal active
            try:
                await release.wait()
            finally:
                active -= 1
            yield b'{"active": false}'

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> Response:
            nonlocal active, maximum_active
            token = str(kwargs["headers"]["Authorization"]).removeprefix("Bearer ")
            posted.append(token)
            active += 1
            maximum_active = max(maximum_active, active)
            if len(posted) == 2:
                two_started.set()
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    first = asyncio.create_task(
        remote_auth.introspect_oauth_token("bounded-token-a", "unigrok:connect")
    )
    second = asyncio.create_task(
        remote_auth.introspect_oauth_token("bounded-token-b", "unigrok:connect")
    )
    await asyncio.wait_for(two_started.wait(), timeout=1.0)

    capacity = await remote_auth.introspect_oauth_token(
        "bounded-token-c", "unigrok:connect"
    )
    assert isinstance(capacity, remote_auth.OAuthIntrospectionCapacity)
    assert capacity.retry_after_seconds == 1
    assert posted == ["bounded-token-a", "bounded-token-b"]

    release.set()
    await asyncio.gather(first, second)
    assert maximum_active == 2
    assert remote_auth._oauth_introspection_inflight == {}


@pytest.mark.parametrize(
    ("path", "method", "body_chunks"),
    (
        (
            "/mcp",
            "POST",
            (b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',),
        ),
        ("/runtimez", "GET", (b"",)),
    ),
)
async def test_oauth_introspection_capacity_is_temporary_unavailability_not_401(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    method: str,
    body_chunks: tuple[bytes, ...],
) -> None:
    monkeypatch.setattr(remote_auth, "_OAUTH_INTROSPECTION_MAX_INFLIGHT", 1)
    held_token = "held-capacity-secret-marker"  # noqa: S105
    rejected_token = "rejected-capacity-secret-marker"  # noqa: S105
    started = asyncio.Event()
    release = asyncio.Event()
    posted: list[str] = []
    downstream_called = False

    class Response(_JSONResponseStream):
        async def aiter_raw(self) -> AsyncIterator[bytes]:
            await release.wait()
            yield b'{"active": false}'

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> Response:
            token = str(kwargs["headers"]["Authorization"]).removeprefix("Bearer ")
            posted.append(token)
            started.set()
            return Response({"active": False})

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True
        await JSONResponse({"ok": True})(scope, receive, send)

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    leader = asyncio.create_task(
        remote_auth.introspect_oauth_token(held_token, "unigrok:connect")
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    status, headers, body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path=path,
        method=method,
        body_chunks=body_chunks,
        headers=((b"authorization", f"Bearer {rejected_token}".encode()),),
    )

    assert status == 503
    assert json.loads(body) == {"error": "oauth_introspection_capacity"}
    assert headers["retry-after"] == "1"
    assert "www-authenticate" not in headers
    assert posted == [held_token]
    assert downstream_called is False
    assert held_token not in body.decode()
    assert rejected_token not in body.decode()
    assert held_token not in caplog.text
    assert rejected_token not in caplog.text

    release.set()
    assert await leader is None


@pytest.mark.parametrize(
    ("path", "method", "body_chunks", "expected_scope"),
    (
        (
            "/mcp",
            "POST",
            (b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',),
            "unigrok:connect",
        ),
        ("/runtimez", "GET", (b"",), "unigrok:status"),
    ),
)
async def test_oauth_introspection_unavailable_is_503_on_both_middleware_paths(
    remote_oauth_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    method: str,
    body_chunks: tuple[bytes, ...],
    expected_scope: str,
) -> None:
    token = "middleware-unavailable-secret-marker"  # noqa: S105
    observed: list[tuple[str, str]] = []
    downstream_called = False

    async def unavailable(
        received_token: str, required: str
    ) -> remote_auth.OAuthIntrospectionUnavailable:
        observed.append((received_token, required))
        return remote_auth.OAuthIntrospectionUnavailable(retry_after_seconds=2)

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True
        await JSONResponse({"ok": True})(scope, receive, send)

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", unavailable)
    caplog.set_level(logging.DEBUG)

    status, headers, body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path=path,
        method=method,
        body_chunks=body_chunks,
        headers=((b"authorization", f"Bearer {token}".encode()),),
    )

    assert status == 503
    assert json.loads(body) == {"error": "oauth_introspection_unavailable"}
    assert headers["retry-after"] == "2"
    assert "www-authenticate" not in headers
    assert observed == [(token, expected_scope)]
    assert downstream_called is False
    assert token not in body.decode()
    assert token not in caplog.text


async def test_same_valid_token_concurrent_introspection_is_coalesced(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "coalesced-valid-token"  # noqa: S105
    started = asyncio.Event()
    release = asyncio.Event()
    posts = 0

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            await release.wait()
            yield json.dumps(
                _active_introspection_payload("coalesced-reviewer")
            ).encode()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal posts
            posts += 1
            started.set()
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    leader = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    follower = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.sleep(0)
    assert not follower.done()
    assert posts == 1

    release.set()
    leader_result, follower_result = await asyncio.gather(leader, follower)
    assert leader_result == follower_result
    assert leader_result is not None
    assert leader_result["unigrok_principal"] == _canonical_principal(
        "coalesced-reviewer"
    )
    assert posts == 1


async def test_same_token_transient_failure_is_coalesced_as_unavailable(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "coalesced-unavailable-secret-marker"  # noqa: S105
    started = asyncio.Event()
    release = asyncio.Event()
    posts = 0

    class Response:
        status_code = 503
        headers: dict[str, str] = {}

        async def __aenter__(self) -> Response:
            await release.wait()
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            raise AssertionError("a non-200 introspection body must not be read")
            yield b""  # pragma: no cover

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal posts
            posts += 1
            started.set()
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    leader = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    follower = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.sleep(0)
    assert not follower.done()

    release.set()
    leader_result, follower_result = await asyncio.gather(leader, follower)

    assert isinstance(leader_result, remote_auth.OAuthIntrospectionUnavailable)
    assert follower_result is leader_result
    assert posts == 1
    assert remote_auth._oauth_negative_cache == {}
    assert remote_auth._oauth_introspection_inflight == {}
    assert token not in caplog.text


async def test_cancelled_introspection_leader_releases_follower_as_unavailable(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "cancelled-leader-secret-marker"  # noqa: S105
    started = asyncio.Event()
    posts = 0

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self) -> Response:
            await asyncio.Event().wait()
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            yield b"{}"

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal posts
            posts += 1
            started.set()
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    leader = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    follower = asyncio.create_task(
        remote_auth.introspect_oauth_token(token, "unigrok:connect")
    )
    await asyncio.sleep(0)
    assert not follower.done()

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    follower_result = await asyncio.wait_for(follower, timeout=1.0)

    assert isinstance(follower_result, remote_auth.OAuthIntrospectionUnavailable)
    assert posts == 1
    assert remote_auth._oauth_negative_cache == {}
    assert remote_auth._oauth_introspection_inflight == {}


async def test_bad_inflight_token_does_not_block_distinct_valid_token(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_auth, "_OAUTH_INTROSPECTION_MAX_INFLIGHT", 2)
    bad_token = "held-invalid-token"  # noqa: S105
    valid_token = "distinct-valid-token"  # noqa: S105
    bad_started = asyncio.Event()
    release_bad = asyncio.Event()
    posted: list[str] = []

    class Response(_JSONResponseStream):
        def __init__(self, payload: dict[str, Any], *, wait_for_bad: bool) -> None:
            super().__init__(payload)
            self.payload = payload
            self.wait_for_bad = wait_for_bad

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            if self.wait_for_bad:
                await release_bad.wait()
            yield json.dumps(self.payload).encode()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> Response:
            token = str(kwargs["headers"]["Authorization"]).removeprefix("Bearer ")
            posted.append(token)
            if token == bad_token:
                bad_started.set()
                return Response({"active": False}, wait_for_bad=True)
            return Response(
                _active_introspection_payload("distinct-reviewer"), wait_for_bad=False
            )

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    bad = asyncio.create_task(
        remote_auth.introspect_oauth_token(bad_token, "unigrok:connect")
    )
    await asyncio.wait_for(bad_started.wait(), timeout=1.0)

    duplicate_bad = asyncio.create_task(
        remote_auth.introspect_oauth_token(bad_token, "unigrok:connect")
    )
    await asyncio.sleep(0)
    assert not duplicate_bad.done()
    valid = await remote_auth.introspect_oauth_token(valid_token, "unigrok:connect")
    assert valid is not None
    assert valid["unigrok_principal"] == _canonical_principal("distinct-reviewer")
    assert posted == [bad_token, valid_token]

    release_bad.set()
    assert await bad is None
    assert await duplicate_bad is None


async def test_oauth_negative_cache_ttl_and_size_are_bounded(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    posted: list[str] = []
    monkeypatch.setattr(remote_auth.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(remote_auth, "_OAUTH_NEGATIVE_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(remote_auth, "_OAUTH_NEGATIVE_CACHE_MAX_ENTRIES", 2)

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **kwargs: Any) -> _JSONResponseStream:
            posted.append(str(kwargs["headers"]["Authorization"]))
            return _JSONResponseStream({"active": False})

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    for token in ("cache-token-a", "cache-token-b", "cache-token-c"):
        assert await remote_auth.introspect_oauth_token(token, "unigrok:connect") is None
    assert len(remote_auth._oauth_negative_cache) == 2

    assert await remote_auth.introspect_oauth_token("cache-token-a", "unigrok:connect") is None
    assert len(posted) == 4
    assert len(remote_auth._oauth_negative_cache) == 2

    clock[0] += 6.0
    assert await remote_auth.introspect_oauth_token("cache-token-c", "unigrok:connect") is None
    assert len(posted) == 5
    assert len(remote_auth._oauth_negative_cache) <= 2


async def test_transient_introspection_failure_is_never_negative_cached(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> None:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("control unavailable")

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    token = "transient-failure-secret-marker"  # noqa: S105
    caplog.set_level(logging.DEBUG)

    first = await remote_auth.introspect_oauth_token(token, "unigrok:connect")
    second = await remote_auth.introspect_oauth_token(token, "unigrok:connect")
    assert isinstance(first, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(second, remote_auth.OAuthIntrospectionUnavailable)
    assert attempts == 2
    assert remote_auth._oauth_negative_cache == {}
    retained_state = repr(
        (remote_auth._oauth_negative_cache, remote_auth._oauth_introspection_inflight)
    )
    assert token not in retained_state
    assert token not in caplog.text


@pytest.mark.parametrize(
    ("case", "status_code", "chunks"),
    (
        ("non_200", 502, (b'{"active": false}',)),
        ("malformed_json", 200, (b'{"active":',)),
        ("non_object_json", 200, (b"[]",)),
        ("invalid_active", 200, (b'{"active": "yes"}',)),
    ),
)
async def test_oauth_introspection_protocol_failures_are_unavailable_not_denials(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    status_code: int,
    chunks: tuple[bytes, ...],
) -> None:
    attempts = 0
    body_reads = 0
    token = f"{case}-protocol-secret-marker"  # noqa: S105

    class Response:
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self.status_code = status_code

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            nonlocal body_reads
            body_reads += 1
            for chunk in chunks:
                yield chunk

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal attempts
            attempts += 1
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)

    first = await remote_auth.introspect_oauth_token(token, "unigrok:connect")
    second = await remote_auth.introspect_oauth_token(token, "unigrok:connect")

    assert isinstance(first, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(second, remote_auth.OAuthIntrospectionUnavailable)
    assert attempts == 2
    assert body_reads == (0 if status_code != 200 else 2)
    assert remote_auth._oauth_negative_cache == {}
    assert token not in repr(remote_auth._oauth_introspection_inflight)
    assert token not in caplog.text


async def test_oauth_introspection_absolute_deadline_covers_slow_stream(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        remote_auth, "_OAUTH_INTROSPECTION_DEADLINE_SECONDS", 0.02
    )
    attempts = 0
    closed = 0
    token = "slow-stream-secret-marker"  # noqa: S105

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            nonlocal closed
            closed += 1

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            yield b"{"
            await asyncio.Event().wait()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            nonlocal attempts
            attempts += 1
            return Response()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)

    first = await asyncio.wait_for(
        remote_auth.introspect_oauth_token(token, "unigrok:connect"), timeout=0.5
    )
    second = await asyncio.wait_for(
        remote_auth.introspect_oauth_token(token, "unigrok:connect"), timeout=0.5
    )

    assert isinstance(first, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(second, remote_auth.OAuthIntrospectionUnavailable)
    assert attempts == 2
    assert closed == 2
    assert remote_auth._oauth_negative_cache == {}
    assert remote_auth._oauth_introspection_inflight == {}
    assert token not in caplog.text


async def test_oauth_introspection_rejects_compression_and_caps_streamed_bytes(
    remote_oauth_env: None,
    clean_oauth_introspection_state: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    compressed_token = "compressed-stream-secret-marker"  # noqa: S105
    oversized_token = "oversized-stream-secret-marker"  # noqa: S105
    compressed_body_read = False
    oversized_chunks: list[int] = []
    accept_encodings: list[str] = []

    class CompressedResponse:
        status_code = 200
        headers = {"content-encoding": "gzip"}

        async def __aenter__(self) -> CompressedResponse:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            nonlocal compressed_body_read
            compressed_body_read = True
            yield b"compressed-body-must-not-be-read"

    class OversizedResponse:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self) -> OversizedResponse:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            for chunk in (b"x" * 8_192, b"y" * 8_192, b"z", b"unreachable"):
                oversized_chunks.append(len(chunk))
                yield chunk

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(
            self, *_args: Any, **kwargs: Any
        ) -> CompressedResponse | OversizedResponse:
            accept_encodings.append(str(kwargs["headers"]["Accept-Encoding"]))
            token = str(kwargs["headers"]["Authorization"]).removeprefix("Bearer ")
            return CompressedResponse() if token == compressed_token else OversizedResponse()

    monkeypatch.setattr(remote_auth.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)

    compressed = await remote_auth.introspect_oauth_token(
        compressed_token, "unigrok:connect"
    )
    compressed_again = await remote_auth.introspect_oauth_token(
        compressed_token, "unigrok:connect"
    )
    oversized = await remote_auth.introspect_oauth_token(
        oversized_token, "unigrok:connect"
    )
    oversized_again = await remote_auth.introspect_oauth_token(
        oversized_token, "unigrok:connect"
    )

    assert isinstance(compressed, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(compressed_again, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(oversized, remote_auth.OAuthIntrospectionUnavailable)
    assert isinstance(oversized_again, remote_auth.OAuthIntrospectionUnavailable)
    assert compressed_body_read is False
    assert oversized_chunks == [8_192, 8_192, 1] * 2
    assert accept_encodings == ["identity"] * 4
    assert remote_auth._oauth_negative_cache == {}
    assert compressed_token not in caplog.text
    assert oversized_token not in caplog.text


async def test_oauth_middleware_logs_constant_route_provenance(
    remote_oauth_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "hostile-auth-kind-marker"
    principal = _canonical_principal("reviewer:42")

    async def hostile_claims(_token: str, required: str) -> dict[str, Any]:
        return {
            "active": True,
            "scope": required,
            "unigrok_principal": principal,
            "unigrok_auth": marker,
        }

    monkeypatch.setattr(remote_auth, "introspect_oauth_token", hostile_claims)
    caplog.set_level(logging.INFO, logger=remote_auth.__name__)

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await JSONResponse({"ok": True})(scope, receive, send)

    status, _, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(downstream),
        path="/v1/models",
        headers=((b"authorization", b"Bearer opaque-token"),),
    )

    assert status == 200
    assert json.loads(response_body) == {"ok": True}
    assert "oauth access_allowed" in caplog.text
    assert marker not in caplog.text


def test_observability_identifiers_exclude_raw_auth_material(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _canonical_principal("privacy-marker-subject")
    label = principal_label(principal)
    assert label == principal_label(principal)
    assert label is not None
    assert principal not in label
    assert "privacy-marker-subject" not in label
    assert AUTHORIZATION_SERVER not in label

    credential_marker = "synthetic-credential-marker-xxxxxxxxxxxxxxxx"
    monkeypatch.setenv("XAI_API_KEY", credential_marker)
    monkeypatch.delenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raising=False)
    cache_key = xai_api.credential_cache_key()
    assert credential_marker not in cache_key
    assert cache_key.startswith("owner_default:")


def test_principal_key_selection_never_crosses_oauth_tenants(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = _canonical_principal("alice")
    bob = _canonical_principal("bob")
    monkeypatch.setenv("XAI_API_KEY", "owner-test-key")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({alice: "alice-test-key", bob: "bob-test-key"}),
    )

    assert resolve_xai_api_key(principal=alice) == ("alice-test-key", "principal")
    assert resolve_xai_api_key(principal=bob) == ("bob-test-key", "principal")
    assert resolve_xai_api_key(principal=_canonical_principal("carol")) == (
        "owner-test-key",
        "owner_default:XAI_API_KEY",
    )


def test_principal_key_selection_uses_the_supplied_environment_consistently(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = _canonical_principal("alice")
    supplied = {
        "UNIGROK_OAUTH_AUTHORIZATION_SERVERS": AUTHORIZATION_SERVER,
        "XAI_API_KEY": "owner-test-key",
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON": json.dumps(
            {alice: "alice-test-key"}
        ),
    }
    monkeypatch.delenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS")

    assert resolve_xai_api_key(principal=alice, environ=supplied) == (
        "alice-test-key",
        "principal",
    )


def test_principal_key_table_rejects_duplicates_and_foreign_issuers(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = _canonical_principal("alice")
    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        f'{{"{alice}":"first-test-key","{alice}":"second-test-key"}}',
    )
    with pytest.raises(PrincipalXAIConfigurationError) as duplicate:
        load_principal_key_table()
    assert duplicate.value.code == "duplicate_principal"

    monkeypatch.setenv(
        "UNIGROK_PRINCIPAL_XAI_KEYS_JSON",
        json.dumps({"oauth:https%3A%2F%2Fevil.example.test:alice": "test-key"}),
    )
    with pytest.raises(PrincipalXAIConfigurationError) as foreign:
        load_principal_key_table()
    assert foreign.value.code == "invalid_principal"


async def test_tenant_sessions_and_knowledge_are_isolated(
    tmp_path: Path, remote_oauth_env: None
) -> None:
    store = PublicStateStore(tmp_path / "tenant-state.db")
    alice = _canonical_principal("alice")
    bob = _canonical_principal("bob")

    alice_token = set_active_principal(alice)
    try:
        alice_prefix = tenant_prefix()
        alice_session = scoped_session("shared-name")
        alice_global = scoped_scope("global")
        alice_project = scoped_scope("project")
        assert public_state_name(alice_session) == "shared-name"
    finally:
        reset_active_principal(alice_token)

    bob_token = set_active_principal(bob)
    try:
        bob_prefix = tenant_prefix()
        bob_session = scoped_session("shared-name")
        bob_global = scoped_scope("global")
        bob_project = scoped_scope("project")
        with pytest.raises(ValueError, match="does not belong"):
            scoped_session(alice_session)
    finally:
        reset_active_principal(bob_token)

    assert alice_prefix and bob_prefix and alice_prefix != bob_prefix
    await store.append_turn(alice_session, "alice question", "alice answer")
    await store.append_turn(bob_session, "bob question", "bob answer")
    alice_fact = await store.save_fact("shared keyword alice", scope=alice_global)
    await store.save_fact("shared keyword alice project", scope=alice_project)
    bob_fact = await store.save_fact("shared keyword bob", scope=bob_global)
    await store.save_fact("shared keyword bob project", scope=bob_project)

    alice_sessions = await store.list_sessions(prefix=alice_prefix)
    bob_sessions = await store.list_sessions(prefix=bob_prefix)
    assert [item["name"] for item in alice_sessions] == [alice_session]
    assert [item["name"] for item in bob_sessions] == [bob_session]

    alice_results = await store.search_facts("shared keyword", scope=alice_project, limit=25)
    bob_results = await store.search_facts("shared keyword", scope=bob_project, limit=25)
    assert {item["scope"] for item in alice_results} == {alice_global, alice_project}
    assert {item["scope"] for item in bob_results} == {bob_global, bob_project}
    assert all("bob" not in item["fact"] for item in alice_results)
    assert all("alice" not in item["fact"] for item in bob_results)

    assert await store.delete_fact(bob_fact, scope_prefix=alice_prefix) is False
    assert await store.delete_fact(alice_fact, scope_prefix=alice_prefix) is True
    assert await store.delete_fact(bob_fact, scope_prefix=bob_prefix) is True


async def test_agent_jobs_are_bound_to_their_authenticated_owner(
    tmp_path: Path, remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublicStateStore(tmp_path / "owner-state.db")
    monkeypatch.setattr(server, "STATE", store)
    server._DURABLE_JOBS.clear()
    job_id = "a" * 32
    alice = _canonical_principal("alice")
    bob = _canonical_principal("bob")
    alice_owner = principal_label(alice)
    bob_owner = principal_label(bob)
    assert alice_owner and bob_owner and alice_owner != bob_owner

    await store.save_agent_job(
        job_id,
        "complete",
        {"status": "complete", "job_id": job_id, "text": "private result"},
        owner=alice_owner,
    )
    # Later writes must not transfer an existing job to a different principal.
    await store.save_agent_job(
        job_id,
        "complete",
        {"status": "complete", "job_id": job_id, "text": "updated result"},
        owner=bob_owner,
    )
    assert await store.load_agent_job(job_id, owner=bob_owner) is None

    bob_token = set_active_principal(bob)
    try:
        with pytest.raises(ValueError, match="not found or has expired"):
            await server.agent_result(job_id)
    finally:
        reset_active_principal(bob_token)

    alice_token = set_active_principal(alice)
    try:
        result = await server.agent_result(job_id)
    finally:
        reset_active_principal(alice_token)
    assert result["text"] == "updated result"


async def test_agent_job_owner_column_migrates_existing_state(tmp_path: Path) -> None:
    path = tmp_path / "legacy-owner-state.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE agent_jobs ("
            "job_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
            "status TEXT NOT NULL, payload TEXT)"
        )
        connection.execute(
            "INSERT INTO agent_jobs(job_id, created_at, status, payload) "
            "VALUES (?, ?, ?, ?)",
            ("b" * 32, "2026-07-19T00:00:00+00:00", "complete", "{}"),
        )
        connection.commit()

    store = PublicStateStore(path)
    await store.initialize()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_jobs)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(agent_jobs)")}
    assert "owner" in columns
    assert "agent_jobs_owner" in indexes
    assert await store.load_agent_job("b" * 32, owner="some-owner") is None


# --- Service tokens (GitHub Copilot / non-OAuth automation) ---


@pytest.fixture
def service_token_env(monkeypatch: pytest.MonkeyPatch) -> str:
    # Synthetic fixture secret (not a production credential).
    token = "copilot-test-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"  # noqa: S105
    monkeypatch.setenv("UNIGROK_SERVICE_TOKENS", token)
    monkeypatch.setenv("UNIGROK_SERVICE_TOKEN_LABEL", "github-copilot")
    monkeypatch.setenv(
        "UNIGROK_SERVICE_TOKEN_SCOPES",
        "unigrok:connect,unigrok:invoke,unigrok:review,unigrok:status,unigrok:chat",
    )
    # Local-style enforcement without OAuth introspection.
    monkeypatch.delenv("UNIGROK_OAUTH_INTROSPECTION_URL", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    return token


def test_service_token_match_is_constant_time_and_labeled(
    service_token_env: str,
) -> None:
    claims = remote_auth.match_service_token(service_token_env)
    assert claims is not None
    assert claims["unigrok_auth"] == "service_token"
    assert claims["unigrok_principal"] == "service:github-copilot"
    assert "unigrok:connect" in claims["scope"]
    assert "unigrok:invoke" in claims["scope"]
    assert remote_auth.match_service_token("wrong-token-xxxxxxxxxxxxxxxxxxxxxxxx") is None
    assert remote_auth.match_service_token(None) is None


def test_service_token_sha256_env_matches_without_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    token = "copilot-hash-token-cccccccccccccccccccccccccccc"  # noqa: S105
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    monkeypatch.delenv("UNIGROK_SERVICE_TOKENS", raising=False)
    monkeypatch.setenv("UNIGROK_SERVICE_TOKEN_SHA256", digest)
    monkeypatch.setenv("UNIGROK_SERVICE_TOKEN_LABEL", "ci")
    claims = remote_auth.match_service_token(token)
    assert claims is not None
    assert claims["unigrok_principal"] == "service:ci"
    assert remote_auth.match_service_token(token + "x") is None


def test_auth_enforcement_active_with_service_tokens_only(
    service_token_env: str,
) -> None:
    assert remote_auth.service_tokens_configured() is True
    assert remote_auth.auth_enforcement_active() is True
    assert remote_auth.introspection_url() is None


@pytest.mark.asyncio
async def test_service_token_middleware_allows_tools_call(
    service_token_env: str,
) -> None:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["claims"] = scope.get("unigrok.oauth")
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "chat", "arguments": {"prompt": "hi"}},
        }
    ).encode()
    status, headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=((b"authorization", f"Bearer {service_token_env}".encode()),),
    )
    assert status == 200
    assert seen["claims"]["unigrok_auth"] == "service_token"
    assert json.loads(response_body)["ok"] is True


@pytest.mark.asyncio
async def test_service_token_middleware_denies_anonymous(
    service_token_env: str,
) -> None:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    status, headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',),
        headers=(),
    )
    assert status == 401
    assert json.loads(response_body)["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_service_token_works_alongside_oauth(
    remote_oauth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "copilot-dual-token-dddddddddddddddddddddddddddd"  # noqa: S105
    monkeypatch.setenv("UNIGROK_SERVICE_TOKENS", token)
    monkeypatch.setenv("UNIGROK_SERVICE_TOKEN_LABEL", "github-copilot")

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        response = JSONResponse(
            {
                "principal": (scope.get("unigrok.oauth") or {}).get("unigrok_principal"),
                "auth": (scope.get("unigrok.oauth") or {}).get("unigrok_auth"),
            }
        )
        await response(scope, receive, send)

    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    status, _headers, response_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=((b"authorization", f"Bearer {token}".encode()),),
    )
    assert status == 200
    payload = json.loads(response_body)
    assert payload["auth"] == "service_token"
    assert payload["principal"] == "service:github-copilot"


@pytest.mark.asyncio
async def test_valid_local_token_bypasses_peer_failure_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "local-throttle-token-cccccccccccccccccccccccc"  # noqa: S105
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_OAUTH_INTROSPECTION_URL", raising=False)
    monkeypatch.delenv("UNIGROK_SERVICE_TOKENS", raising=False)
    monkeypatch.delenv("UNIGROK_SERVICE_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_MCP_TOKEN_SHA256", raising=False)
    monkeypatch.setenv("UNIGROK_LOCAL_MCP_TOKEN", token)
    monkeypatch.setattr(remote_auth, "_AUTH_FAILURE_LIMIT", 2)
    remote_auth._auth_failures.clear()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        response = JSONResponse({"auth": (scope.get("unigrok.oauth") or {}).get("unigrok_auth")})
        await response(scope, receive, send)

    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    wrong_header = ((b"authorization", b"Bearer wrong-token-xxxxxxxxxxxxxxxxxxxx"),)
    first_status, _headers, _body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=wrong_header,
    )
    limited_status, _headers, limited_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=wrong_header,
    )
    assert first_status == 401
    assert limited_status == 429
    assert json.loads(limited_body) == {"error": "too_many_auth_failures"}

    valid_status, _headers, valid_body = await _asgi_exchange(
        remote_auth.RemoteOAuthMiddleware(app),
        path="/mcp",
        method="POST",
        body_chunks=(body,),
        headers=((b"authorization", f"Bearer {token}".encode()),),
    )
    assert valid_status == 200
    assert json.loads(valid_body) == {"auth": "local_mcp_token"}
    assert remote_auth._auth_failures == {}
