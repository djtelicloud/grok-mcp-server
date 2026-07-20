import asyncio
import base64
import json
from pathlib import Path

import pytest

from unigrok_public import __version__, grok_build, server, xai_api


def _catalogs(
    *,
    cli_ready: bool = True,
    api_ready: bool = True,
    cli_models: list[str] | None = None,
    api_models: list[str] | None = None,
) -> dict:
    cli_ids = cli_models if cli_models is not None else ["grok-cli-live"]
    api_ids = api_models if api_models is not None else ["grok-api-live"]
    return {
        "cli": {
            "ready": cli_ready,
            "binary": True,
            "authenticated": cli_ready,
            "models": cli_ids,
            "default_model": cli_ids[0] if cli_ids else None,
        },
        "api": {
            "ready": api_ready,
            "configured": api_ready,
            "authenticated": api_ready,
            "models": [{"id": model} for model in api_ids],
            "image_models": [{"id": "grok-image-live"}],
            "default_model": api_ids[0] if api_ids else None,
        },
    }


def _build_worker(tmp_path: Path, *, agentic: bool, allow_web: bool) -> grok_build.GrokBuildWorker:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    return grok_build.GrokBuildWorker(
        binary="/usr/local/bin/grok",
        auth_path=auth,
        model="grok-live" if agentic else None,
        effort="high" if agentic else None,
        max_turns=4,
        allow_web=allow_web,
        agentic=agentic,
        system_prompt="system",
        timeout_seconds=120,
    )


def test_build_acp_agent_removes_local_tools_without_disabling_web(tmp_path: Path) -> None:
    args = _build_worker(tmp_path, agentic=True, allow_web=True)._command()
    assert args[args.index("--disallowed-tools") + 1] == grok_build.LOCAL_AUTHORITY_TOOLS
    assert "--deny" not in args
    assert "--disable-web-search" not in args
    assert args[args.index("-m") + 1] == "grok-live"
    assert args[args.index("--max-turns") + 1] == "4"
    assert args[-2:] == ["agent", "stdio"]
    assert "--no-auto-update" in args


def test_build_acp_chat_hard_disables_tools_and_web(tmp_path: Path) -> None:
    args = _build_worker(tmp_path, agentic=False, allow_web=False)._command()
    assert "--disable-web-search" in args
    assert args[args.index("--tools") + 1] == ""
    assert "--verbatim" in args


