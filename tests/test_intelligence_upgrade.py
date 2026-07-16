import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src import utils
from src.http_server import _layer_to_chat_completion, get_xai_model_ids
from src.tools.system import list_models
from src.utils import (
    GrokSessionStore,
    MetaLayer,
    UNIGROK_SAFETY_POLICY,
    compose_system_prompt,
    load_grok_profile,
    load_grok_prompt,
    redact_secrets,
)


def _tool_names():
    return {tool.function.name for tool in utils._build_custom_tools()}


def test_grok_profile_loading_from_namespace():
    profile = load_grok_profile("grok-code-fast-1")

    assert profile["profile"] == "grok-code-fast-1"
    assert profile["temperature"] == 0.4
    assert profile["top_p"] == 0.95
    assert profile["system_prompt_ref"] == "grok_adapter.md"


def test_grok_profile_missing_model_maps_to_default_profile():
    profile = load_grok_profile("grok-build-0.1")

    assert profile["profile"] == "grok-code-fast-1"
    assert profile["thinking_mode"] is False


def test_grok_profile_clamps_values_and_rejects_prompt_traversal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    hyperparams = root / ".grok" / "hyperparams"
    prompts = root / ".grok" / "prompts"
    hyperparams.mkdir(parents=True)
    prompts.mkdir(parents=True)
    (hyperparams / "unsafe.json").write_text(
        '{"temperature": -4, "top_p": 8, "system_prompt_ref": "../secret.md"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(utils.PathResolver, "get_service_root", staticmethod(lambda: root))

    profile = load_grok_profile("unsafe")

    assert profile["temperature"] == 0.0
    assert profile["top_p"] == 1.0
    assert profile["system_prompt_ref"] == "grok_adapter.md"


def test_grok_prompt_loader_blocks_traversal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    prompts = root / ".grok" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "ok.md").write_text("adapter prompt", encoding="utf-8")

    monkeypatch.setattr(utils.PathResolver, "get_service_root", staticmethod(lambda: root))

    assert load_grok_prompt("ok.md") == "adapter prompt"
    assert load_grok_prompt("../secret.md") == ""


def test_compose_system_prompt_ordering():
    prompt = compose_system_prompt(
        "workspace context",
        adapter_prompt="adapter prompt",
        memory_notes="memory notes",
        caller_instructions="caller instructions",
    )

    assert prompt.index(UNIGROK_SAFETY_POLICY.strip()) < prompt.index("workspace context")
    assert prompt.index("workspace context") < prompt.index("memory notes")
    assert prompt.index("memory notes") < prompt.index("adapter prompt")
    assert prompt.index("adapter prompt") < prompt.index("caller instructions")


def test_adapter_prompts_are_localized():
    prompt_dir = Path(__file__).resolve().parents[1] / ".grok" / "prompts"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in prompt_dir.glob("*.md"))

    forbidden = [
        "legacy_terminal",
        "CURRENT_STATUS.md",
        ".agent/ground",
        "AgentixAIOS",
        "dev/app/legacy_terminal",
    ]
    for token in forbidden:
        assert token not in combined


def test_redact_secrets_strips_keys_and_bearer_tokens():
    text = (
        "XAI_API_KEY=xai-abc123456789 "
        "OPENAI_API_KEY=sk-proj-abc123456789 "
        "Authorization: Bearer abc.defghi123456"
    )

    redacted = redact_secrets(text)

    assert "xai-abc" not in redacted
    assert "sk-proj" not in redacted
    assert "abc.defghi" not in redacted
    assert "[REDACTED" in redacted


