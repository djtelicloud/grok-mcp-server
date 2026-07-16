"""
tests/test_server.py

Unit and integration tests for FastMCP tool endpoints in src/server.py.
"""

import json
import os
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

# Set dummy key before importing src
os.environ.setdefault("XAI_API_KEY", "xai-test-dummy-key-for-unit-tests")

from src.server import (
    grok_mcp_status,
    list_chat_sessions,
    clear_chat_history,
    agent,
    chat,
    grok_reflect,
    GrokReflectionResult,
    chat_with_files,
    xai_upload_file,
    xai_list_files,
    xai_get_file,
)
from src.utils import MetaLayer


@pytest.mark.asyncio
async def test_grok_mcp_status():
    """grok_mcp_status must return version, git HEAD, project paths, and SQLite session metrics."""
    cli_status = {
        "state": "ready",
        "auth": "oauth_verified",
        "setup_command": "docker exec -it grok-mcp-server env -u XAI_API_KEY -u GROK_API_KEY grok login --device-auth",
    }
    with patch("asyncio.create_subprocess_exec") as mock_exec, patch(
        "src.tools.system.grok_cli_plane_status", return_value=cli_status
    ):
        mock_proc_git = AsyncMock()
        mock_proc_git.returncode = 0
        mock_proc_git.communicate.return_value = (b"abcdefg\n", b"")
        mock_exec.return_value = mock_proc_git
        
        res = await grok_mcp_status()
        
        assert "# UniGrok MCP Server Status" in res
        assert "Server Version" in res
        assert "abcdefg" in res
        assert "ready (oauth_verified)" in res
        assert "docker exec -it grok-mcp-server" in res
        assert "Active Database Sessions" in res
        # Runtime concurrency wiring: timed-thread counters + breaker state
        assert "Timed Threads In Flight" in res
        assert "Timed Threads Peak" in res
        assert "Circuit Breakers" in res
        # Telemetry-informed borderline prior surface (RoutingAdvisor view)
        assert "Routing Advisor" in res
        assert "static prior" in res


@pytest.mark.asyncio
async def test_grok_mcp_status_json_view_is_structured(monkeypatch):
    from datetime import datetime
    import src.tools.system as system_module

    rows = [{
        "created_at": datetime.now().isoformat(),
        "chosen_plane": "API",
        "success": 1,
        "latency": 1.25,
        "cost": 0.004,
        "metadata": '{"model":"grok-4.5","tokens":42,"token_kind":"provider_exact"}',
    }]
    monkeypatch.setattr(system_module.store, "get_telemetry_stats", AsyncMock(return_value=rows))
    monkeypatch.setattr(
        system_module,
        "fetch_provider_api_usage",
        AsyncMock(return_value={"state": "not_configured", "usage_usd": None}),
    )
    monkeypatch.setattr(
        system_module,
        "grok_cli_plane_status",
        lambda **_: {
            "state": "ready", "auth": "oauth_verified",
            "setup_command": "docker exec auth",
        },
    )

    result = json.loads(await grok_mcp_status(view="json"))

    assert result["schema_version"] == 3
    assert result["usage"]["today"]["summary"]["requests"] == 1
    assert result["usage"]["today"]["summary"]["api_cost_usd"] == pytest.approx(0.004)
    assert result["credential_planes"]["version"] == 1
    assert result["credential_planes"]["local_usage"]["cli_requests_tracked"] is True