def test_isolated_cli_environment_excludes_all_provider_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "AUTH_PATH", auth)
    for name in (
        "XAI_API_KEY",
        "XAI_MANAGEMENT_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setenv(name, "must-not-leak")
    with server._isolated_cli_runtime() as (work, env):
        assert work.is_dir()
        assert env["GROK_AUTH_PATH"] == str(auth)
        assert not any(name.endswith("API_KEY") for name in env)
        assert Path(env["HOME"]).is_dir()


def test_build_acp_turn_state_returns_only_last_message_run() -> None:
    state = grok_build._TurnState()
    state.update({"sessionUpdate": "agent_message_chunk", "content": {"text": "I'll look."}})
    state.update({"sessionUpdate": "tool_call"})
    state.update({"sessionUpdate": "agent_thought_chunk", "content": {"text": "private"}})
    state.update({"sessionUpdate": "agent_message_chunk", "content": {"text": "PUBLIC "}})
    state.update({"sessionUpdate": "agent_message_chunk", "content": {"text": "OK"}})
    assert state.final_text() == "PUBLIC OK"


def test_cli_model_parser_discovers_all_reported_models() -> None:
    models, default, authenticated = server._parse_models(
        "You are logged in with grok.com.\n"
        "Default model: grok-new-default\n"
        "  * grok-new-default (default)\n"
        "  * grok-another-model\n"
    )
    assert models == ["grok-new-default", "grok-another-model"]
    assert default == "grok-new-default"
    assert authenticated is True


def test_public_mcp_tool_contract_is_exact_and_self_checked() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    names = [tool.name for tool in tools]
    assert names == list(server.PUBLIC_TOOL_NAMES)
    assert len(names) == 29
    assert server.mcp._mcp_server.version == __version__ == "1.1.0"
    assert server.mcp._mcp_server.instructions == server.INSTRUCTIONS
    assert "makes web research, X search, and code execution available" in server.INSTRUCTIONS
    assert "Inform the user" in server.INSTRUCTIONS
    assert ".agents/skills/<skill-name>/SKILL.md" in server.INSTRUCTIONS
    agent_tool = next(tool for tool in tools if tool.name == "agent")
    assert "disable_tools" in agent_tool.inputSchema["properties"]
    caller_evidence = agent_tool.inputSchema["properties"]["caller_evidence"]
    evidence_schema = agent_tool.inputSchema["$defs"]["CallerEvidenceInput"]
    assert caller_evidence["anyOf"][0]["items"]["$ref"].endswith(
        "/CallerEvidenceInput"
    )
    assert set(evidence_schema["required"]) == {"reference", "observation"}
    assert "class" not in evidence_schema["properties"]
    for hidden_control in ("model", "reasoning_effort", "mode", "plane", "fallback_policy"):
        assert hidden_control not in agent_tool.inputSchema["properties"]
    assert "prompt" in agent_tool.inputSchema["properties"]
    onboarding_tool = next(tool for tool in tools if tool.name == "grok_mcp_onboard_client")
    assert "ctx" not in onboarding_tool.inputSchema["properties"]


def test_heuristic_route_skips_metered_router_for_direct_tasks() -> None:
    assert server._heuristic_route("Reply with exactly: ping") == "direct"
    assert server._heuristic_route("What is 2+2?") == "direct"
    assert server._heuristic_route("Build a parser") is None
    assert server._heuristic_route("Generate an image of a red fox") is None


@pytest.mark.asyncio
async def test_lead_router_is_schema_bounded_and_build_specialist_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogs = _catalogs(
        cli_models=["grok-4.5"],
        api_models=["grok-4.20", "grok-4.5", "grok-build-next"],
    )
    captured: dict = {}

    async def fake_api(prompt: str, **kwargs: object) -> dict:
        if kwargs.get("response_format") == "json_object":
            captured.update(prompt=prompt, router_kwargs=kwargs)
            return {
                "text": '{"route":"code","specialist_prompt":"Implement a typed parser."}',
                "model": kwargs["model"],
                "cost_usd": 0.001,
            }
        captured.update(specialist_prompt=prompt, specialist_kwargs=kwargs)
        return {"text": "CODE_OK", "model": kwargs["model"], "cost_usd": 0.01}

    monkeypatch.setattr(server.xai_api, "chat", fake_api)
    routed = await server._route_task("Build a parser", catalogs)
    assert routed["route"] == "code"
    assert captured["router_kwargs"]["model"] == "grok-4.5"
    assert captured["router_kwargs"]["max_tokens"] == 256
    assert captured["router_kwargs"]["response_format"] == "json_object"
    result = await server._run_specialist("code", routed["specialist_prompt"], catalogs)
    assert result is not None
    assert result["model"] == "grok-build-next"
    assert result["orchestration"]["lead"] == "grok-4.5"
    assert result["orchestration"]["brief_authored_by_lead"] is True


@pytest.mark.asyncio
async def test_auto_routing_uses_actual_catalog_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs()

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    plane, _ = await server._resolve_plane("auto", "grok-api-live", requires_api=False)
    assert plane == "api"
    plane, _ = await server._resolve_plane("auto", "grok-cli-live", requires_api=False)
    assert plane == "cli"
    with pytest.raises(ValueError, match="not present"):
        await server._resolve_plane("auto", "hard-coded-ghost", requires_api=False)


@pytest.mark.asyncio
async def test_auto_uses_owner_enabled_api_when_cli_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs(cli_ready=False, cli_models=[])

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    plane, _ = await server._resolve_plane("auto", None, requires_api=False)
    assert plane == "api"


@pytest.mark.asyncio
async def test_api_only_capability_explicitly_selects_api(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs()

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    plane, _ = await server._resolve_plane("auto", None, requires_api=True)
    assert plane == "api"


@pytest.mark.asyncio
async def test_readyz_rejects_healthy_first_run_without_grok_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs(
            cli_ready=False,
            api_ready=False,
            cli_models=[],
            api_models=[],
        )

    class HealthyState:
        async def health(self) -> bool:
            return True

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "STATE", HealthyState())
    monkeypatch.setattr(server, "is_cloudrun_runtime", lambda: False)

    response = await server.readyz(None)
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["bootstrap"]["status"] == "BLOCKED"
    assert payload["bootstrap"]["can_chat"] is False
    assert payload["state"] == {"ready": True, "backend": "sqlite"}


@pytest.mark.asyncio
async def test_readyz_accepts_healthy_runtime_with_live_cli_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs(api_ready=False, api_models=[])

    class HealthyState:
        async def health(self) -> bool:
            return True

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "STATE", HealthyState())
    monkeypatch.setattr(server, "is_cloudrun_runtime", lambda: False)

    response = await server.readyz(None)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert payload["bootstrap"]["status"] == "OK"
    assert payload["bootstrap"]["can_chat"] is True
    assert payload["state"] == {"ready": True, "backend": "sqlite"}


