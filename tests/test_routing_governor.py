from __future__ import annotations

from typing import Any

import pytest

from unigrok_public import server
from unigrok_public.mission import epoch
from unigrok_public.mission.epoch import seal_mission_epoch
from unigrok_public.mission.evidence import default_agent_policy
from unigrok_public.mission.governor import GovernorConfig, shadow_recommend
from unigrok_public.mission.lease import lease_expiry_iso
from unigrok_public.state import PublicStateStore

OWNER_LEASE = "routing-governor-owner"  # noqa: S105


async def _commit_fake_mission(
    store: PublicStateStore,
    job_id: str,
    result: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    mission_id = str(kwargs["mission_id"])
    lease_token = str(kwargs["mission_lease_token"])
    generation = int(kwargs["mission_lease_generation"])
    mission = await store.load_mission(mission_id)
    assert mission is not None
    if mission["status"] == "running":
        assert await store.cas_mission_status(
            mission_id,
            expect_status="running",
            expect_version=int(mission["checkpoint_version"]),
            expect_lease_generation=generation,
            expect_lease_token=lease_token,
            new_status="verifying",
        )
        mission = await store.load_mission(mission_id)
        assert mission is not None
    assert await store.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=int(mission["checkpoint_version"]),
        expect_lease_generation=generation,
        expect_lease_token=lease_token,
        new_status="complete",
        clear_lease=True,
    )
    return {
        **result,
        "status": "complete",
        "job_id": job_id,
        "mission": {"status": "complete", "committed": True},
        "autonomy": {"committed": True},
    }


