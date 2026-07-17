from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from unigrok_public import server
from unigrok_public.harness import (
    format_session_prompt,
    is_nonanswer_completion,
    workspace_courier,
)
from unigrok_public.state import PublicStateStore, normalize_session, redact_secrets


@pytest.mark.asyncio
async def test_state_persists_redacted_sessions_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = PublicStateStore(path)
    count = await first.append_turn(
        "team:alpha",
        "Use XAI_API_KEY=super-secret",
        "Verified result",
        model="grok-test",
        plane="cli",
    )
    assert count == 2

    second = PublicStateStore(path)
    messages = await second.load_messages("team:alpha")
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert "super-secret" not in messages[0]["content"]
    assert "[REDACTED]" in messages[0]["content"]
    sessions = await second.list_sessions()
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["model"] == "grok-test"
    assert await second.health() is True

    assert await second.delete_session("team:alpha") is True
    assert await second.load_messages("team:alpha") == []


@pytest.mark.asyncio
async def test_knowledge_is_scoped_ranked_deduplicated_and_deletable(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "knowledge.db")
    global_id = await store.save_fact("Public releases require live IDE testing", scope="global")
    duplicate_id = await store.save_fact("Public releases require live IDE testing", scope="global")
    team_id = await store.save_fact("Team alpha uses bounded workspace context", scope="team:alpha")
    await store.save_fact("Unrelated team constraint", scope="team:beta")
    assert duplicate_id == global_id

    facts = await store.search_facts(
        "team alpha workspace context and public testing", scope="team:alpha", limit=5
    )
    assert {item["id"] for item in facts} == {global_id, team_id}
    await store.touch_facts([team_id])
    touched = await store.search_facts("bounded workspace", scope="team:alpha")
    assert touched[0]["uses"] == 1
    assert await store.delete_fact(team_id) is True
    assert await store.delete_fact(team_id) is False


def test_redaction_session_validation_and_workspace_courier() -> None:
    assert "secret" not in redact_secrets("Authorization: Bearer secret")
    assert normalize_session("vscode:project-one") == "vscode:project-one"
    with pytest.raises(ValueError, match="session must"):
        normalize_session("bad session name")

    courier = workspace_courier(
        "Failure with XAI_API_KEY=do-not-store",
        "project",
        max_chars=1_000,
    )
    assert "do-not-store" not in courier
    assert "grants no filesystem" in courier
    with pytest.raises(ValueError, match="exceeds"):
        workspace_courier("x" * 20, "project", max_chars=10)


def test_history_format_and_nonanswer_contract() -> None:
    prompt = format_session_prompt(
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ],
        "continue",
    )
    assert prompt.index("first question") < prompt.index("first answer") < prompt.index("continue")
    assert is_nonanswer_completion("I'll inspect the repository now.", prompt="Audit it")
    assert is_nonanswer_completion("Plan:\n1. Inspect files\n2. Run tests", prompt="Audit it")
    assert not is_nonanswer_completion("Findings: the state volume is missing.", prompt="Audit it")
    assert not is_nonanswer_completion(
        "Plan:\n1. Inspect files\n2. Run tests", prompt="Give me a plan"
    )