@pytest.mark.asyncio
async def test_list_chat_sessions_empty():
    """list_chat_sessions should indicate if no sessions exist."""
    with patch("src.server.store.list_sessions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = []
        res = await list_chat_sessions()
        assert "No chat sessions found." in res


@pytest.mark.asyncio
async def test_list_chat_sessions_populated():
    """list_chat_sessions lists stored sessions correctly."""
    with patch("src.server.store.list_sessions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [
            {"session_name": "test-sess", "model": "grok-4.3", "last_active": "2026-06-27T00:00:00"}
        ]
        res = await list_chat_sessions()
        assert "test-sess" in res
        assert "grok-4.3" in res


@pytest.mark.asyncio
async def test_list_chat_sessions_filters_to_active_principal():
    """With a bound principal, other principals' session rows must not leak."""
    from src.identity import reset_active_principal, set_active_principal

    token = set_active_principal("oauth:github:42")
    try:
        with patch("src.server.store.list_sessions", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [
                {
                    "session_name": "oauth%3Agithub%3A42:vscode:mine",
                    "model": "grok-4.5",
                    "last_active": "2026-07-16T00:00:00",
                },
                {
                    "session_name": "oauth%3Agithub%3A99:vscode:theirs",
                    "model": "grok-4.3",
                    "last_active": "2026-07-16T00:00:01",
                },
                {
                    "session_name": "http%3Aanon:cursor:shared",
                    "model": "grok-composer-2.5-fast",
                    "last_active": "2026-07-16T00:00:02",
                },
            ]
            res = await list_chat_sessions()
        assert "oauth%3Agithub%3A42:vscode:mine" in res
        assert "oauth%3Agithub%3A99:vscode:theirs" not in res
        assert "http%3Aanon:cursor:shared" not in res
        assert "grok-4.5" in res
        assert "grok-4.3" not in res
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_clear_chat_history():
    """clear_chat_history clears local SQLite store and JSON backups."""
    with patch("src.server.store.delete_session", new_callable=AsyncMock) as mock_delete, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.unlink") as mock_unlink:
        
        res = await clear_chat_history(session="my-session")
        
        mock_delete.assert_awaited_once_with("my-session")
        mock_unlink.assert_called_once()
        assert "Cleared history for session" in res


@pytest.mark.asyncio
async def test_chat_routes_correctly(monkeypatch):
    """chat tool must orchestrate parameters correctly and produce signature
    output when the (default-off) footer is explicitly enabled."""
    monkeypatch.setenv("GROK_MCP_ENABLE_SIGNATURE", "1")
    mock_layer = MetaLayer(
        plan="Plan test",
        reasoning="Reason test",
        generation="Grok final output",
        reflection="Reflection test",
        plane="API",
        latency=0.5,
    )
    
    with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
        mock_orchestrate.return_value = mock_layer
        
        res = await chat(prompt="hello grok", session="test-sess", model="grok-4.3")
        
        mock_orchestrate.assert_awaited_once()
        assert "Grok final output" in res.response
        assert "Used: grok-4.3 (API)" in res.text
        assert "Context:" in res.text
        assert res.finish_reason == "unknown"
        assert res.cost_usd == 0.0


@pytest.mark.asyncio
async def test_chat_forwards_agent_count():
    """chat must pass multi-agent count through to orchestration."""
    mock_layer = MetaLayer(generation="Multi-agent answer", plane="API", latency=0.1)

    with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
        mock_orchestrate.return_value = mock_layer

        res = await chat(
            prompt="solve together",
            model="grok-4.20-multi-agent",
            agent_count=16,
        )

        args, kwargs = mock_orchestrate.call_args
        assert kwargs.get("agent_count") == 16
        assert "Multi-agent answer" in res.response


@pytest.mark.asyncio
async def test_chat_rejects_agent_count_for_wrong_model():
    with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
        res = await chat(prompt="hello", model="grok-4.3", agent_count=4)

    mock_orchestrate.assert_not_awaited()
    assert "Input Validation Error" in res.response


@pytest.mark.asyncio
async def test_chat_defaults_to_agentic():
    """chat absorbed agentic_chat: enable_agentic now defaults to True, and
    enable_agentic=False still forces the toolless fast path."""
    mock_layer = MetaLayer(generation="Agent final output", plane="API", latency=0.8)

    with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
        mock_orchestrate.return_value = mock_layer

        res = await chat(prompt="complex query", session="test-sess", model="grok-4.3")

        mock_orchestrate.assert_awaited_once()
        args, kwargs = mock_orchestrate.call_args
        assert kwargs.get("enable_agentic") is True
        assert "Agent final output" in res.response

    with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
        mock_orchestrate.return_value = mock_layer

        await chat(prompt="tiny question", enable_agentic=False)

        _, kwargs = mock_orchestrate.call_args
        assert kwargs.get("enable_agentic") is False


@pytest.mark.asyncio
async def test_chat_cli_plane_resets_api_thread_id():
    """A CLI-plane turn appends local history without touching the upstream
    thread, so it must RESET the session's api_thread_id to the placeholder —
    otherwise the next server-state agent turn would continue a stale thread
    that never saw this exchange (silent conversation-context loss)."""
    from src.utils import store

    await store.save_session("cli-reset-sess", api_thread_id="resp-stale", model="grok-4.3")
    mock_layer = MetaLayer(generation="cli answer", plane="CLI", latency=0.1)
    try:
        with patch("src.server.orchestrate", new_callable=AsyncMock) as mock_orchestrate:
            mock_orchestrate.return_value = mock_layer
            await chat(prompt="hello", session="cli-reset-sess", model="grok-composer-2.5-fast")

        row = await store.get_session("cli-reset-sess")
        # The placeholder (session name) is never sent upstream; a real
        # response id would have been kept only for server-state API turns.
        assert row["api_thread_id"] == "cli-reset-sess"
    finally:
        await store.delete_session("cli-reset-sess")


@pytest.mark.asyncio
async def test_agentic_chat_tool_is_gone():
    """Tombstone: agentic_chat was merged into chat — it must not resurface
    as an import or a registered MCP tool."""
    import src.server as server_module
    from src.server import mcp

    assert not hasattr(server_module, "agentic_chat")
    tools = await mcp.list_tools()
    assert "agentic_chat" not in {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_chat_with_files_validates_file_ids():
    res = await chat_with_files(prompt="summarize this", file_ids=[])
    assert "Input Validation Error" in res.response


@pytest.mark.asyncio
async def test_xai_upload_file_returns_structured_file_id(tmp_path):
    """xai_upload_file (renamed from upload_file) returns a dict so clients
    can read file_id directly instead of scraping it out of markdown."""
    report = tmp_path / "report.pdf"
    report.write_bytes(b"test report")
    fake_file = SimpleNamespace(id="file-123", filename="report.pdf", size=42)
    fake_client = MagicMock()
    fake_client.files.upload.return_value = fake_file

    with patch("src.tools.system._resolve_workspace_file", return_value=report), \
         patch("src.tools.system.get_xai_client", return_value=fake_client):
        res = await xai_upload_file(str(report))

    fake_client.files.upload.assert_called_once_with(str(report))
    assert res["file_id"] == "file-123"
    assert res["filename"] == "report.pdf"
    assert res["size_bytes"] == 42
    # The human-readable text is preserved in the summary field.
    assert "file-123" in res["summary"]
    assert "42 bytes" in res["summary"]


@pytest.mark.asyncio
async def test_xai_list_files_reads_list_response_data():
    fake_client = MagicMock()
    fake_client.files.list.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(id="file-a", filename="a.txt", size=10, public_url=""),
            SimpleNamespace(id="file-b", filename="b.txt", size=20, public_url="https://example.test/b.txt"),
        ],
        pagination_token="",
    )

    with patch("src.tools.system.get_xai_client", return_value=fake_client):
        res = await xai_list_files()

    assert "file-a" in res
    assert "a.txt" in res
    assert "20" in res
    assert "https://example.test/b.txt" in res


@pytest.mark.asyncio
async def test_xai_get_file_uses_xai_file_metadata_shape():
    fake_client = MagicMock()
    fake_client.files.get.return_value = SimpleNamespace(
        id="file-meta",
        filename="meta.txt",
        size=99,
        public_url="",
        expires_at=None,
    )

    with patch("src.tools.system.get_xai_client", return_value=fake_client):
        res = await xai_get_file("file-meta")

    fake_client.files.get.assert_called_once_with("file-meta")
    assert "meta.txt" in res
    assert "99 bytes" in res
    assert "Public URL" in res


@pytest.mark.asyncio
async def test_mcp_tool_surface_includes_chat_with_files():
    from src.server import mcp

    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}
    assert "chat_with_files" in tool_names
    assert "grok_reflect" in tool_names