@pytest.mark.asyncio
async def test_self_description_is_generated_from_live_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs()

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    description = await server.grok_mcp_discover_self()
    assert [tool["name"] for tool in description["tools"]] == list(server.PUBLIC_TOOL_NAMES)
    assert description["version"] == "1.1.0"
    assert description["bootstrap"]["can_chat"] is True
    assert description["bootstrap"]["can_spend_api"] is True
    assert description["bootstrap"]["metered_api_requires_confirmation"] is False
    assert description["credential_planes"]["cli"]["models"] == ["grok-cli-live"]
    assert description["credential_planes"]["api"]["models"] == ["grok-api-live"]
    assert description["credential_planes"]["api"]["image_models"] == ["grok-image-live"]
    assert description["credential_planes"]["api"]["requires_per_request_confirmation"] is False
    assert description["credential_planes"]["notices"][0]["prompt_user"] is False
    assert set(description["routing"]) == {
        "lead",
        "specialists",
        "caller_controls",
        "same_plane",
        "cross_plane",
    }
    assert description["capability_defaults"]["agent"]["allow_web"] is True
    assert description["capability_defaults"]["agent"]["allow_x_search"] is True
    assert description["capability_defaults"]["agent"]["allow_remote_code_execution"] is True
    assert description["capability_defaults"]["agent"]["user_notice_required"] is True
    assert "caller_evidence" in description["capability_defaults"]["agent"][
        "optional_inputs"
    ]
    assert "plane" not in description["capability_defaults"]["agent"]
    assert "prompt" in description["capability_defaults"]["agent"]["input"]
    assert description["capability_defaults"]["chat"]["allow_web"] is False
    client_onboarding = description["client_onboarding"]
    assert client_onboarding["recommended_scope"] == "global"
    assert client_onboarding["automatic_writes"] is False
    assert client_onboarding["project_overrides_global"] is True
    assert client_onboarding["adapters"]["antigravity"]["global_root"] == (
        "~/.gemini/config/plugins/unigrok"
    )
    onboarding = description["project_onboarding"]
    assert onboarding["automatic_workspace_writes"] is False
    assert onboarding["recommended_scope"] == "global_client_namespace"
    assert onboarding["canonical_paths"] == {
        "repository_instructions": "AGENTS.md",
        "antigravity_rules": ".agents/rules/<rule-name>.md",
        "antigravity_workflows": ".agents/workflows/<workflow-name>.md",
        "agent_skills": ".agents/skills/<skill-name>/SKILL.md",
    }
    assert onboarding["legacy_paths_not_to_create"] == [".agent/rules"]
    assert description["team_harness"] == {
        "named_sessions": True,
        "state_backend": "local_sqlite",
        "durable_knowledge": True,
        "state_persistence": True,
        "state_lifetime": "persistent_volume",
        "workspace_context": "explicit_bounded_redacted_courier_only",
        "automatic_workspace_access": False,
        "local_subagents": False,
        "completion_recovery": "one_same_plane_retry_before_bounded_api_fallback",
        "request_limits": {
            "build_concurrency": "provider_managed",
            "build_timeout_seconds": 120,
            "api_timeout_seconds": 120,
            "file_list_timeout_seconds": 120,
            "file_io_timeout_seconds": 60,
            "media_timeout_seconds": 300,
            "agent_sync_window_seconds": 16,
            "agent_result_wait_default_seconds": 16,
            "agent_result_wait_max_seconds": 20,
            "agent_max_turns_cap": 6,
            "mission_lease_ttl_seconds": 180,
            "router_max_output_tokens": 256,
            "vote_max_output_tokens": 128,
            "prompt_chars": 100_000,
            "workspace_context_chars": 100_000,
            "file_content_bytes": 2_000_000,
            "api_max_inflight": 4,
            "api_max_file_inflight": 2,
            "state_terminal_retention_hours": 24,
        },
    }
    assert description["needle"]["active"] is False