@pytest.mark.asyncio
async def test_hive_route_receipts_use_actual_planes_and_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = [
        {
            "text": '{"route":"code","depth":"hive","voters":4}',
            "resolved_plane": "cli",
            "model": "grok-cli",
            "cost_usd": 0.0,
        },
        {
            "text": '{"route":"code","depth":"hive","voters":5}',
            "resolved_plane": "api",
            "model": "grok-api",
            "cost_usd": 0.012,
        },
        {
            "text": '{"route":"direct","depth":"deep","voters":3}',
            "resolved_plane": "api",
            "model": "grok-api",
            "cost_usd": 0.008,
        },
    ]
    calls = 0

    async def fake_run_unified(_prompt: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        reply = replies[calls]
        calls += 1
        return reply

    monkeypatch.setattr(server, "_run_unified", fake_run_unified)
    routing = await server._hive_route("Implement a durable queue")
    assert routing is not None
    assert routing["route"] == "code"
    assert routing["depth_hint"] == "hive"
    assert routing["router_plane"] == "mixed"
    assert routing["router_planes"] == ["api", "cli"]
    assert routing["router_models"] == ["grok-api", "grok-cli"]
    assert routing["router_cost_usd"] == pytest.approx(0.02)
    assert [vote["plane"] for vote in routing["router_votes"]] == [
        "cli",
        "api",
        "api",
    ]
    assert [vote["cost_usd"] for vote in routing["router_votes"]] == [
        0.0,
        0.012,
        0.008,
    ]


@pytest.mark.asyncio
async def test_semantic_router_parse_failure_keeps_metered_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogs = {
        "cli": {"ready": True, "models": ["grok-cli"], "default_model": "grok-cli"},
        "api": {
            "ready": True,
            "configured": True,
            "models": [{"id": "grok-api"}],
            "default_model": "grok-api",
        },
    }

    async def fake_guarded(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "text": "not valid router json",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.031,
        }

    monkeypatch.setattr(server, "_heuristic_route", lambda _prompt: None)
    monkeypatch.setattr(server, "_guarded_provider_call", fake_guarded)
    routing = await server._route_task("Ambiguous task", catalogs)
    assert routing["route"] == "direct"
    assert routing["router_parse_failed"] is True
    assert routing["router_plane"] == "api"
    assert routing["router_cost_usd"] == pytest.approx(0.031)


@pytest.mark.asyncio
async def test_code_route_hive_hint_keeps_specialist_draft_and_router_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogs = {
        "cli": {"ready": True, "models": ["grok-cli"], "default_model": "grok-cli"},
        "api": {"ready": True, "models": [{"id": "grok-build"}]},
    }
    captured: dict[str, Any] = {}

    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return catalogs

    async def fake_route(_prompt: str) -> dict[str, Any]:
        return {
            "route": "code",
            "depth_hint": "hive",
            "voters_hint": 2,
            "specialist_prompt": "Implement the queue with tests.",
            "router_model": "hive_route",
            "router_models": ["grok-cli", "grok-api"],
            "router_plane": "mixed",
            "router_planes": ["api", "cli"],
            "router_cost_usd": 0.03,
            "router_votes": [],
        }

    async def fake_hive(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "text": "final code",
            "model": "grok-cli",
            "resolved_plane": "cli",
            "cost_usd": 0.04,
            "hive": {
                "stages": {
                    "draft": {
                        "route": "code",
                        "model": "grok-build",
                        "plane": "api",
                    }
                }
            },
        }

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_heuristic_route", lambda _prompt: None)
    monkeypatch.setattr(server, "_hive_route", fake_route)
    monkeypatch.setattr(server, "_run_hive", fake_hive)
    result = await server._execute_team_turn(
        prompt="Implement a durable queue",
        session=None,
        workspace_context="",
        workspace_label="",
        caller_instructions="",
        memory_scope=None,
        use_memory=False,
        model=None,
        effort=None,
        mode="auto",
        plane="auto",
        fallback_policy="cross_plane",
        turns=6,
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
    )
    assert captured["draft_route"] == "code"
    assert captured["draft_prompt"] == "Implement the queue with tests."
    assert captured["num_voters"] == 2
    assert result["depth_engaged"] == "hive"
    assert result["cost_usd"] == pytest.approx(0.07)
    assert result["orchestration"]["router_plane"] == "mixed"
    assert result["orchestration"]["router_cost_usd"] == pytest.approx(0.03)
    assert result["orchestration"]["specialist_model"] == "grok-build"


@pytest.mark.asyncio
async def test_run_hive_uses_code_specialist_for_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogs = {
        "cli": {"ready": True, "models": ["grok-cli"], "default_model": "grok-cli"},
        "api": {"ready": True, "models": [{"id": "grok-build"}]},
    }
    specialist_calls: list[tuple[str, str]] = []

    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return catalogs

    async def fake_specialist(
        route: str, prompt: str, _catalogs: dict[str, Any]
    ) -> dict[str, Any]:
        specialist_calls.append((route, prompt))
        return {
            "text": "def enqueue(item):\n    return item",
            "model": "grok-build",
            "resolved_plane": "api",
            "cost_usd": 0.02,
        }

    async def fake_unified(prompt: str, **_kwargs: Any) -> dict[str, Any]:
        if "hive merge editor" in prompt:
            return {
                "text": "def enqueue(item):\n    return item",
                "model": "grok-cli",
                "resolved_plane": "cli",
                "cost_usd": 0.01,
            }
        return {
            "text": '{"v":"pass","c":2,"r":"none","f":"none","loc":"-"}',
            "model": "grok-cli",
            "resolved_plane": "cli",
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_run_specialist", fake_specialist)
    monkeypatch.setattr(server, "_run_unified", fake_unified)
    result = await server._run_hive(
        "Implement a queue",
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
        system_context=None,
        num_voters=1,
        draft_route="code",
        draft_prompt="Precise queue brief",
    )
    assert specialist_calls == [("code", "Precise queue brief")]
    assert result["cost_usd"] == pytest.approx(0.03)
    assert result["hive"]["draft_route"] == "code"
    assert result["hive"]["stages"]["draft"]["model"] == "grok-build"


@pytest.mark.asyncio
async def test_malformed_hive_vote_keeps_plane_and_cost_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return {
            "cli": {"ready": True, "models": ["grok-cli"]},
            "api": {"ready": False, "models": []},
        }

    calls = 0

    async def fake_unified(_prompt: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "text": "A complete draft with enough content for review.",
                "model": "grok-cli",
                "resolved_plane": "cli",
                "cost_usd": 0.0,
            }
        return {
            "text": "malformed vote",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.027,
        }

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_run_unified", fake_unified)
    result = await server._run_hive(
        "Review this draft",
        allow_web=False,
        allow_x_search=False,
        allow_code=False,
        system_context=None,
        num_voters=1,
    )
    assert result["cost_usd"] == pytest.approx(0.027)
    assert result["hive"]["vote_receipts"] == 1
    assert result["hive"]["votes_returned"] == 0
    assert result["hive"]["stages"]["votes"][0]["parsed"] is False


@pytest.mark.asyncio
async def test_hive_merge_failure_preserves_all_reported_stage_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return {
            "cli": {"ready": True, "models": ["grok-cli"]},
            "api": {"ready": False, "models": []},
        }

    calls = 0

    async def fake_unified(_prompt: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "text": "A complete draft.",
                "model": "grok-cli",
                "resolved_plane": "cli",
                "cost_usd": 0.01,
                "usage": {"totalTokens": 10},
            }
        if calls == 2:
            return {
                "text": '{"v":"pass","c":2,"r":"none","f":"none","loc":"-"}',
                "model": "grok-cli",
                "resolved_plane": "cli",
                "cost_usd": 0.02,
                "usage": {"totalTokens": 20},
            }
        merge_result = {
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.03,
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
        }
        error = RuntimeError("merge failed after provider response")
        raise server._with_incurred_usage(
            error,
            [
                server._usage_attempt(
                    merge_result,
                    stage="completion_initial",
                    outcome="rejected_nonanswer",
                )
            ],
        ) from error

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_run_unified", fake_unified)

    with pytest.raises(server._IncurredUsageError) as caught:
        await server._run_hive(
            "Review this draft",
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
            system_context=None,
            num_voters=1,
        )

    usage = server._exception_usage(caught.value)
    assert usage["cost_usd"] == pytest.approx(0.06)
    assert usage["total_tokens"] == 40
    assert [item["stage"] for item in usage["incurred_attempts"]] == [
        "hive_draft",
        "hive_vote",
        "completion_initial",
    ]


@pytest.mark.asyncio
async def test_main_work_failure_preserves_prior_router_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return {
            "cli": {"ready": True, "models": ["grok-cli"]},
            "api": {"ready": True, "models": [{"id": "grok-api"}]},
        }

    async def fake_hive_route(_prompt: str) -> dict[str, Any]:
        return {
            "route": "direct",
            "specialist_prompt": "routed prompt",
            "router_model": "hive_route",
            "router_plane": "api",
            "router_cost_usd": 0.03,
            "router_votes": [
                {"plane": "api", "cost_usd": 0.03, "total_tokens": 6}
            ],
        }

    async def no_specialist(*_args: object, **_kwargs: object) -> None:
        return None

    async def main_fails(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise RuntimeError("main provider unavailable")

    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_heuristic_route", lambda _prompt: None)
    monkeypatch.setattr(server, "_hive_route", fake_hive_route)
    monkeypatch.setattr(server, "_run_specialist", no_specialist)
    monkeypatch.setattr(server, "_run_unified", main_fails)

    with pytest.raises(server._IncurredUsageError) as caught:
        await server._execute_team_turn(
            prompt="Implement an unusual subsystem",
            session=None,
            workspace_context="",
            workspace_label="",
            caller_instructions="",
            memory_scope=None,
            use_memory=False,
            model=None,
            effort=None,
            mode="auto",
            plane="auto",
            fallback_policy="cross_plane",
            turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
        )

    usage = server._exception_usage(caught.value)
    assert usage["cost_usd"] == pytest.approx(0.03)
    assert usage["total_tokens"] == 6
    assert usage["incurred_attempts"][0]["stage"] == "router_vote"


@pytest.mark.asyncio
async def test_state_failure_after_provider_result_preserves_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_catalogs(*, refresh: bool = False) -> dict[str, Any]:
        del refresh
        return {
            "cli": {"ready": True, "models": ["grok-cli"]},
            "api": {"ready": False, "models": []},
        }

    async def no_specialist(*_args: object, **_kwargs: object) -> None:
        return None

    async def main_succeeds(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "text": "Completed answer.",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.05,
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }

    class FailingState:
        async def search_facts(self, *_args: object, **_kwargs: object) -> list[dict]:
            return [{"id": 1, "scope": "global", "fact": "known fact"}]

        async def touch_facts(self, _fact_ids: list[int]) -> None:
            raise RuntimeError("state write failed")

    monkeypatch.setattr(server, "STATE", FailingState())
    monkeypatch.setattr(server, "_catalogs", fake_catalogs)
    monkeypatch.setattr(server, "_heuristic_route", lambda _prompt: "direct")
    monkeypatch.setattr(server, "_run_specialist", no_specialist)
    monkeypatch.setattr(server, "_run_unified", main_succeeds)

    with pytest.raises(server._IncurredUsageError) as caught:
        await server._execute_team_turn(
            prompt="Answer with memory",
            session=None,
            workspace_context="",
            workspace_label="",
            caller_instructions="",
            memory_scope=None,
            use_memory=True,
            model=None,
            effort=None,
            mode="auto",
            plane="auto",
            fallback_policy="cross_plane",
            turns=1,
            allow_web=False,
            allow_x_search=False,
            allow_code=False,
        )

    usage = server._exception_usage(caught.value)
    assert usage["cost_usd"] == pytest.approx(0.05)
    assert usage["total_tokens"] == 6
    assert usage["incurred_attempts"][0]["stage"] == "agent_work"


def test_frozen_governor_round_trip_is_independent_of_defaults() -> None:
    original = shadow_recommend(
        uncertainty=0.95,
        impact=0.9,
        risk=0.95,
        signals=("concurrency", "security"),
    )
    frozen = GovernorConfig.from_dict(original.to_dict())
    assert frozen == original
    assert GovernorConfig.from_dict({"reasoning_level": "future-level"}) is None


@pytest.mark.asyncio
async def test_mission_resume_executes_frozen_governor_without_recommendation(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    store = PublicStateStore(tmp_path / "frozen-governor.db")
    await store.initialize()
    token = "a" * 32
    job_id = "b" * 32
    mission_id = f"msn_{job_id}"
    frozen = shadow_recommend(
        uncertainty=0.0,
        impact=0.0,
        risk=0.0,
        irreversibility=0.0,
        novelty=0.0,
    )
    await store.create_mission(
        mission_id,
        job_id=job_id,
        acceptance_hash="acceptance-hash",
        acceptance_text="Review the concurrency security race",
        continue_token=token,
        package={
            "task": "Review the concurrency security race",
            "acceptance": "Review the concurrency security race",
            "governor_config": frozen.to_dict(),
            "level_ceiling": "ultra",
            "request": {
                "task": "Review the concurrency security race",
                "acceptance": "Review the concurrency security race",
                "depth": "auto",
                "level": None,
                "voters": None,
            },
        },
        lease_token=OWNER_LEASE,
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=180),
    )
    assert await store.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    assert await store.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="waiting_event",
        clear_lease=True,
    )
    captured: dict[str, Any] = {}

    async def fake_turn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"text": "review", "cost_usd": 0.0, "orchestration": {}}

    async def fake_seal(
        _job_id: str, *, result: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return await _commit_fake_mission(store, _job_id, result, **kwargs)

    def forbid_recommendation(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("resume must not recompute a frozen governor")

    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", True)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", True)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})
    monkeypatch.setattr(server, "_execute_team_turn", fake_turn)
    monkeypatch.setattr(server, "_seal_autonomy_done", fake_seal)
    monkeypatch.setattr(
        "unigrok_public.mission.governor.recommend_for_task", forbid_recommendation
    )
    result = await server.agent(continue_token=token)
    assert captured["effort"] == frozen.reasoning_level
    assert captured["depth"] == "auto"
    assert captured["num_voters"] == len(frozen.voter_roles)
    assert captured["turns"] == frozen.tool_budget
    assert result["governor_execution"]["source"] == "frozen_mission"
    assert result["governor_execution"]["config"] == frozen.to_dict()
    assert result["governor_execution"]["applied_max_turns"] == frozen.tool_budget