@pytest.mark.asyncio
async def test_grok_reflect_uses_structured_parse(monkeypatch):
    parsed = GrokReflectionResult(
        verdict="needs_changes",
        summary="Good direction but verification is incomplete.",
        strengths=["small scope"],
        issues=["missing test"],
        recommendations=["add a focused regression test"],
        next_action="Add the test.",
        confidence=0.82,
    )
    mock_parse = AsyncMock(return_value=(parsed, 17, 0.004))
    monkeypatch.setattr("src.tools.chats._parse_structured", mock_parse)

    res = await grok_reflect(
        subject="Plan: add a tool",
        criteria="Review correctness only.",
        context="Existing tests use monkeypatch.",
        model="grok-4.3",
    )

    assert res.ok is True
    assert res.critique["verdict"] == "needs_changes"
    assert res.tokens == 17
    assert res.cost_usd == pytest.approx(0.004)
    assert res.finish_reason == "final_answer"
    shape, system_prompt, user_prompt, model = mock_parse.await_args.args[:4]
    assert shape is GrokReflectionResult
    assert "Reflection Oracle" in system_prompt
    assert "Plan: add a tool" in user_prompt
    assert "Review correctness only." in user_prompt
    assert model == "grok-4.3"


@pytest.mark.asyncio
async def test_grok_reflect_degrades_when_parse_unavailable(monkeypatch):
    monkeypatch.setattr("src.tools.chats._parse_structured", AsyncMock(return_value=(None, 0, 0.0)))

    res = await grok_reflect(subject="Review this")

    assert res.ok is False
    assert res.critique == {}
    assert res.response == "structured_reflection_unavailable"
    assert res.model == "grok-4.5"
    assert res.tokens == 0
    assert res.cost_usd == 0.0
    assert res.finish_reason == "error"


@pytest.mark.asyncio
async def test_mcp_server_declares_instructions():
    """Next#10: the stdio server must ship instructions= so clients know the
    agent tool is the headline entry point."""
    from src.server import mcp

    instructions = mcp._mcp_server.instructions
    assert instructions
    assert "agent" in instructions
    assert "UniGrok" in instructions


@pytest.mark.asyncio
async def test_discover_self_tool():
    from src.server import grok_mcp_discover_self
    res = await grok_mcp_discover_self()
    assert res.finish_reason == "final_answer"
    assert res.route == "discovery"
    assert res.data["okf_version"] == "0.1"
    assert res.data["schema_version"] == 2
    assert res.data["name"] == "uni-grok-mcp"
    assert "/docs/okf/index.md" in res.data["files"]
    assert "/docs/okf/api-reference.md" in res.data["files"]
    assert "UniGrok MCP Discovery" in res.response
    assert "Bootstrap (first connect)" in res.response
    assert "credential_planes" in res.data
    assert res.data["credential_planes"]["preferred_plane"] in {"CLI", "API"}
    assert "local_usage" in res.data["credential_planes"]
    assert "request_context" in res.data
    assert "bootstrap" in res.data
    bootstrap = res.data["bootstrap"]
    assert bootstrap["schema_version"] == 1
    assert bootstrap["status"] in {"OK", "WARN", "ERR"}
    assert bootstrap["caller_config_audit"] == "not_available_on_service"
    assert isinstance(bootstrap["first_connect_checklist"], list)
    assert bootstrap["first_connect_checklist"]
    assert res.data["request_context"]["surface"] in {
        "stable_core",
        "contributor_forge",
        "mode_dial",
    }
    assert "can_chat" in bootstrap
    assert "can_mutate_workspace" in bootstrap