def test_client_onboarding_is_namespaced_and_never_writes() -> None:
    plan = server._client_onboarding_plan("antigravity", "global")
    assert plan["writes_performed"] is False
    assert plan["project_root_files_avoided"] is True
    assert plan["precedence"] == "workspace_over_global"
    assert plan["write_policy"]["blind_overwrite"] is False
    paths = [item["path"] for item in plan["files"]]
    assert "~/.gemini/config/plugins/unigrok/plugin.json" in paths
    assert "~/.gemini/config/plugins/unigrok/skills/using-unigrok/SKILL.md" in paths
    assert "~/.gemini/config/global_workflows/ask-grok.md" in paths
    assert not any(path.startswith(".agents/") for path in paths)
    assert all(len(item["sha256"]) == 64 for item in plan["files"])


def test_client_onboarding_detection_and_safe_non_filesystem_fallback() -> None:
    assert server._client_kind("Google Antigravity") == "antigravity"
    assert server._client_kind("codex-desktop") == "codex"
    assert server._client_kind("Claude Code") == "claude_code"
    assert server._client_kind("Cursor") == "cursor"
    assert server._client_kind("Visual Studio Code") == "github_copilot"
    assert server._client_kind("unknown") == "generic"
    cursor = server._client_onboarding_plan("cursor", "global")
    # Cursor now gets the ported client setup: an mcp.json merge entry pointing at the
    # Grok gateway and a routing rule (no longer an empty client_settings-only plan).
    assert cursor["client_settings_instruction"] is None
    entry = cursor["mcp_server"]
    assert entry["target"] == "~/.cursor/mcp.json"
    assert entry["entry"]["mcpServers"]["grok"]["headers"]["X-Client-ID"] == "cursor"
    assert entry["entry"]["mcpServers"]["grok"]["url"].endswith("/mcp")
    rule_paths = [f["path"] for f in cursor["files"]]
    assert ".cursor/rules/using-unigrok.mdc" in rule_paths
    # The emitted MCP config carries NO credential — only the URL and the non-secret
    # X-Client-ID telemetry header (Cursor is a client, never an execution plane).
    grok_server = entry["entry"]["mcpServers"]["grok"]
    assert set(grok_server) == {"url", "headers"}
    assert set(grok_server["headers"]) == {"X-Client-ID"}
    assert "CURSOR_API_KEY" not in json.dumps(cursor)
    # Cursor's "plugin": the beforeMCPExecution hook that auto-approves ONLY the agent
    # tool, plus the hook script shipped as an owned file.
    hooks = cursor["hooks"]
    assert hooks["target"] == "~/.cursor/hooks.json"
    before = hooks["entry"]["hooks"]["beforeMCPExecution"][0]
    assert before["matcher"] == "agent"
    assert "~/.cursor/hooks/before-unigrok-agent.py" in rule_paths
    # The hook auto-allows the agent tool and defers (ask) on anything else.
    assert '"allow"' in server.CURSOR_AGENT_HOOK and '"ask"' in server.CURSOR_AGENT_HOOK


@pytest.mark.asyncio
async def test_agent_accepts_prompt_alias_with_simple_auto_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_turn(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"text": "SEQUENTIAL_TEST_GROK"}

    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs()

    monkeypatch.setattr(server, "_execute_team_turn", fake_turn)
    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    result = await server.agent(prompt="Reply exactly with: SEQUENTIAL_TEST_GROK")
    assert result["text"] == "SEQUENTIAL_TEST_GROK"
    assert captured["prompt"] == "Reply exactly with: SEQUENTIAL_TEST_GROK"
    assert captured["plane"] == "auto"
    assert captured["allow_web"] is True
    assert captured["allow_x_search"] is True
    assert captured["allow_code"] is True
    assert captured["fallback_policy"] == "cross_plane"
    assert result["agent_tools"]["user_notice_required"] is True

    with pytest.raises(ValueError, match="cannot contain different values"):
        await server.agent(task="one", prompt="two")


@pytest.mark.asyncio
async def test_long_agent_turn_returns_resumable_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server._AGENT_JOBS.clear()
    waits = 0

    async def fake_turn(**kwargs: object) -> dict:
        await asyncio.sleep(0)
        return {"text": "LONG_RESULT"}

    async def fake_wait(
        task: asyncio.Task[dict], ctx: object, wait_seconds: int
    ) -> dict | None:
        nonlocal waits
        waits += 1
        if waits == 1:
            return None
        return await task

    monkeypatch.setattr(server, "_execute_team_turn", fake_turn)
    monkeypatch.setattr(server, "_await_job_window", fake_wait)
    pending = await server.agent(task="long task")
    # Autonomy is off by default: unfinished agent jobs stay pending (not continue).
    assert pending["status"] == "pending"
    assert pending["poll"]["tool"] == "agent_result"
    assert "continue_token" not in pending
    complete = await server.agent_result(pending["job_id"])
    assert complete["status"] == "complete"
    assert complete["text"] == "LONG_RESULT"
    assert complete.get("job_id") == pending["job_id"]
    assert not server._AGENT_JOBS