@pytest.mark.asyncio
async def test_unified_agent_recovers_one_nonanswer_on_same_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    catalogs = {
        "cli": {"ready": True, "models": ["grok-test"], "default_model": "grok-test"},
        "api": {"ready": False, "models": []},
    }

    async def fake_resolve(*args, **kwargs):
        return "cli", catalogs

    async def fake_system(*args, **kwargs):
        return "system"

    async def fake_build(prompt: str, **kwargs: object) -> dict:
        calls.append(prompt)
        if len(calls) == 1:
            return {"text": "I'll inspect this now.", "model": "grok-test", "cost_usd": 0.0}
        return {
            "text": "Findings: the recovered answer is complete.",
            "model": "grok-test",
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    result = await server._run_unified(
        "Audit this",
        model=None,
        effort="high",
        plane="cli",
        fallback_policy="same_plane",
        agentic=True,
        max_turns=4,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
    )
    assert result["text"].startswith("Findings:")
    assert result["completion_recovery"]["succeeded"] is True
    assert len(calls) == 2
    assert "previous response" in calls[1]


@pytest.mark.asyncio
async def test_agent_session_couriers_context_and_reuses_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PublicStateStore(tmp_path / "agent.db")
    captured: list[dict] = []

    async def fake_unified(prompt: str, **kwargs) -> dict:
        captured.append({"prompt": prompt, **kwargs})
        return {
            "text": "Findings: team response complete.",
            "model": "grok-test",
            "plane": "grok_cli_oauth",
            "resolved_plane": "cli",
            "cost_usd": 0.0,
            "degraded": False,
        }

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_run_unified", fake_unified)
    async def fake_catalogs(*, refresh: bool = False) -> dict:
        return {
            "cli": {"ready": True, "models": ["grok-test"], "default_model": "grok-test"},
            "api": {"ready": False, "models": [], "image_models": []},
        }

    async def fake_route(prompt: str, catalogs: dict) -> dict[str, str]:
        return {"route": "direct", "specialist_prompt": prompt}

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_route_task", fake_route)
    server._SESSION_LOCKS.clear()
    first = await server.agent(
        "Review the failure",
        session="vscode:alpha",
        workspace_context="trace XAI_API_KEY=hidden-value",
        workspace_label="alpha",
        use_memory=False,
    )
    assert first["session_message_count"] == 2
    assert first["workspace_context_supplied"] is True
    assert "hidden-value" not in str(captured[0]["system_context"])

    second = await server.agent(
        "Continue the review",
        session="vscode:alpha",
        use_memory=False,
    )
    assert second["session_message_count"] == 4
    assert "Review the failure" in captured[1]["prompt"]
    assert "team response complete" in captured[1]["prompt"]


@pytest.mark.asyncio
async def test_forget_session_serializes_with_active_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = PublicStateStore(tmp_path / "forget.db")
    await state.append_turn("team:race", "question", "answer")
    monkeypatch.setattr(server, "STATE", state)
    server._SESSION_LOCKS.clear()
    lock = server._session_lock("team:race")
    await lock.acquire()
    deletion = asyncio.create_task(server.forget_session("team:race", confirm_delete=True))
    await asyncio.sleep(0)
    assert deletion.done() is False
    lock.release()
    result = await deletion
    assert result["status"] == "deleted"
    assert await state.load_messages("team:race") == []


@pytest.mark.asyncio
async def test_telemetry_summary_and_explicit_benchmark_feedback(tmp_path: Path) -> None:
    state = PublicStateStore(tmp_path / "telemetry.db")
    telemetry_id = await state.save_telemetry(
        {
            "caller": "cursor-public",
            "request_kind": "agent",
            "route": "code",
            "requested_plane": "auto",
            "resolved_plane": "api",
            "model": "grok-build-live",
            "success": None,
            "verified": False,
            "latency_ms": 1250,
            "cost_usd": 0.02,
            "fallback_reason": "cli_cancelled",
            "stop_reason": "EndTurn",
        }
    )
    before = await state.telemetry_summary()
    assert before["sample_size"] == 1
    assert before["verified_samples"] == 0
    assert before["callers"][0]["name"] == "cursor-public"
    assert before["fallbacks"][0]["name"] == "cli_cancelled"
    assert await state.record_benchmark_result(telemetry_id, True, "expected marker present")
    after = await state.telemetry_summary()
    assert after["verified_samples"] == 1
    assert after["verified_success_rate"] == 1.0


def test_deep_harness_prefix_and_leak_guard() -> None:
    from unigrok_public.harness import (
        DEEP_HARNESS_PROMPT,
        apply_deep_harness,
        is_nonanswer_completion,
        leaks_deep_harness,
    )

    deep_prompt = apply_deep_harness("Solve the puzzle.")
    assert deep_prompt.startswith(DEEP_HARNESS_PROMPT)
    assert deep_prompt.endswith("Solve the puzzle.")

    clean = "The maximum sum is 60 via 5, 8, 12, 1, 15, 8, 11."
    leaky = "PROVER — candidate 2 survived the vote; the j-space run confirms 60."
    assert not leaks_deep_harness(clean)
    assert leaks_deep_harness(leaky)
    # A leaked deliberation is a failed completion only for deep-mode prompts.
    assert is_nonanswer_completion(leaky, prompt=deep_prompt)
    assert not is_nonanswer_completion(clean, prompt=deep_prompt)
    assert not is_nonanswer_completion(leaky, prompt="Summarize our red team exercise.")


def test_auto_deepen_heuristic_targets_hard_reasoning_only() -> None:
    from unigrok_public.harness import should_auto_deepen

    assert should_auto_deepen("Find the optimal path through this grid puzzle.")
    assert should_auto_deepen("Compute 2 to the power of 64 exactly.")
    assert should_auto_deepen("Critique my implementation plan for the migration.")
    assert should_auto_deepen("Is there a race condition in this handler?")
    assert not should_auto_deepen("What does this error message mean?")
    assert not should_auto_deepen("Write a haiku about deployment Fridays.")
    assert not should_auto_deepen("Rename this variable to something clearer.")


def test_final_polish_triggers_on_deliberation_residue_only() -> None:
    from unigrok_public.harness import final_polish_prompt, needs_final_polish

    clean = "The maximum sum is 60 via 5, 8, 12, 1, 15, 8, 11."
    messy = "## Ranking note (internal)\n1. 60\n2. 59\n\nThe answer is 60."
    leaky = "PROVER — verified. The answer is 60."
    assert not needs_final_polish(clean)
    assert needs_final_polish(messy)
    assert needs_final_polish(leaky)
    assert "Draft" in final_polish_prompt(messy)


def test_level_ladder_maps_effort_shape_and_voters() -> None:
    from unigrok_public.harness import LEVEL_NAMES, resolve_level

    assert LEVEL_NAMES[0] == "none" and LEVEL_NAMES[-1] == "ultra"
    assert resolve_level(None) is None
    assert resolve_level("bogus") is None
    low = resolve_level("low")
    assert low == {"effort": "low", "shape": "direct", "voters": 0}
    assert resolve_level("MAX")["shape"] == "deep"
    ultra = resolve_level("ultra")
    assert ultra["shape"] == "hive" and ultra["voters"] == 5


def test_done_vote_parse_and_prompt() -> None:
    from unigrok_public.harness import build_done_vote_prompt, parse_done_vote

    assert parse_done_vote('{"done":"yes","why":"gave the answer"}') is True
    assert parse_done_vote('noise {"done":"no","why":"promised later"} tail') is False
    assert parse_done_vote("not json") is None
    assert parse_done_vote('{"done":"maybe"}') is None
    prompt = build_done_vote_prompt("solve x", "the answer is 42")
    assert "## Request" in prompt and "## Reply" in prompt