@pytest.mark.asyncio
async def test_discover_self_model_catalog_is_opt_in_and_plane_specific(monkeypatch):
    monkeypatch.setattr(
        "src.tools.system.grok_cli_plane_status",
        lambda timeout_sec=5.0: {
            "state": "ready",
            "ready": True,
            "binary": True,
            "auth": "oauth",
            "setup_command": "unused",
        },
    )
    catalog = AsyncMock(return_value={
        "xai_api": [
            {"id": "grok-4.5", "context_window": 131072},
            {"id": "grok-api-only"},
        ],
        "grok_cli": [
            {"id": "grok-4.5", "default": True},
            {"id": "grok-composer-2.5-fast", "default": False},
        ],
        "local_profiles": [],
        "default_cli_model": "grok-4.5",
        "warnings": [],
        "sources": {"xai_api": "xai_api", "grok_cli": "grok_cli", "local_profiles": ".grok/hyperparams"},
        "availability": {"xai_api": True, "grok_cli": True},
    })
    monkeypatch.setattr("src.tools.system.build_model_catalog", catalog)

    from src.server import grok_mcp_discover_self

    compact = await grok_mcp_discover_self()
    assert "model_catalog" not in compact.data
    catalog.assert_not_awaited()

    detailed = await grok_mcp_discover_self(include_models=True)
    model_catalog = detailed.data["model_catalog"]
    assert set(model_catalog["planes"]) == {"CLI", "API"}
    assert model_catalog["planes"]["CLI"]["default_model"] == "grok-4.5"
    assert model_catalog["planes"]["CLI"]["credential_available"] is True
    assert model_catalog["planes"]["CLI"]["catalog_available"] is True
    assert model_catalog["planes"]["CLI"]["economics"].startswith("Subscription-backed")
    assert model_catalog["planes"]["API"]["economics"].startswith("Metered developer API")
    assert model_catalog["shared_model_ids"] == ["grok-4.5"]
    assert model_catalog["routing"]["preferred_plane"] in {"CLI", "API"}
    catalog.assert_awaited_once_with(include_cli=True)


@pytest.mark.asyncio
async def test_mcp_tool_surface_consolidated():
    """Consolidation pins: agent is registered; grok_imagine and agentic_chat
    are gone; code_executor and the xAI file tools carry their new names."""
    from src.server import mcp

    tool_names = {tool.name for tool in await mcp.list_tools()}
    assert "agent" in tool_names
    assert "remote_code_execution" in tool_names
    assert "grok_mcp_discover_self" in tool_names
    assert {"xai_upload_file", "xai_list_files", "xai_get_file",
            "xai_get_file_content", "xai_delete_file"} <= tool_names
    # Local workspace tools keep their unprefixed names.
    assert {"read_local_file", "list_project_files"} <= tool_names
    # Deleted / renamed-away tools must not resurface.
    for gone in ("grok_imagine", "agentic_chat", "code_executor",
                 "upload_file", "list_files", "get_file", "get_file_content",
                 "delete_file"):
        assert gone not in tool_names, f"'{gone}' should no longer be registered"


@pytest.mark.asyncio
async def test_mcp_tools_carry_spec_annotations():
    """Quick win 9: read-only tools advertise readOnlyHint and destructive
    tools advertise destructiveHint via real spec ToolAnnotations."""
    from src.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    readonly = [
        "grok_mcp_status", "grok_mcp_discover_self", "list_chat_sessions", "get_chat_history",
        "list_models", "xai_list_files", "xai_get_file", "xai_get_file_content",
        "read_local_file", "list_project_files", "web_search", "x_search",
        "git_status", "git_diff", "git_log", "git_show", "git_current_branch",
        "retrieve_stateful_response", "grok_reflect",
    ]
    for name in readonly:
        assert tools[name].annotations is not None, f"{name} missing annotations"
        assert tools[name].annotations.readOnlyHint is True, f"{name} not readOnlyHint"

    destructive = [
        "xai_delete_file", "clear_chat_history", "db_vacuum",
        "git_commit", "git_apply_patch", "delete_stateful_response",
    ]
    for name in destructive:
        assert tools[name].annotations is not None, f"{name} missing annotations"
        assert tools[name].annotations.destructiveHint is True, f"{name} not destructiveHint"

    # The dead non-spec READONLY constant must not survive.
    import src.tools.system as system_module
    assert not hasattr(system_module, "READONLY")


@pytest.mark.asyncio
async def test_agent_tool_returns_structured_metadata(monkeypatch):
    """The unified agent tool returns a structured dict with the response and
    route/plane/finish_reason/cost execution metadata."""
    mock_run = AsyncMock(return_value=MetaLayer(
        generation="agent answer",
        route="agentic",
        plane="API",
        model="grok-build-0.1",
        routing_why="cost",
        degraded=False,
        profile="grok-build-0.1",
        finish_reason="final_answer",
        tokens=42,
        cost_usd=0.012,
        latency=1.5,
        routing_receipt={"v": 1, "resolved_model": "grok-build-0.1", "why_detail": "coding_signal"},
    ))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

    res = await agent(task="summarize src/utils.py", session="s-agent")

    assert res.response == "agent answer"
    assert res.route == "agentic"
    assert res.plane == "API"
    assert res.model == "grok-build-0.1"
    assert res.why == "cost"
    assert res.degraded is False
    assert res.profile == "grok-build-0.1"
    assert res.finish_reason == "final_answer"
    assert res.tokens == 42
    assert res.cost_usd == 0.012
    assert res.latency_sec == 1.5
    assert res.routing["why_detail"] == "coding_signal"
    _, kwargs = mock_run.call_args
    assert kwargs["prompt"] == "summarize src/utils.py"
    assert kwargs["session"] == "s-agent"
    assert kwargs["mode"] == "auto"
    assert kwargs["thinking_mode"] is False
    assert kwargs["enable_agentic"] is True
    assert kwargs["model"] is None
    assert kwargs["plane"] == "auto"
    assert kwargs["fallback_policy"] == "cross_plane"