@pytest.mark.asyncio
async def test_all_tools_available_does_not_bypass_authenticated_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_resolve(
        requested: str, model: str | None, *, requires_api: bool
    ) -> tuple[str, dict]:
        captured.update(requested=requested, model=model, requires_api=requires_api)
        return "cli", _catalogs()

    async def fake_system_prompt(kind: str, extra_context: str | None = None) -> str:
        return "safe"

    async def fake_build(prompt: str, **kwargs: object) -> dict:
        captured.update(build_prompt=prompt, build_kwargs=kwargs)
        return {"text": "CLI_FIRST_OK", "plane": "cli", "cost_usd": 0.0}

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system_prompt)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    result = await server._run_unified(
        "Use every compatible tool",
        model=None,
        effort=None,
        plane="auto",
        fallback_policy="cross_plane",
        agentic=True,
        max_turns=6,
        allow_web=True,
        allow_x_search=True,
        allow_code=True,
    )
    assert captured["requires_api"] is False
    assert captured["build_prompt"] == "Use every compatible tool"
    assert captured["build_kwargs"]["allow_web"] is True
    assert result["resolved_plane"] == "cli"
    assert result["fallback_occurred"] is False


@pytest.mark.asyncio
async def test_cli_unavailable_capability_gets_one_bounded_api_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(
        requested: str, model: str | None, *, requires_api: bool
    ) -> tuple[str, dict]:
        assert requested == "auto"
        assert requires_api is False
        return "cli", _catalogs()

    async def fake_system_prompt(kind: str, extra_context: str | None = None) -> str:
        return "safe"

    async def fake_build(prompt: str, **kwargs: object) -> dict:
        return {"text": server.CAPABILITY_UNAVAILABLE_PREFIX + "remote sandbox"}

    async def fake_alternate(current: str, model: str | None, *, requires_api: bool) -> str:
        assert current == "cli"
        assert requires_api is False
        return "api"

    async def fake_api_chat(*args: object, **kwargs: object) -> dict:
        return {"text": "API_RECOVERY_OK", "plane": "api", "cost_usd": 0.01}

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system_prompt)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    monkeypatch.setattr(server, "_alternate_plane", fake_alternate)
    monkeypatch.setattr(server.xai_api, "chat", fake_api_chat)
    result = await server._run_unified(
        "Use a remote sandbox",
        model=None,
        effort=None,
        plane="auto",
        fallback_policy="cross_plane",
        agentic=True,
        max_turns=6,
        allow_web=True,
        allow_x_search=True,
        allow_code=True,
    )
    assert result["text"] == "API_RECOVERY_OK"
    assert result["resolved_plane"] == "api"
    assert result["fallback_occurred"] is True
    assert result["fallback_from"] == "cli"
    assert result["fallback_reason"] == "cli_capability_unavailable"


def test_metered_api_requires_only_owner_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "METERED_API_ENABLED", False)
    with pytest.raises(RuntimeError, match="disabled by server policy"):
        server._require_metered_api_enabled()

    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    server._require_metered_api_enabled()


@pytest.mark.parametrize(
    ("source", "error", "expected"),
    [
        ("cli", RuntimeError("stop reason: cancelled"), "cli_cancelled"),
        ("cli", TimeoutError(), "cli_timeout"),
        ("cli", RuntimeError("HTTP 429 rate limit"), "cli_rate_limited"),
        ("cli", RuntimeError("provider overloaded at capacity"), "cli_congested"),
        ("cli", RuntimeError("OAuth token expired"), "cli_authentication_failed"),
        (
            "cli",
            RuntimeError("Grok returned a non-answer completion twice"),
            "cli_incomplete_response",
        ),
        ("cli", RuntimeError("ACP runtime exited"), "cli_runtime_unavailable"),
        ("api", ConnectionError("network connection failed"), "api_transport_failure"),
        ("api", RuntimeError("unexpected provider failure"), "api_runtime_failure"),
    ],
)
def test_fallback_reason_is_precise_and_plane_specific(
    source: str, error: Exception, expected: str
) -> None:
    assert server._classify_fallback_reason(source, error) == expected


