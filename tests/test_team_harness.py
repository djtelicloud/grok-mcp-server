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


def test_nonanswer_catches_generic_promise_preambles() -> None:
    # Live fast-route regression (2026-07-17): Grok returned exactly this preamble
    # with finish_reason final_answer and no body; "ground" is outside the curated
    # action-verb list, so only the generic bare-preamble guard catches it.
    live_sample = (
        "I'll ground the checklist in the actual flow, then start the answer "
        "at '## Checklist'"
    )
    prompt = "Update the contributor checklist. No preamble, start with ## Checklist."
    assert is_nonanswer_completion(live_sample, prompt=prompt)
    assert is_nonanswer_completion(
        "Sure — I'll structure the response around your three questions.", prompt=prompt
    )
    # Legitimate short replies must survive: delivered content after a delimiter,
    # a clarifying question, and a stated blocker are all real answers.
    assert not is_nonanswer_completion(
        "Sure thing, I'll be brief — the fix is to pin the revision.", prompt=prompt
    )
    assert not is_nonanswer_completion(
        "I'll assume you mean the staging cluster — is that right?", prompt=prompt
    )
    assert not is_nonanswer_completion(
        "I'll need the deploy logs from you before I can diagnose this.", prompt=prompt
    )
    assert not is_nonanswer_completion("The answer is 42.", prompt=prompt)