def test_redact_secrets_strips_github_jwt_and_google_keys():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signaturepaddingvalue12"
    )
    text = (
        f"token=github_pat_11AAAAAAAABBBBBBBBBB "
        f"classic=ghp_abcdefghijklmnopqrst "
        f"google=AIzaSyA-abcdefghijklmnopqrstuv "
        f"jwt={jwt}"
    )
    redacted = redact_secrets(text)
    assert "github_pat_" not in redacted
    assert "ghp_" not in redacted
    assert "AIza" not in redacted
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted
    assert "[REDACTED" in redacted


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: BEARER abc.defghi123456", "abc.defghi123456"),
        ("Authorization: Bearer\tabc.defghi123456", "abc.defghi123456"),
        ("OpenAI_Api_Key = secretvalue123", "secretvalue123"),
        ("ghs_abcdefghijklmnopqrst", "ghs_abcdefghijklmnopqrst"),
        (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepaddingvalue12",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        ),
    ],
)
def test_redact_secrets_fast_path_preserves_case_and_whitespace_coverage(text, secret):
    redacted = redact_secrets(text)

    assert secret not in redacted
    assert "[REDACTED" in redacted


def test_redact_secrets_fast_path_preserves_benign_text_and_input_coercion():
    assert redact_secrets("ordinary telemetry") == "ordinary telemetry"
    assert redact_secrets(123) == "123"


@pytest.mark.asyncio
async def test_dispatch_internal_tool_redacts_and_bounds_output(monkeypatch):
    async def noisy_tool() -> str:
        return "start XAI_API_KEY=xai-abc123456789 " + ("a" * 2000)

    monkeypatch.setenv("UNIGROK_TOOL_OUTPUT_MAX_CHARS", "1200")
    utils.register_internal_tool("__noisy_tool__", noisy_tool)
    try:
        obs = await utils.dispatch_internal_tool("__noisy_tool__", {})
    finally:
        utils._INTERNAL_TOOL_REGISTRY.pop("__noisy_tool__", None)

    assert obs.success is True
    assert "xai-abc" not in obs.content
    assert "[REDACTED" in obs.content
    assert "truncated" in obs.content
    assert len(obs.content) < 1400


@pytest.mark.asyncio
async def test_store_recovers_after_closed_connection(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "grok_sessions_test.db")
    try:
        await store.save_telemetry("before", "API", 1, 0.1, 0.0)
        stale_conn = store._conn
        await stale_conn.close()

        await store.save_telemetry("after", "API", 1, 0.2, 0.0)
        stats = await store.get_telemetry_stats()

        assert len(stats) == 2
        assert stats[0]["intent"] == "after"
        assert store._conn is not stale_conn
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agentloop_parallel_dispatch_converts_unexpected_exception(monkeypatch):
    loop = utils.AgentLoop(
        policy=utils.AgentLoopPolicy(enable_parallel_dispatch=True),
        dynamic_sys_prompt="System",
        model="grok-4.3",
    )
    tc = MagicMock()
    tc.id = "call-bad"
    tc.function.name = "__bad_dispatch__"

    async def broken_dispatch(_tc):
        raise RuntimeError("unexpected xai-abc123456789")

    monkeypatch.setattr(loop, "_dispatch_one", broken_dispatch)

    observations = await loop._dispatch_parallel([tc])

    assert len(observations) == 1
    assert observations[0].success is False
    assert observations[0].tool_name == "__bad_dispatch__"
    assert observations[0].tool_call_id == "call-bad"
    assert "xai-abc" not in observations[0].content


@pytest.mark.asyncio
async def test_communicate_with_timeout_terminates_and_reaps():
    class HangingProc:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.waited = 0

        async def communicate(self):
            await asyncio.sleep(10)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited += 1
            return 0

    proc = HangingProc()

    with pytest.raises(asyncio.TimeoutError):
        await utils.communicate_with_timeout(proc, 0.01)

    assert proc.terminated is True
    assert proc.waited >= 1