def test_circuit_breaker_opens_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    server._CIRCUIT_BREAKERS.clear()
    monkeypatch.setattr(server, "BREAKER_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(server, "BREAKER_COOLDOWN_SECONDS", 5)
    server._breaker_failure("cli", "grok-live")
    server._breaker_failure("cli", "grok-live")
    snapshot = server._breaker_snapshot()["cli:grok-live"]
    assert snapshot["open"] is True
    assert snapshot["trips"] == 1
    with pytest.raises(RuntimeError, match="circuit breaker open"):
        server._breaker_before_call("cli", "grok-live")
    server._breaker_success("cli", "grok-live")
    assert server._breaker_snapshot()["cli:grok-live"]["open"] is False


@pytest.mark.asyncio
async def test_pull_request_review_is_bounded_read_only_courier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_agent(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"status": "complete", "text": "No blocking findings."}

    monkeypatch.setattr(server, "agent", fake_agent)
    result = await server.review_pull_request(
        "owner/repo",
        42,
        "Review me",
        "+ changed line",
        ci_summary="tests pass",
    )
    assert result["read_only"] is True
    assert result["review"] == "No blocking findings."
    assert "+ changed line" in str(captured["workspace_context"])
    assert captured["disable_tools"] == ["web", "x_search", "remote_code_execution"]


@pytest.mark.asyncio
async def test_failed_agent_job_persists_terminal_error_for_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0)
    release = asyncio.Event()

    async def boom(**_kwargs: object) -> dict:
        await release.wait()
        raise RuntimeError("simulated agent failure")

    monkeypatch.setattr(server, "_execute_team_turn", boom)
    pending = await server.agent(task="fail please")
    assert pending["status"] == "pending"
    job_id = pending["job_id"]
    release.set()
    # Wait until the background task has written the terminal payload, then
    # drop the in-memory task so the poll must read SQLite (restart-shaped path).
    for _ in range(50):
        stored = await server.STATE.load_agent_job(job_id)
        if stored and stored.get("status") in {"complete", "error"}:
            break
        await asyncio.sleep(0.02)
    server._DURABLE_JOBS.clear()
    result = await server.agent_result(job_id)
    assert result["status"] == "error"
    assert "simulated agent failure" in result["text"]
    assert result.get("stop_reason") == "error"
    assert result.get("job_id") == job_id


@pytest.mark.asyncio
async def test_post_provider_projection_failure_preserves_reported_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = server.PublicStateStore(tmp_path / "projection-failure.db")
    await state.initialize()
    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", False)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", False)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})

    async def completed_turn(**_kwargs: object) -> dict:
        return {
            "text": "Completed provider answer.",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.07,
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 3,
                "total_tokens": 12,
            },
            "orchestration": {"route": "direct"},
        }

    finalize_calls = 0

    async def fail_first_finalize(job_id: str, payload: dict) -> dict:
        nonlocal finalize_calls
        del job_id
        finalize_calls += 1
        if finalize_calls == 1:
            raise RuntimeError("projection write failed")
        return payload

    monkeypatch.setattr(server, "_execute_team_turn", completed_turn)
    monkeypatch.setattr(server, "_finalize_job_payload", fail_first_finalize)

    result = await server.agent(task="Complete this task")

    assert result["status"] == "error"
    assert result["error_type"] == "RuntimeError"
    assert result["cost_usd"] == pytest.approx(0.07)
    assert result["total_tokens"] == 12
    assert result["incurred_attempts"][0]["stage"] == "agent_result"
    stored = await state.load_agent_job(result["job_id"])
    assert stored is not None
    assert stored["payload"]["cost_usd"] == pytest.approx(0.07)
    telemetry = await state.telemetry_summary()
    assert telemetry["sample_size"] == 1
    assert telemetry["cost_usd"] == pytest.approx(0.07)
    assert telemetry["routes"] == [
        {
            "name": "error",
            "calls": 1,
            "verified": 1,
            "successes": 0,
            "cost_usd": 0.07,
            "success_rate": 0.0,
        }
    ]