@pytest.mark.asyncio
async def test_fast_route_runs_nonanswer_recovery_and_cross_plane_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-agentic (fast/chat) route must reject a canned preamble-only CLI
    completion, retry once on the same plane, and then fall back CLI->API."""
    preamble = (
        "I'll ground the checklist in the actual flow, then start the answer "
        "at '## Checklist'"
    )
    build_calls: list[str] = []
    catalogs = {
        "cli": {"ready": True, "models": ["grok-test"], "default_model": "grok-test"},
        "api": {
            "ready": True,
            "models": [{"id": "grok-api"}],
            "default_model": "grok-api",
        },
    }

    async def fake_resolve(*args, **kwargs):
        return "cli", catalogs

    async def fake_system(*args, **kwargs):
        return "system"

    async def fake_build(prompt: str, **kwargs: object) -> dict:
        build_calls.append(prompt)
        return {"text": preamble, "model": "grok-test", "cost_usd": 0.0}

    async def fake_alternate(current: str, model, *, requires_api: bool) -> str:
        return "api"

    async def fake_api_chat(*args: object, **kwargs: object) -> dict:
        return {"text": "## Checklist\n- [ ] ship it", "model": "grok-api", "cost_usd": 0.01}

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    monkeypatch.setattr(server, "_alternate_plane", fake_alternate)
    monkeypatch.setattr(server.xai_api, "chat", fake_api_chat)
    result = await server._run_unified(
        "Update the checklist. No preamble, start with ## Checklist.",
        model=None,
        effort=None,
        plane="auto",
        fallback_policy="cross_plane",
        agentic=False,
        max_turns=1,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
    )
    # Two CLI attempts (initial + same-plane recovery), then the bounded API fallback.
    assert len(build_calls) == 2
    assert "previous response" in build_calls[1]
    assert result["text"].startswith("## Checklist")
    assert result["resolved_plane"] == "api"
    assert result["fallback_occurred"] is True
    assert result["fallback_reason"] == "cli_incomplete_response"


@pytest.mark.asyncio
async def test_cross_plane_fallback_preserves_rejected_metered_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogs = {
        "cli": {"ready": True, "models": ["grok-cli"], "default_model": "grok-cli"},
        "api": {
            "ready": True,
            "models": [{"id": "grok-api"}],
            "default_model": "grok-api",
        },
    }
    api_calls = 0

    async def fake_resolve(*args, **kwargs):
        return "api", catalogs

    async def fake_system(*args, **kwargs):
        return "system"

    async def fake_api_chat(*args: object, **kwargs: object) -> dict:
        nonlocal api_calls
        api_calls += 1
        return {
            "text": "I'll provide the result after I finish checking it.",
            "model": "grok-api",
            "cost_usd": 0.02,
            "usage": {
                "prompt_tokens": 3 if api_calls == 1 else 5,
                "completion_tokens": 4 if api_calls == 1 else 6,
                "total_tokens": 7 if api_calls == 1 else 11,
            },
        }

    async def fake_build(*args: object, **kwargs: object) -> dict:
        return {
            "text": "The completed answer is 42.",
            "model": "grok-cli",
            "cost_usd": 0.0,
            "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
        }

    async def fake_alternate(current: str, model, *, requires_api: bool) -> str:
        return "cli"

    monkeypatch.setattr(server, "METERED_API_ENABLED", True)
    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system)
    monkeypatch.setattr(server.xai_api, "chat", fake_api_chat)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    monkeypatch.setattr(server, "_alternate_plane", fake_alternate)

    result = await server._run_unified(
        "Give me the completed answer.",
        model=None,
        effort=None,
        plane="auto",
        fallback_policy="cross_plane",
        agentic=False,
        max_turns=1,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
    )

    assert api_calls == 2
    assert result["text"] == "The completed answer is 42."
    assert result["resolved_plane"] == "cli"
    assert result["fallback_reason"] == "api_incomplete_response"
    assert result["cost_usd"] == pytest.approx(0.04)
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 11
    assert result["total_tokens"] == 21
    assert [item["stage"] for item in result["incurred_attempts"]] == [
        "completion_initial",
        "completion_retry",
    ]
    assert [item["cost_usd"] for item in result["incurred_attempts"]] == [0.02, 0.02]


@pytest.mark.asyncio
async def test_bounded_votes_skip_nonanswer_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls: list[str] = []
    catalogs = {
        "cli": {"ready": True, "models": ["grok-test"], "default_model": "grok-test"},
        "api": {"ready": False, "models": []},
    }

    async def fake_resolve(*args, **kwargs):
        return "cli", catalogs

    async def fake_system(*args, **kwargs):
        return "system"

    async def fake_build(prompt: str, **kwargs: object) -> dict:
        build_calls.append(prompt)
        return {"text": "I'll tally the vote shortly.", "model": "grok-test", "cost_usd": 0.0}

    monkeypatch.setattr(server, "_resolve_plane", fake_resolve)
    monkeypatch.setattr(server, "_system_prompt", fake_system)
    monkeypatch.setattr(server.BUILD_ACP, "run", fake_build)
    result = await server._run_unified(
        "vote prompt",
        model=None,
        effort="low",
        plane="cli",
        fallback_policy="same_plane",
        agentic=False,
        max_turns=1,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
        nonanswer_recovery=False,
    )
    # Opted-out internal votes accept the reply as-is: no retry, no error.
    assert len(build_calls) == 1
    assert result["text"] == "I'll tally the vote shortly."


@pytest.mark.asyncio
async def test_chat_tool_requests_cross_plane_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_unified(prompt: str, **kwargs) -> dict:
        captured.update(prompt=prompt, **kwargs)
        return {"text": "done", "resolved_plane": "cli", "cost_usd": 0.0}

    monkeypatch.setattr(server, "_run_unified", fake_unified)
    result = await server.chat("What is the pinned revision convention?")
    assert result["text"] == "done"
    assert captured["fallback_policy"] == "cross_plane"
    assert captured.get("nonanswer_recovery", True) is True


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


def _deep_polish_turn_fixture(monkeypatch: pytest.MonkeyPatch, polish_behavior):
    """Drive _execute_team_turn in deep mode with a stubbed _run_unified.

    First call produces the main (messy but substantive) deep answer; the
    second call is the polish pass, whose behavior the test injects.
    """
    messy = "## Ranking note (internal)\n1. 60\n2. 59\n\nThe answer is 60."
    calls: list[str] = []
    catalogs = {
        "cli": {"ready": True, "models": ["grok-test"], "default_model": "grok-test"},
        "api": {"ready": False, "models": []},
    }

    async def fake_catalogs():
        return catalogs

    async def fake_run_unified(prompt: str, **kwargs: object) -> dict:
        calls.append(prompt)
        if len(calls) == 1:
            return {
                "text": messy,
                "model": "grok-test",
                "resolved_plane": "cli",
                "cost_usd": 0.0,
            }
        return await polish_behavior(prompt)

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_run_unified", fake_run_unified)
    return messy, calls


async def _run_deep_turn() -> dict:
    return await server._execute_team_turn(
        prompt="What is the maximum sum?",
        session=None,
        workspace_context="",
        workspace_label="",
        caller_instructions="",
        memory_scope=None,
        use_memory=False,
        model=None,
        effort=None,
        mode="reasoning",
        plane="auto",
        fallback_policy="cross_plane",
        turns=1,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
        depth="deep",
    )


@pytest.mark.asyncio
async def test_deep_polish_nonanswer_keeps_unpolished_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A polish pass that returns a bare-promise non-answer must not clobber
    the already-good deep answer (hosted @grok review of PR #500, blocking)."""

    async def polish_returns_nonanswer(prompt: str) -> dict:
        return {
            "text": "I'll tidy up the draft and share the polished version.",
            "model": "grok-test",
            "resolved_plane": "cli",
            "cost_usd": 0.0,
        }

    messy, calls = _deep_polish_turn_fixture(monkeypatch, polish_returns_nonanswer)
    result = await _run_deep_turn()
    assert len(calls) == 2
    assert result["text"] == messy
    assert result["final_polish"] == {
        "attempted": True,
        "applied": False,
        "plane": "cli",
        "cost_usd": 0.0,
    }