@pytest.mark.asyncio
async def test_agent_tool_forwards_strict_subscription_contract(monkeypatch):
    mock_run = AsyncMock(return_value=MetaLayer(
        generation="subscription answer",
        plane="CLI",
        model="grok-4.5",
        routing_receipt={
            "requested_plane": "CLI",
            "resolved_plane": "CLI",
            "fallback_policy": "same_plane",
            "billing_class": "subscription",
        },
    ))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

    result = await agent(
        task="use subscription",
        plane="cli",
        fallback_policy="same_plane",
        model="grok-4.5",
    )

    assert mock_run.call_args.kwargs["plane"] == "cli"
    assert mock_run.call_args.kwargs["fallback_policy"] == "same_plane"
    assert result.requested_plane == "cli"
    assert result.resolved_plane == "CLI"
    assert result.billing_class == "subscription"


@pytest.mark.asyncio
async def test_agent_tool_reports_failover_metadata(monkeypatch):
    """Routing metadata stays explicit when the agent degraded to fallback."""
    mock_run = AsyncMock(return_value=MetaLayer(
        generation="fallback answer",
        route="cli-fallback",
        plane="CLI-Fallback",
        model="grok-composer-2.5-fast",
        routing_why="failover",
        degraded=True,
        finish_reason="fallback",
    ))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

    res = await agent(task="recover")

    assert res.plane == "CLI-Fallback"
    assert res.model == "grok-composer-2.5-fast"
    assert res.why == "failover"
    assert res.degraded is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("auto", {"mode": "auto", "thinking_mode": False, "enable_agentic": True}),
        ("fast", {"mode": "auto", "thinking_mode": False, "enable_agentic": False}),
        ("reasoning", {"mode": "reasoning", "thinking_mode": False, "enable_agentic": True}),
        ("thinking", {"mode": "auto", "thinking_mode": True, "enable_agentic": True}),
    ],
)
async def test_agent_tool_mode_mapping(monkeypatch, mode, expected):
    """agent's mode enum maps onto run_agent_turn's mode/thinking_mode/
    enable_agentic parameters (fast = toolless path, thinking = agent loop +
    schema-enforced reflection)."""
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

    await agent(task="do it", mode=mode, model="grok-4.3")

    _, kwargs = mock_run.call_args
    for key, value in expected.items():
        assert kwargs[key] == value, f"mode={mode}: {key} should be {value}"
    assert kwargs["model"] == "grok-4.3"


@pytest.mark.asyncio
async def test_agent_tool_thinking_mode_end_to_end(monkeypatch):
    """mode='thinking' flows through run_agent_turn → orchestrate →
    run_thinking_loop: AgentLoop produces the answer, the schema-enforced
    reviewer passes it, and the structured result reports route='thinking'."""
    from src.utils import MetaLayer as ML, ReflectionVerdict

    monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)

    async def fake_agentloop_run(self, prompt, session=None, history=None, input_messages=None):
        layer = ML(generation="deep reviewed answer", finish_reason="final_answer")
        layer.cost_usd = 0.01
        layer.tokens = 20
        layer.reasoning = "opening plan"
        return layer

    mock_reflect = AsyncMock(return_value=(ReflectionVerdict(status="pass"), 5, 0.001))
    with patch("src.utils.AgentLoop.run", new=fake_agentloop_run), \
         patch("src.utils._reflect_on_answer", new=mock_reflect), \
         patch("src.utils._call_plane", new_callable=AsyncMock) as mock_call:
        res = await agent(task="solve the hardest problem", mode="thinking")

    mock_call.assert_not_awaited()
    mock_reflect.assert_awaited_once()
    assert res.response == "deep reviewed answer"
    assert res.route == "thinking"
    assert res.plane == "API"
    assert res.finish_reason == "final_answer"


@pytest.mark.asyncio
async def test_pydantic_validation_grok_agent_input():
    from src.server import grok_agent

    res = await grok_agent(prompt="hello", max_iterations=11, cost_limit=0.50)
    assert "Input Validation Error" in res.response

    res2 = await grok_agent(prompt="hello", max_iterations=5, cost_limit=-0.1)
    assert "Input Validation Error" in res2.response


@pytest.mark.asyncio
async def test_grok_agent_forwards_caps_to_thinking_loop(monkeypatch):
    """grok_agent maps max_iterations/cost_limit onto run_thinking_loop's
    max_reflections/global_budget_usd caps."""
    from src.server import grok_agent
    from src.utils import MetaLayer as ML

    mock_loop = AsyncMock(return_value=ML(generation="capped answer", finish_reason="final_answer"))
    monkeypatch.setattr("src.utils.run_thinking_loop", mock_loop)

    res = await grok_agent(prompt="hello", max_iterations=3, cost_limit=0.25)

    _, kwargs = mock_loop.call_args
    assert kwargs["max_reflections"] == 3
    assert kwargs["global_budget_usd"] == 0.25
    assert kwargs["model"] == "grok-4.5"
    assert "capped answer" in res.response