@pytest.mark.asyncio
async def test_pending_pull_request_review_keeps_metadata_on_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0)
    release = asyncio.Event()

    async def slow_turn(**_kwargs: object) -> dict:
        await release.wait()
        return {
            "text": "No blocking findings.",
            "plane": "test",
            "stop_reason": "EndTurn",
            "workspace_attached": False,
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", slow_turn)
    pending = await server.review_pull_request(
        "owner/repo",
        7,
        "Pending review",
        "+ line",
    )
    assert pending["status"] in {"pending", "continue"}
    assert pending["review_kind"] == "pull_request"
    assert pending["repository"] == "owner/repo"
    release.set()
    complete = await server.agent_result(pending["job_id"])
    assert complete["status"] == "complete"
    assert complete["review_kind"] == "pull_request"
    assert complete["repository"] == "owner/repo"
    assert complete["pull_number"] == 7
    assert complete["read_only"] is True
    assert complete["review"] == "No blocking findings."


@pytest.mark.asyncio
async def test_public_dashboard_manifest_and_okf_routes_exist() -> None:
    dashboard = await server.control_center(None)
    manifest = await server.webmcp_manifest(None)
    okf = await server.okf_index(None)
    assert dashboard.status_code == 200
    assert "Control Center" in dashboard.body.decode()
    assert manifest.status_code == 200
    assert '"benchmarks":"/benchmarkz"' in manifest.body.decode()
    assert okf.status_code == 200
    assert "Benchmark receipts" in okf.body.decode()


@pytest.mark.asyncio
async def test_unified_api_call_obeys_owner_policy_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_resolve(*args, **kwargs):
        return "api", _catalogs()

    async def fake_system(*args, **kwargs):
        return "system"

    async def fake_api_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"text": "API_OK", "model": "grok-api-live", "cost_usd": 0.01}

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system)
    monkeypatch.setattr(xai_api, "chat", fake_api_chat)
    arguments = {
        "model": None,
        "effort": None,
        "plane": "api",
        "fallback_policy": "same_plane",
        "agentic": False,
        "max_turns": 1,
        "allow_web": False,
        "allow_x_search": False,
        "allow_code": False,
    }
    monkeypatch.setattr(server, "METERED_API_ENABLED", False)
    with pytest.raises(RuntimeError, match="disabled by server policy"):
        await server._run_unified("test", **arguments)
    assert calls == 0

    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    result = await server._run_unified("test", **arguments)
    assert result["text"] == "API_OK"
    assert calls == 1


@pytest.mark.asyncio
async def test_discovery_reports_owner_disabled_spend_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return _catalogs()

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "METERED_API_ENABLED", False)
    description = await server.grok_mcp_discover_self()
    assert description["bootstrap"]["can_spend_api"] is False
    assert description["credential_planes"]["api"]["can_spend"] is False
    assert description["credential_planes"]["notices"][0]["prompt_user"] is False


@pytest.mark.asyncio
async def test_destructive_tools_require_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(server, "STATE", server.PublicStateStore(tmp_path / "delete.db"))
    with pytest.raises(ValueError, match="confirm_delete=true"):
        await server.forget_session("team:safe")
    with pytest.raises(ValueError, match="confirm_delete=true"):
        await server.forget_fact(1)
    with pytest.raises(ValueError, match="confirm_delete=true"):
        await server.xai_delete_file("file_safe")


def test_api_key_is_required_only_for_api_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert xai_api.api_key_configured() is False
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        xai_api._require_key()


def test_local_and_unsafe_media_urls_are_rejected() -> None:
    for value in (
        "/private/secret.png",
        "file:///private/secret.png",
        "http://localhost/a.png",
        "https://127.0.0.1/a.png",
    ):
        with pytest.raises(ValueError, match="public https URL"):
            server._validated_media_url(value, "image_url")
    assert (
        server._validated_media_url("https://images.example.com/a.png", "image_url")
        == "https://images.example.com/a.png"
    )


@pytest.mark.asyncio
async def test_upload_accepts_bytes_not_local_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_upload(content: bytes, *, filename: str, expires_after_seconds: int) -> dict:
        captured.update(
            content=content,
            filename=filename,
            expires_after_seconds=expires_after_seconds,
        )
        return {"file_id": "file_test"}

    monkeypatch.setattr(xai_api, "upload_file", fake_upload)
    result = await server.xai_upload_file(
        "note.txt", base64.b64encode(b"hello").decode(), expires_after_seconds=300
    )
    # Tool results are wrapped in a durable-job envelope (job_id/job_kind/status);
    # assert the underlying payload rather than exact-dict equality.
    assert result["file_id"] == "file_test"
    assert result["status"] == "complete"
    assert result["job_kind"] == "xai_upload_file"
    assert captured["content"] == b"hello"
    assert captured["expires_after_seconds"] == 3_600
    with pytest.raises(ValueError, match="path components"):
        await server.xai_upload_file("../private.txt", "aGVsbG8=")