@pytest.mark.asyncio
async def test_explicit_level_and_voters_override_mission_governor(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    store = PublicStateStore(tmp_path / "governor-overrides.db")
    await store.initialize()
    captured: dict[str, Any] = {}

    async def fake_turn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"text": "review", "cost_usd": 0.0, "orchestration": {}}

    async def fake_seal(
        _job_id: str, *, result: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return await _commit_fake_mission(store, _job_id, result, **kwargs)

    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", True)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", True)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})
    monkeypatch.setattr(server, "_execute_team_turn", fake_turn)
    monkeypatch.setattr(server, "_seal_autonomy_done", fake_seal)
    result = await server.agent(
        task="Adversarial security review of a concurrent lease race",
        depth="hive",
        level="low",
        voters=1,
    )
    # The explicit ladder rung has always owned shape as well as effort; a separate
    # explicit voter count remains authoritative over both ladder and governor.
    assert captured["effort"] == "low"
    assert captured["depth"] == "direct"
    assert captured["num_voters"] == 1
    receipt = result["governor_execution"]
    assert receipt["caller_overrides"] == {
        "level": "low",
        "depth": "hive",
        "voters": 1,
    }
    assert receipt["applied_effort"] == "low"
    assert receipt["applied_depth"] == "direct"
    mission = await store.load_mission_by_job(result["job_id"])
    assert mission is not None
    assert mission["package"]["governor_config"] == receipt["config"]