@pytest.mark.asyncio
async def test_stateful_chat_returns_response_id_field(monkeypatch):
    """Next#11: stateful_chat returns response_id as a structured field, not
    just embedded in prose."""
    fake_response = SimpleNamespace(content="stored reply", id="resp-789", usage=None)
    fake_chat = MagicMock()
    fake_chat.sample.return_value = fake_response
    fake_client = MagicMock()
    fake_client.chat.create.return_value = fake_chat

    monkeypatch.setattr("src.tools.chats.get_xai_client", lambda: fake_client)

    from src.server import stateful_chat
    res = await stateful_chat(prompt="continue", model="grok-4.3")

    assert res.response_id == "resp-789"
    assert res.model == "grok-4.3"
    assert "stored reply" in res.text


@pytest.mark.asyncio
async def test_generate_image_returns_structured_urls(monkeypatch):
    """Next#11: generate_image returns the image URLs as a structured list
    while keeping the human-readable summary."""
    fake_images = [
        SimpleNamespace(url="https://img.test/1.png", prompt="a cat, refined", usage=None),
        SimpleNamespace(url="https://img.test/2.png", prompt="a cat", usage=None),
    ]
    fake_client = MagicMock()
    fake_client.image.sample_batch.return_value = fake_images
    monkeypatch.setattr("src.tools.media.get_xai_client", lambda: fake_client)

    from src.server import generate_image
    res = await generate_image(prompt="a cat", n=2)

    assert res.images == ["https://img.test/1.png", "https://img.test/2.png"]
    assert res.model == "grok-imagine-image"
    assert "https://img.test/1.png" in res.summary
    assert "a cat, refined" in res.summary


@pytest.mark.asyncio
async def test_web_search_forwards_domain_filters(monkeypatch):
    """Now#6: web_search passes allowed/excluded domains through to the xAI
    server-side search tool helper."""
    fake_response = SimpleNamespace(content="web result", citations=[], usage=None)
    fake_chat = MagicMock()
    fake_chat.sample.return_value = fake_response
    fake_client = MagicMock()
    fake_client.chat.create.return_value = fake_chat
    monkeypatch.setattr("src.tools.system.get_xai_client", lambda: fake_client)

    mock_tool = MagicMock(return_value={"type": "web_search"})
    monkeypatch.setattr("src.tools.system.xai_web_search", mock_tool)

    from src.server import web_search
    res = await web_search(
        prompt="latest research",
        allowed_domains=["arxiv.org"],
        excluded_domains=["example.com"],
    )

    mock_tool.assert_called_once_with(
        allowed_domains=["arxiv.org"], excluded_domains=["example.com"]
    )
    assert "web result" in res.response


@pytest.mark.asyncio
async def test_x_search_forwards_handles_and_dates(monkeypatch):
    """Now#6: x_search passes handle and ISO date filters through to the xAI
    x_search helper as datetimes."""
    from datetime import datetime

    fake_response = SimpleNamespace(content="x result", citations=[], usage=None)
    fake_chat = MagicMock()
    fake_chat.sample.return_value = fake_response
    fake_client = MagicMock()
    fake_client.chat.create.return_value = fake_chat
    monkeypatch.setattr("src.tools.system.get_xai_client", lambda: fake_client)

    mock_tool = MagicMock(return_value={"type": "x_search"})
    monkeypatch.setattr("src.tools.system.xai_x_search", mock_tool)

    from src.server import x_search
    res = await x_search(
        prompt="what did they post",
        allowed_x_handles=["xai"],
        from_date="2026-06-01",
        to_date="2026-07-01",
    )

    mock_tool.assert_called_once_with(
        from_date=datetime(2026, 6, 1),
        to_date=datetime(2026, 7, 1),
        allowed_x_handles=["xai"],
    )
    assert "x result" in res.response


@pytest.mark.asyncio
async def test_x_search_rejects_bad_dates():
    from src.server import x_search

    res = await x_search(prompt="anything", from_date="not-a-date")
    assert "Input Validation Error" in res.response


@pytest.mark.asyncio
async def test_get_chat_history_limits_to_recent_messages(monkeypatch):
    """Quick win 11: get_chat_history returns only the most recent `limit`
    messages with a notice instead of the unbounded transcript."""
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
        for i in range(30)
    ]
    monkeypatch.setattr("src.utils.load_history", AsyncMock(return_value=history))

    from src.server import get_chat_history
    res = await get_chat_history(session="default")

    assert "msg-29" in res
    assert "msg-10" in res
    assert "msg-9" not in res
    assert "20 most recent of 30" in res

    res_all = await get_chat_history(session="default", limit=30)
    assert "msg-0" in res_all
    assert "most recent of" not in res_all