@pytest.mark.asyncio
async def test_task_memory_migration_indexes_and_roundtrip(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "grok_sessions_test.db")
    try:
        await store._ensure_initialized()
        async with store._conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row[0] >= 3

        async with store._conn.execute("SELECT name FROM sqlite_master WHERE type='index';") as cursor:
            indexes = {row[0] for row in await cursor.fetchall()}
            assert "idx_task_memory_hash" in indexes
            assert "idx_task_memory_context_id" in indexes
            assert "idx_task_memory_created_at" in indexes

        await store.save_task_memory(
            prompt="fix pytest failure in utils routing",
            outcome_summary="Fixed with xai-test-secret xai-abc123456789",
            plane="API",
            model="grok-4.3",
            profile="grok-4-1-fast-reasoning",
            success=1,
            latency=0.2,
            cost=0.01,
            context_id="ctx-a",
        )

        memories = await store.get_similar_task_memories("pytest routing fix", context_id="ctx-a")

        assert await store.get_task_memory_count() == 1
        assert memories[0]["profile"] == "grok-4-1-fast-reasoning"
        assert "xai-abc" not in memories[0]["outcome_summary"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_task_memory_prefers_context_id_then_overlap(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "grok_sessions_test.db")
    try:
        await store.save_task_memory(
            prompt="unrelated media generation",
            outcome_summary="context match",
            plane="API",
            model="grok-4.3",
            profile="p1",
            success=1,
            latency=0.1,
            cost=0.0,
            context_id="ctx-match",
        )
        await store.save_task_memory(
            prompt="pytest routing memory exact overlap",
            outcome_summary="term overlap",
            plane="API",
            model="grok-4.3",
            profile="p2",
            success=1,
            latency=0.1,
            cost=0.0,
            context_id="ctx-other",
        )

        memories = await store.get_similar_task_memories("pytest routing memory", context_id="ctx-match")

        assert memories[0]["context_id"] == "ctx-match"
        assert any(item["profile"] == "p2" for item in memories)
    finally:
        await store.close()


def test_cloudrun_tool_advertising_omits_local_tools(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.delenv("ENABLE_GIT_WRITE", raising=False)

    names = _tool_names()

    assert "generate_image" in names
    assert "get_file_content" in names
    assert "read_local_file" not in names
    assert "git_status" not in names
    assert "run_local_tests" not in names
    assert "git_apply_patch" not in names


def test_local_tool_advertising_gates_mutating_git(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.delenv("ENABLE_GIT_WRITE", raising=False)

    names = _tool_names()

    assert "read_local_file" in names
    assert "git_status" in names
    assert "run_local_tests" in names
    assert "git_apply_patch" not in names
    assert "git_commit" not in names
    assert "git_create_branch" not in names

    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    write_names = _tool_names()

    assert "git_apply_patch" in write_names
    assert "git_commit" in write_names
    assert "git_create_branch" in write_names


def test_http_unigrok_metadata_includes_route_profile_policy():
    layer = MetaLayer(
        generation="done",
        plane="API",
        route="agentic",
        profile="grok-4-1-fast-reasoning",
        policy_mode="local-readonly",
        context_id="ctx-test",
    )

    body = _layer_to_chat_completion(layer, "unigrok-agent")

    assert body["unigrok"]["route"] == "agentic"
    assert body["unigrok"]["profile"] == "grok-4-1-fast-reasoning"
    assert body["unigrok"]["policy_mode"] == "local-readonly"


def test_parse_grok_cli_models_output_with_default_marker():
    parsed = utils._parse_grok_cli_models_output(
        """
Default model: grok-build

Available models:
  * grok-build (default)
    grok-code-fast-1
"""
    )

    assert parsed["default_model"] == "grok-build"
    assert parsed["models"] == ["grok-build", "grok-code-fast-1"]


def test_parse_grok_cli_models_output_handles_ansi_logs_and_warnings():
    parsed = utils._parse_grok_cli_models_output(
        "\x1b[2m2026-07-01T19:52:05Z\x1b[0m \x1b[33mWARN\x1b[0m Failed to fetch models\n"
        "Default model: grok-build\n"
        "Available models:\n"
        "  * grok-build (default)\n"
    )

    assert parsed["models"] == ["grok-build"]
    assert any("Failed to fetch models" in warning for warning in parsed["warnings"])


@pytest.mark.asyncio
async def test_grok_cli_discovery_failure_returns_fallback(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")

    async def raise_missing(*args, **kwargs):
        raise FileNotFoundError("missing grok")

    monkeypatch.setattr(utils.asyncio, "create_subprocess_exec", raise_missing)

    discovered = await utils.discover_grok_cli_models()

    assert discovered["available"] is False
    assert discovered["default_model"] == "grok-composer-2.5-fast"
    assert [item["id"] for item in discovered["models"]] == utils.FALLBACK_GROK_CLI_MODELS
    assert discovered["warnings"]


@pytest.mark.asyncio
async def test_grok_cli_discovery_rejects_api_key_backed_cli(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    server_secrets = {
        "XAI_API_KEY": "xai-inference",
        "XAI_MANAGEMENT_API_KEY": "xai-management",
        "GROK_API_KEY": "grok-api",
        "OPENAI_API_KEY": "openai-api",
        "ANTHROPIC_API_KEY": "anthropic-api",
        "CLAUDE_API_KEY": "claude-api",
        "GEMINI_API_KEY": "gemini-api",
        "GOOGLE_API_KEY": "google-api",
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/vertex-adc.json",
        "UNIGROK_API_KEYS": "gateway-client-secret",
    }
    for name, value in server_secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GROK_AUTH_PATH", "/oauth/auth.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "benign-project-id")
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (
                b"You are using XAI_API_KEY.\nDefault model: grok-4.5\n"
                b"Available models:\n  * grok-4.5 (default)\n",
                b"",
            )

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProc()

    monkeypatch.setattr(utils.asyncio, "create_subprocess_exec", fake_exec)

    discovered = await utils.discover_grok_cli_models()

    assert discovered["available"] is False
    assert server_secrets.keys().isdisjoint(captured["env"])
    assert captured["env"]["GROK_AUTH_PATH"] == "/oauth/auth.json"
    assert captured["env"]["GOOGLE_CLOUD_PROJECT"] == "benign-project-id"
    assert any("not independent" in warning for warning in discovered["warnings"])


@pytest.mark.asyncio
async def test_grok_cli_discovery_skips_cloudrun(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    mock_exec = AsyncMock(side_effect=AssertionError("CLI should not run in Cloud Run"))
    monkeypatch.setattr(utils.asyncio, "create_subprocess_exec", mock_exec)

    discovered = await utils.discover_grok_cli_models()

    assert discovered["models"] == []
    assert discovered["default_model"] is None
    assert "Cloud Run" in discovered["warnings"][0]
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_list_models_renders_api_cli_and_profile_sections(monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setattr(
        "src.tools.system.build_model_catalog",
        AsyncMock(
            return_value={
                "xai_api": [{"id": "grok-api-model", "context_window": 123}],
                "grok_cli": [{"id": "grok-build", "default": True}],
                "local_profiles": [
                    {
                        "name": "grok-code-fast-1",
                        "temperature": 0.4,
                        "top_p": 0.95,
                        "thinking_mode": False,
                        "system_prompt_ref": "grok_adapter.md",
                    }
                ],
                "default_cli_model": "grok-build",
                "warnings": ["partial CLI discovery"],
                "sources": {},
            }
        ),
    )

    from src.tools.system import list_models_detailed
    rendered = await list_models_detailed()

    assert "## xAI API Models" in rendered
    assert "grok-api-model" in rendered
    assert "## Local Grok CLI Models" in rendered
    assert "grok-build" in rendered
    assert "## .grok Local Profiles" in rendered
    assert "grok-code-fast-1" in rendered
    assert "partial CLI discovery" in rendered


@pytest.mark.asyncio
async def test_http_model_ids_exclude_cli_and_profiles(monkeypatch):
    monkeypatch.setattr(
        "src.http_server.discover_xai_api_models",
        AsyncMock(
            return_value={
                "models": [{"id": "grok-api-only"}],
                "available": True,
                "warnings": [],
                "source": "xai_api",
            }
        ),
    )

    model_ids = await get_xai_model_ids()

    assert "unigrok-agent" in model_ids
    assert "grok-api-only" in model_ids
    assert "grok-build" not in model_ids
    assert "grok-code-fast-1" not in model_ids


@pytest.mark.asyncio
async def test_list_models_lightweight(monkeypatch):
    monkeypatch.setattr(
        "src.tools.system.discover_xai_api_models",
        AsyncMock(
            return_value={
                "models": [{"id": "grok-4.5"}, {"id": "grok-4.3"}],
                "available": True,
                "warnings": [],
                "source": "xai_api",
            }
        ),
    )
    res = await list_models()
    assert res == ["grok-4.5", "grok-4.3"]
