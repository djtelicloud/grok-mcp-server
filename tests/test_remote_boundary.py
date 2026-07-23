from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from starlette.responses import JSONResponse

from unigrok_public import remote_auth, server
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
    cursor_rule = next(
        item["content"]
        for item in plan["files"]
        if item["path"].endswith("using-unigrok.mdc")
    )
    assert "instance-local" in cursor_rule
    assert "durable facts" not in cursor_rule
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