@pytest.mark.asyncio
async def test_list_project_files_truncates_at_max_results():
    """Quick win 11: list_project_files caps output at max_results and says
    how many files were omitted."""
    from src.server import list_project_files

    res = await list_project_files(max_results=3)
    listed = [line for line in res.splitlines() if line.startswith("- `")]
    assert len(listed) == 3
    assert "Truncated: showing 3 of" in res

    # Prove the non-truncation path with a high enough ceiling; the live
    # checkout now has more than 200 ``.py`` files, so the default cap truncates.
    res_full = await list_project_files(extensions="py", max_results=10_000)
    assert "Truncated" not in res_full


@pytest.mark.asyncio
async def test_chat_with_vision_path_validation():
    from src.server import chat_with_vision
    # Using an absolute path outside project root should raise PermissionError
    with pytest.raises(PermissionError, match="Access denied"):
        await chat_with_vision(prompt="analyze", image_paths=["/etc/passwd"])


@pytest.mark.asyncio
async def test_generate_image_path_validation():
    from src.server import generate_image
    with pytest.raises(PermissionError, match="Access denied"):
        await generate_image(prompt="generate", image_paths=["/etc/passwd"])


@pytest.mark.asyncio
async def test_generate_video_path_validation():
    from src.server import generate_video
    with pytest.raises(PermissionError, match="Access denied"):
        await generate_video(prompt="generate", image_path="/etc/passwd")
        
    with pytest.raises(PermissionError, match="Access denied"):
        await generate_video(prompt="generate", video_path="/etc/passwd")

    with pytest.raises(PermissionError, match="Access denied"):
        await generate_video(prompt="generate", reference_image_paths=["/etc/passwd"])


@pytest.mark.asyncio
async def test_local_file_reader_rejects_oversize_input(tmp_path, monkeypatch):
    from src.server import read_local_file

    large_file = tmp_path / "large.txt"
    large_file.write_text("x" * 128)
    monkeypatch.setenv("UNIGROK_MAX_LOCAL_FILE_CHARS", "1024")
    with patch("src.tools.system.PathResolver.validate_path", return_value=large_file), \
         patch("src.tools.system.is_path_ignored", return_value=False):
        with pytest.raises(ValueError, match="limit"):
            await read_local_file(str(large_file), max_chars=8)


@pytest.mark.asyncio
async def test_upload_rejects_oversize_input(tmp_path, monkeypatch):
    from src.server import xai_upload_file

    large_file = tmp_path / "large.bin"
    large_file.write_bytes(b"x" * 2048)
    monkeypatch.setenv("UNIGROK_MAX_UPLOAD_BYTES", "1024")
    with patch("src.tools.system._resolve_workspace_file", return_value=large_file):
        with pytest.raises(ValueError, match="limit"):
            await xai_upload_file(str(large_file))


@pytest.mark.asyncio
async def test_upload_blocks_ignored_private_file(tmp_path, monkeypatch):
    from src.server import xai_upload_file

    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("SECRET=leak", encoding="utf-8")
    monkeypatch.setattr("src.tools.system.PathResolver.get_workspace_root", lambda: tmp_path)

    with patch("src.tools.system.PathResolver.validate_path", return_value=secret):
        with pytest.raises(PermissionError, match="ignored or private"):
            await xai_upload_file(str(secret))


@pytest.mark.asyncio
async def test_read_local_file_reports_unavailable_without_workspace(monkeypatch):
    from src.server import read_local_file

    monkeypatch.setattr("src.tools.system.PathResolver.get_workspace_root", lambda: None)

    res = await read_local_file("notes.txt")

    assert "[UNAVAILABLE] No workspace is attached" in res


@pytest.mark.asyncio
async def test_media_input_bounds_are_checked(tmp_path, monkeypatch):
    from src.server import generate_image, generate_video

    bad_image = tmp_path / "source.gif"
    bad_image.write_bytes(b"image")
    monkeypatch.setenv("UNIGROK_MAX_MEDIA_INPUT_BYTES", "1024")
    with patch("src.tools.media.PathResolver.validate_path", return_value=bad_image):
        with pytest.raises(ValueError, match="Unsupported image type"):
            await generate_image(prompt="edit", image_paths=[str(bad_image)])

    oversized_video = tmp_path / "source.mp4"
    oversized_video.write_bytes(b"x" * 2048)
    monkeypatch.setenv("UNIGROK_MAX_MEDIA_INPUT_BYTES", "1024")
    with patch("src.tools.media.PathResolver.validate_path", return_value=oversized_video):
        with pytest.raises(ValueError, match="limit"):
            await generate_video(prompt="edit", video_path=str(oversized_video))

    with pytest.raises(ValueError, match="duration"):
        await generate_video(prompt="generate", duration=16)


@pytest.mark.asyncio
async def test_generate_image_rejects_invalid_count():
    from src.server import generate_image

    with pytest.raises(ValueError, match="between 1 and 10"):
        await generate_image(prompt="generate", n=11)


@pytest.mark.asyncio
async def test_clear_chat_history_sanitization():
    # Valid session name should work (or mock work)
    with patch("src.tools.system.store.delete_session", new_callable=AsyncMock) as mock_delete:
        res = await clear_chat_history("valid-session-123_abc")
        assert "Cleared history for session" in res

    # Namespaced session name containing colon should work
    with patch("src.tools.system.store.delete_session", new_callable=AsyncMock) as mock_delete:
        res_ns = await clear_chat_history("vscode:my-session-name")
        assert "Cleared history for session" in res_ns

    # Invalid session name with relative traversal should return error output
    res2 = await clear_chat_history("../traversal")
    assert "Error: Invalid session name" in res2

    # Traversal attempts should be blocked
    res3 = await clear_chat_history("vscode:../main")
    assert "Error: Invalid session name" in res3