@pytest.mark.asyncio
async def test_epoch_uses_frozen_governor_on_resume(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    store = PublicStateStore(tmp_path / "epoch-governor.db")
    await store.initialize()
    token = "c" * 32
    frozen = shadow_recommend(
        uncertainty=0.0,
        impact=0.0,
        risk=0.0,
        irreversibility=0.0,
        novelty=0.0,
    )
    await store.create_mission(
        "msn_epoch_governor",
        job_id="job_epoch_governor",
        acceptance_hash="acceptance-hash",
        acceptance_text="Reply with exactly OK",
        continue_token=token,
        package={
            "task": "Reply with exactly OK",
            "acceptance": "Reply with exactly OK",
            "task_class": "literal",
            "verification_mode": "structural",
            "evidence_policy": default_agent_policy().to_dict(),
            "governor_config": frozen.to_dict(),
        },
        lease_token=OWNER_LEASE,
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=180),
    )

    def forbid_recommendation(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("epoch must not recompute a frozen governor")

    monkeypatch.setattr(epoch, "recommend_for_task", forbid_recommendation)
    result = await seal_mission_epoch(
        store,
        mission_id="msn_epoch_governor",
        job_id="job_epoch_governor",
        acceptance_text="Reply with exactly OK",
        result={"text": "OK", "orchestration": {}, "cost_usd": 0.0},
        lease_generation=1,
        lease_token=OWNER_LEASE,
        continue_token=token,
        shadow_cognition=False,
    )
    assert result["status"] == "complete", result.get("mission")
    assert result["mission"]["governor_source"] == "frozen_mission"
    assert result["mission"]["governor_shadow"] == frozen.to_dict()