@pytest.mark.asyncio
async def test_deep_polish_exception_keeps_unpolished_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def polish_raises(prompt: str) -> dict:
        raise RuntimeError("polish plane fell over")

    messy, calls = _deep_polish_turn_fixture(monkeypatch, polish_raises)
    result = await _run_deep_turn()
    assert len(calls) == 2
    assert result["text"] == messy
    assert result["final_polish"] == {
        "attempted": True,
        "applied": False,
        "plane": None,
        "cost_usd": 0.0,
    }


@pytest.mark.asyncio
async def test_deep_polish_failure_preserves_reported_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def polish_raises_after_usage(prompt: str) -> dict:
        del prompt
        reported = {
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.015,
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            },
        }
        error = RuntimeError("polish response was unusable")
        raise server._with_incurred_usage(
            error,
            [
                server._usage_attempt(
                    reported,
                    stage="completion_initial",
                    outcome="rejected_nonanswer",
                )
            ],
        ) from error

    messy, calls = _deep_polish_turn_fixture(monkeypatch, polish_raises_after_usage)
    result = await _run_deep_turn()

    assert len(calls) == 2
    assert result["text"] == messy
    assert result["cost_usd"] == pytest.approx(0.015)
    assert result["total_tokens"] == 10
    assert result["incurred_attempts"][0]["stage"] == "completion_initial"
    assert result["final_polish"]["plane"] == "api"
    assert result["final_polish"]["incurred_cost_usd"] == pytest.approx(0.015)


@pytest.mark.asyncio
async def test_successful_deep_polish_keeps_stage_and_retry_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def polish_succeeds_after_retry(prompt: str) -> dict:
        del prompt
        return {
            "text": "The answer is 60.",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.025,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "incurred_attempts": [
                {
                    "stage": "completion_initial",
                    "outcome": "rejected_nonanswer",
                    "plane": "api",
                    "model": "grok-api",
                    "cost_usd": 0.01,
                    "total_tokens": 6,
                }
            ],
        }

    _messy, calls = _deep_polish_turn_fixture(monkeypatch, polish_succeeds_after_retry)
    result = await _run_deep_turn()

    assert len(calls) == 2
    assert result["text"] == "The answer is 60."
    assert result["cost_usd"] == pytest.approx(0.025)
    assert result["total_tokens"] == 15
    assert result["incurred_attempts"][0]["cost_usd"] == pytest.approx(0.01)
    assert result["final_polish"]["cost_usd"] == pytest.approx(0.025)
    assert result["final_polish"]["total_tokens"] == 15
    assert result["final_polish"]["incurred_attempts"][0]["stage"] == (
        "completion_initial"
    )