@pytest.mark.asyncio
async def test_agent_tool_reports_progress_via_ctx(monkeypatch):
    """With an injected FastMCP Context, the agent tool adapts progress events
    onto ctx.report_progress: depth events carry n-of-max progress, tool
    events reuse the last depth with a descriptive message, and content
    deltas are skipped (no per-token notification flood)."""

    async def fake_run_agent_turn(**kwargs):
        on_event = kwargs["on_event"]
        await on_event({"type": "depth", "depth": 2, "max_depth": 8, "cost_usd": 0.01})
        await on_event({"type": "tool_end", "tool": "web_search", "success": True, "elapsed": 1.2, "cost_usd": 0.02})
        await on_event({"type": "content_delta", "text": "skipped"})
        return MetaLayer(generation="ok", finish_reason="final_answer")

    monkeypatch.setattr("src.tools.chats.run_agent_turn", fake_run_agent_turn)
    mock_ctx = MagicMock()
    mock_ctx.report_progress = AsyncMock()

    res = await agent(task="do it", ctx=mock_ctx)

    assert res.response == "ok"
    calls = mock_ctx.report_progress.await_args_list
    assert len(calls) == 2  # content_delta must not notify
    assert calls[0].args[0] == 2.0
    assert calls[0].args[1] == 8.0
    assert "depth 2/8" in calls[0].args[2]
    assert "web_search" in calls[1].args[2]


@pytest.mark.asyncio
async def test_agent_tool_without_ctx_passes_no_event_callback(monkeypatch):
    """Direct calls (no FastMCP context) keep on_event=None."""
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)

    await agent(task="do it")

    assert mock_run.call_args.kwargs["on_event"] is None


@pytest.mark.asyncio
async def test_agent_tool_ctx_hidden_from_schema():
    """FastMCP injects ctx via Context-annotation detection; the parameter
    must not leak into the tool's public input schema."""
    from mcp.server.fastmcp import FastMCP

    probe = FastMCP("schema-probe")
    probe.add_tool(agent)
    tools = {tool.name: tool for tool in await probe.list_tools()}

    assert "agent" in tools
    assert "ctx" not in tools["agent"].inputSchema.get("properties", {})
    assert "task" in tools["agent"].inputSchema.get("properties", {})


@pytest.mark.asyncio
async def test_bound_principal_cannot_read_shared_provider_file_content():
    from src.identity import reset_active_principal, set_active_principal
    from src.tools.system import xai_get_file_content

    principal_token = set_active_principal("oauth:service:tenant-a")
    try:
        with patch("src.tools.system.get_xai_client") as get_client:
            with pytest.raises(PermissionError, match="bound HTTP/MCP principals"):
                await xai_get_file_content("file-owned-by-tenant-b")
    finally:
        reset_active_principal(principal_token)

    get_client.assert_not_called()


@pytest.mark.asyncio
async def test_grok_mcp_restart_container_gating(monkeypatch, tmp_path):
    from src.tools.system import grok_mcp_restart_container
    from src.identity import reset_active_principal, set_active_principal

    # 1. Test disabled behavior
    monkeypatch.setenv("UNIGROK_ENABLE_CONTAINER_RESTART", "0")
    res = await grok_mcp_restart_container()
    assert res.data["status"] == "disabled"
    assert "disabled" in res.response

    monkeypatch.setenv("UNIGROK_ENABLE_CONTAINER_RESTART", "1")
    principal_token = set_active_principal("http:key-1")
    try:
        res_http = await grok_mcp_restart_container()
    finally:
        reset_active_principal(principal_token)
    assert res_http.data["status"] == "http_forbidden"
    assert "unavailable over HTTP/MCP" in res_http.response

    # 2. Test enabled behavior without a Compose project.
    invalid_root = tmp_path / "outside"
    invalid_root.mkdir()
    with patch("src.tools.system.PathResolver.get_service_root", return_value=invalid_root):
        res_scope = await grok_mcp_restart_container()
        assert res_scope.data["status"] == "unauthorized_scope"
        assert "execution blocked" in res_scope.response

    # 3. Test enabled behavior with a portable valid root and mocked success.
    valid_root = tmp_path / "project"
    valid_root.mkdir()
    (valid_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    with patch("src.tools.system.PathResolver.get_service_root", return_value=valid_root):
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        async def fake_subprocess(*args, **kwargs):
            return mock_proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)

        async def fake_communicate(proc, timeout):
            return b"restarted container ok", b""

        monkeypatch.setattr("src.tools.system.communicate_with_timeout", fake_communicate)

        res_success = await grok_mcp_restart_container()
        assert res_success.data["returncode"] == 0
        assert "triggered successfully" in res_success.response

    # 4. An explicit allow-root must match exactly.
    monkeypatch.setenv("UNIGROK_CONTAINER_RESTART_ROOT", str(tmp_path / "different"))
    with patch("src.tools.system.PathResolver.get_service_root", return_value=valid_root):
        res_shadow = await grok_mcp_restart_container()
        assert res_shadow.data["status"] == "unauthorized_scope"
        assert "execution blocked" in res_shadow.response