def test_media_generation_detector_and_guard() -> None:
    from unigrok_public.server import (
        _media_generation_available,
        _media_unavailable_result,
        _wants_media_generation,
    )

    assert _wants_media_generation("Make me a picture of a blue cat") == "image"
    assert _wants_media_generation("create a short video of waves") == "video"
    assert _wants_media_generation("What is 2+2?") is None
    assert _wants_media_generation("parse images from a folder") is None

    no_api = {"api": {"ready": False, "image_models": []}}
    assert not _media_generation_available(no_api, "image")
    assert not _media_generation_available(no_api, "video")
    msg = _media_unavailable_result("image")
    assert "XAI_API_KEY" in msg["text"] and msg["cost_usd"] == 0.0
    assert "won't fake" in msg["text"]


def test_per_client_auto_approve_uses_native_mechanism() -> None:
    from unigrok_public import server

    cc = server._client_onboarding_plan("claude_code", "global")["auto_approve"]
    assert cc["merge_into"] == "permissions.allow"
    assert cc["entry"]["permissions"]["allow"] == ["mcp__grok__agent", "mcp__grok__agent_result"]

    cx = server._client_onboarding_plan("codex", "global")["auto_approve"]
    assert cx["target"].endswith("config.toml")
    assert 'approval_mode = "auto"' in cx["toml"]

    ag = server._client_onboarding_plan("antigravity", "global")["auto_approve"]
    assert ag["entry"]["userSettings"]["globalPermissionGrants"]["allow"] == [
        "mcp(grok/agent)",
        "mcp(grok/agent_result)",
    ]
    assert ag["gemini_cli_alternative"]["entry"]["mcpServers"]["grok"]["trust"] is True
    ag_proj = server._auto_approve("antigravity", "project")
    assert ag_proj["target"] == ".gemini/settings.json"
    assert ag_proj["entry"]["globalPermissionGrants"]["allow"] == [
        "mcp(grok/agent)",
        "mcp(grok/agent_result)",
    ]

    gh = server._client_onboarding_plan("github_copilot", "global")
    assert "--allow-tool 'grok(agent)'" in gh["auto_approve"]["command"]
    assert gh["mcp_server"]["target"] == "~/.copilot/mcp-config.json"
    assert gh["mcp_server"]["entry"]["mcpServers"]["grok"]["url"].endswith("/mcp")
    assert gh["mcp_server"]["vscode_alternative"]["target"] == ".vscode/mcp.json"
    gh_proj = server._client_onboarding_plan("github_copilot", "project")
    assert gh_proj["mcp_server"]["target"] == ".copilot/mcp-config.json"
    assert any(
        f["path"] == ".github/instructions/unigrok.instructions.md" for f in gh_proj["files"]
    )

    # Clients without a verified mechanism must NOT fabricate one.
    assert "auto_approve" not in server._client_onboarding_plan("generic", "global")
    # None of the auto-approve configs carry a credential.
    for c in ("claude_code", "codex", "antigravity", "github_copilot"):
        blob = json.dumps(server._client_onboarding_plan(c, "global"))
        assert "XAI_API_KEY" not in blob and "CURSOR_API_KEY" not in blob


@pytest.mark.asyncio
async def test_failed_catalog_probe_uses_short_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    api_calls = 0

    async def fake_cli() -> dict:
        return {"ready": False, "models": []}

    async def fake_api() -> dict:
        nonlocal api_calls
        api_calls += 1
        return {
            "ready": api_calls > 1,
            "configured": True,
            "models": [{"id": "grok-live"}] if api_calls > 1 else [],
        }

    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server, "_probe_cli", fake_cli)
    monkeypatch.setattr(xai_api, "probe_models", fake_api)
    monkeypatch.setattr(xai_api, "credential_cache_key", lambda: "test-key")
    server._CATALOG_CACHE.clear()

    assert (await server._catalogs())["api"]["ready"] is False
    clock[0] += 4
    assert (await server._catalogs())["api"]["ready"] is False
    assert api_calls == 1
    clock[0] += 2
    assert (await server._catalogs())["api"]["ready"] is True
    assert api_calls == 2
    server._CATALOG_CACHE.clear()
