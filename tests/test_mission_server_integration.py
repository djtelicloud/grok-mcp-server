from __future__ import annotations

import asyncio

import pytest

from unigrok_public import server
from unigrok_public.mission.artifacts import sealed_content_hash
from unigrok_public.mission.epoch import seal_mission_epoch
from unigrok_public.mission.evidence import default_agent_policy
from unigrok_public.mission.lease import lease_expiry_iso
from unigrok_public.state import PublicStateStore

OWNER_LEASE = "owner-token"  # noqa: S105
OTHER_LEASE = "other-worker"  # noqa: S105
INTRUDER_LEASE = "intruder-token"  # noqa: S105


@pytest.fixture
async def mission_server(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> PublicStateStore:
    store = PublicStateStore(tmp_path / "mission-server.db")
    await store.initialize()
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", True)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", True)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})
    monkeypatch.setattr(server, "_JOB_ENRICHMENT", {})
    return store


async def _create_mission(
    store: PublicStateStore,
    *,
    job_id: str,
    token: str,
    lease_token: str = OWNER_LEASE,
    task: str = "Reply with exactly OK",
    request: dict[str, object] | None = None,
) -> str:
    mission_id = f"msn_{job_id}"
    await store.create_mission(
        mission_id,
        job_id=job_id,
        acceptance_hash="acceptance-hash",
        acceptance_text=task,
        continue_token=token,
        package={
            "task": task,
            "acceptance": task,
            "task_class": "literal" if "exactly" in task else "substantial",
            "evidence_policy": default_agent_policy().to_dict(),
            "request": request or {"task": task, "acceptance": task},
        },
        lease_token=lease_token,
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=180),
    )
    return mission_id


@pytest.mark.asyncio
async def test_invalid_resume_preflight_does_not_claim_mission(
    mission_server: PublicStateStore,
) -> None:
    job_id = "3" * 32
    token = "4" * 32
    mission_id = await _create_mission(
        mission_server,
        job_id=job_id,
        token=token,
        request={
            "task": "Reply with exactly OK",
            "acceptance": "Reply with exactly OK",
            "session": "invalid session name",
        },
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="waiting_event",
        clear_lease=True,
    )
    before = await mission_server.load_mission(mission_id)
    assert before is not None

    with pytest.raises(ValueError, match="session must be"):
        await server.agent(continue_token=token)

    after = await mission_server.load_mission(mission_id)
    assert after is not None
    assert after["status"] == "waiting_event"
    assert after["lease_token"] is None
    assert after["lease_generation"] == before["lease_generation"]


@pytest.mark.asyncio
async def test_provider_error_releases_mission_and_persists_retryable_truth(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_turn(**_kwargs: object) -> dict:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(server, "_execute_team_turn", failing_turn)
    result = await server.agent(task="Reply with exactly OK")

    assert result["status"] == "continue"
    assert result["stop_reason"] == "error"
    assert result["mission"]["status"] == "waiting_event"
    mission = await mission_server.load_mission_by_job(result["job_id"])
    assert mission is not None
    assert mission["status"] == "waiting_event"
    assert mission["lease_token"] is None
    stored = await mission_server.load_agent_job(result["job_id"])
    assert stored is not None
    assert stored["status"] == "needs_continuation"
    assert stored["payload"]["mission"]["status"] == "waiting_event"


@pytest.mark.asyncio
async def test_provider_error_billing_survives_restart_and_later_commit(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def billed_failure(**_kwargs: object) -> dict:
        reported = {
            "resolved_plane": "api",
            "cost_usd": 0.02,
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            },
        }
        error = RuntimeError("provider response could not be accepted")
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

    monkeypatch.setattr(server, "_execute_team_turn", billed_failure)
    first = await server.agent(task="Reply with exactly OK")

    assert first["status"] == "continue"
    assert first["cost_usd"] == pytest.approx(0.02)
    assert first["total_tokens"] == 10
    assert first["mission_billing"]["quanta"] == 1

    server._DURABLE_JOBS.clear()
    recovered = await server.agent_result(first["job_id"])
    assert recovered["status"] == "continue"
    assert recovered["cost_usd"] == pytest.approx(0.02)
    assert recovered["total_tokens"] == 10
    assert recovered["incurred_attempts"][0]["stage"] == "completion_initial"

    async def successful_turn(**_kwargs: object) -> dict:
        return {
            "text": "OK",
            "model": "grok-api",
            "resolved_plane": "api",
            "cost_usd": 0.01,
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 1,
                "total_tokens": 5,
            },
            "orchestration": {"route": "direct"},
        }

    monkeypatch.setattr(server, "_execute_team_turn", successful_turn)
    completed = await server.agent(continue_token=first["continue_token"])

    assert completed["status"] == "complete"
    assert completed["text"] == "OK"
    assert completed["cost_usd"] == pytest.approx(0.03)
    assert completed["total_tokens"] == 15
    assert completed["mission_billing"]["quanta"] == 2


@pytest.mark.asyncio
async def test_stale_post_seal_mirror_cannot_overwrite_newer_winner(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def draft_turn(**_kwargs: object) -> dict:
        return {"text": "older draft", "cost_usd": 0.0, "orchestration": {}}

    async def waiting_seal(
        job_id: str,
        *,
        result: dict[str, object],
        mission_id: str,
        mission_lease_token: str,
        mission_lease_generation: int,
        **_kwargs: object,
    ) -> dict[str, object]:
        mission = await mission_server.load_mission(mission_id)
        assert mission is not None
        assert await mission_server.cas_mission_status(
            mission_id,
            expect_status="running",
            expect_version=int(mission["checkpoint_version"]),
            expect_lease_generation=mission_lease_generation,
            expect_lease_token=mission_lease_token,
            new_status="verifying",
        )
        mission = await mission_server.load_mission(mission_id)
        assert mission is not None
        assert await mission_server.cas_mission_status(
            mission_id,
            expect_status="verifying",
            expect_version=int(mission["checkpoint_version"]),
            expect_lease_generation=mission_lease_generation,
            expect_lease_token=mission_lease_token,
            new_status="waiting_event",
            checkpoint_update={"last_verify": {"gaps": ["older_gap"]}},
            clear_lease=True,
        )
        return {
            **result,
            "status": "continue",
            "job_id": job_id,
            "text": "retry older draft",
            "mission": {
                "protocol": "unigrok_mission_v2",
                "status": "waiting_event",
                "committed": False,
                "gaps": ["older_gap"],
            },
            "autonomy": {"committed": False, "gaps": ["older_gap"]},
        }

    original_mirror = mission_server.mirror_mission_result

    async def racing_mirror(
        mission_id: str, **kwargs: object
    ) -> bool:
        claimed, generation = await mission_server.claim_mission(
            mission_id,
            lease_token=OTHER_LEASE,
            ttl_seconds=180,
        )
        assert claimed
        mission = await mission_server.load_mission(mission_id)
        assert mission is not None
        assert await mission_server.cas_mission_status(
            mission_id,
            expect_status="running",
            expect_version=int(mission["checkpoint_version"]),
            expect_lease_generation=generation,
            expect_lease_token=OTHER_LEASE,
            new_status="verifying",
        )
        mission = await mission_server.load_mission(mission_id)
        assert mission is not None
        assert await mission_server.cas_mission_status(
            mission_id,
            expect_status="verifying",
            expect_version=int(mission["checkpoint_version"]),
            expect_lease_generation=generation,
            expect_lease_token=OTHER_LEASE,
            new_status="complete",
            checkpoint_update={
                "candidate_hash": sealed_content_hash("new winner", kind="candidate")
            },
            clear_lease=True,
        )
        winner = {
            "status": "complete",
            "job_id": str(mission["job_id"]),
            "acceptance_hash": str(mission["acceptance_hash"]),
            "text": "new winner",
            "mission": {"status": "complete", "committed": True},
            "autonomy": {"committed": True},
        }
        await mission_server.save_agent_job(str(mission["job_id"]), "complete", winner)
        return await original_mirror(mission_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(server, "_execute_team_turn", draft_turn)
    monkeypatch.setattr(server, "_seal_autonomy_done", waiting_seal)
    monkeypatch.setattr(mission_server, "mirror_mission_result", racing_mirror)
    result = await server.agent(task="Explain a safe release plan")

    assert result["status"] == "complete"
    assert result["text"] == "new winner"
    stored = await mission_server.load_agent_job(result["job_id"])
    assert stored is not None
    assert stored["status"] == "complete"
    assert stored["payload"]["text"] == "new winner"


@pytest.mark.asyncio
async def test_terminal_reattach_returns_winner_without_model_call(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "a" * 32
    token = "b" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    winner = {
        "status": "complete",
        "job_id": job_id,
        "acceptance_hash": "acceptance-hash",
        "text": "OK",
        "mission": {"status": "complete", "committed": True},
        "autonomy": {"committed": True},
        "session_turn_persisted": True,
    }
    await mission_server.save_agent_job(job_id, "complete", winner)
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="complete",
        checkpoint_update={"candidate_hash": sealed_content_hash("OK", kind="candidate")},
        clear_lease=True,
    )

    calls = 0

    async def forbidden_turn(**_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("terminal reattach must not run a model")

    monkeypatch.setattr(server, "_execute_team_turn", forbidden_turn)
    result = await server.agent(continue_token=token)
    assert result["text"] == "OK"
    assert result["mission"]["committed"] is True
    assert calls == 0


@pytest.mark.asyncio
async def test_terminal_reattach_repairs_crash_before_session_commit(
    mission_server: PublicStateStore,
) -> None:
    job_id = "5" * 32
    token = "0" * 32
    task = "Reply with exactly OK"
    mission_id = await _create_mission(
        mission_server,
        job_id=job_id,
        token=token,
        task=task,
        request={
            "task": task,
            "acceptance": task,
            "session": "recover-session",
            "use_memory": False,
        },
    )
    projection_kind = f"candidate_projection:{mission_id}"
    projection_hash = sealed_content_hash("OK", kind=projection_kind)
    assert await mission_server.put_mission_artifact(
        mission_id,
        projection_hash,
        kind=projection_kind,
        sealed="OK",
        projection="OK",
        lease_token=OWNER_LEASE,
        lease_generation=1,
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    winner = {
        "status": "complete",
        "job_id": job_id,
        "acceptance_hash": "acceptance-hash",
        "text": "OK",
        "mission": {"status": "complete", "committed": True},
        "autonomy": {"committed": True},
    }
    await mission_server.save_agent_job(job_id, "complete", winner)
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="complete",
        checkpoint_update={
            "candidate_hash": sealed_content_hash("OK", kind="candidate"),
            "candidate_projection_hash": projection_hash,
        },
        clear_lease=True,
    )
    assert await mission_server.load_messages("recover-session") == []

    first = await server.agent(continue_token=token)
    second = await server.agent(continue_token=token)

    assert first["status"] == "complete"
    assert first["session_turn_persisted"] is True
    assert second["session_turn_persisted"] is True
    messages = await mission_server.load_messages("recover-session")
    assert [message["content"] for message in messages] == [task, "OK"]


@pytest.mark.asyncio
async def test_terminal_reattach_ignores_stale_running_job_projection(
    mission_server: PublicStateStore,
) -> None:
    job_id = "9" * 32
    token = "8" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    projection_kind = f"candidate_projection:{mission_id}"
    projection_hash = sealed_content_hash("OK", kind=projection_kind)
    assert await mission_server.put_mission_artifact(
        mission_id,
        projection_hash,
        kind=projection_kind,
        sealed="OK",
        projection="OK",
        lease_token=OWNER_LEASE,
        lease_generation=1,
    )
    await mission_server.save_agent_job(job_id, "running", {"status": "pending"})
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="complete",
        checkpoint_update={
            "candidate_hash": sealed_content_hash("OK", kind="candidate"),
            "candidate_projection_hash": projection_hash,
        },
        clear_lease=True,
    )

    result = await server.agent(continue_token=token)
    assert result["status"] == "complete"
    assert result["text"] == "OK"
    assert result["artifact_refs"] == [projection_hash]


@pytest.mark.asyncio
async def test_failed_mission_claim_aborts_before_model(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "c" * 32
    token = "d" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    # Move to a reattachable state, then let another worker own the new generation.
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="waiting_event",
        clear_lease=True,
    )
    claimed, _ = await mission_server.claim_mission(
        mission_id, lease_token=OTHER_LEASE, ttl_seconds=180
    )
    assert claimed is True

    calls = 0

    async def forbidden_turn(**_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("failed claim must not run a model")

    monkeypatch.setattr(server, "_execute_team_turn", forbidden_turn)
    result = await server.agent(continue_token=token)
    assert result["status"] == "continue"
    assert result["mission"]["claim_blocked"] is True
    assert calls == 0


@pytest.mark.asyncio
async def test_stale_token_cannot_freeze_or_commit_candidate(
    mission_server: PublicStateStore,
) -> None:
    job_id = "e" * 32
    token = "f" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    candidate = "OK"
    digest = sealed_content_hash(candidate, kind="candidate")
    result = await seal_mission_epoch(
        mission_server,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={"text": candidate},
        lease_generation=1,
        lease_token=INTRUDER_LEASE,
        continue_token=token,
        shadow_cognition=False,
    )
    assert result["status"] == "continue"
    assert result["mission"]["lease_lost"] is True
    row = await mission_server.load_mission(mission_id)
    assert row is not None and row["status"] == "running"
    assert await mission_server.get_mission_artifact(digest) is None


@pytest.mark.asyncio
async def test_losing_faildone_cas_cannot_overwrite_complete_winner(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "7" * 32
    token = "6" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    first = await seal_mission_epoch(
        mission_server,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={"text": "NO"},
        lease_generation=1,
        lease_token=OWNER_LEASE,
        continue_token=token,
        shadow_cognition=False,
    )
    assert first["status"] == "continue"
    claimed, generation = await mission_server.claim_mission(
        mission_id,
        lease_token=OTHER_LEASE,
        ttl_seconds=180,
    )
    assert claimed is True

    winner_kind = f"candidate_projection:{mission_id}:winner"
    winner_hash = sealed_content_hash("OK", kind=winner_kind)
    assert await mission_server.put_mission_artifact(
        mission_id,
        winner_hash,
        kind=winner_kind,
        sealed="OK",
        projection="OK",
        lease_token=OTHER_LEASE,
        lease_generation=generation,
    )
    winner = {
        "status": "complete",
        "job_id": job_id,
        "acceptance_hash": "acceptance-hash",
        "text": "OK",
        "mission": {"status": "complete", "committed": True},
    }
    original_cas = mission_server.cas_mission_status

    async def racing_cas(
        target: str,
        **kwargs: object,
    ) -> bool:
        if kwargs.get("new_status") != "failed":
            return await original_cas(target, **kwargs)  # type: ignore[arg-type]
        committed = await original_cas(
            target,
            expect_status=str(kwargs["expect_status"]),
            expect_version=int(kwargs["expect_version"]),
            expect_lease_generation=int(kwargs["expect_lease_generation"]),
            expect_lease_token=str(kwargs["expect_lease_token"]),
            new_status="complete",
            checkpoint_update={
                "candidate_hash": sealed_content_hash("OK", kind="candidate"),
                "candidate_projection_hash": winner_hash,
            },
            clear_lease=True,
        )
        assert committed is True
        await mission_server.save_agent_job(job_id, "complete", winner)
        return False

    monkeypatch.setattr(mission_server, "cas_mission_status", racing_cas)
    result = await seal_mission_epoch(
        mission_server,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={"text": "NO"},
        lease_generation=generation,
        lease_token=OTHER_LEASE,
        continue_token=token,
        shadow_cognition=False,
    )
    assert result["status"] == "complete"
    assert result["text"] == "OK"
    stored = await mission_server.load_agent_job(job_id)
    assert stored is not None and stored["status"] == "complete"
    mission = await mission_server.load_mission(mission_id)
    assert mission is not None and mission["status"] == "complete"


@pytest.mark.asyncio
async def test_restart_poll_reports_recoverable_mission_not_lost(
    mission_server: PublicStateStore,
) -> None:
    job_id = "1" * 32
    token = "2" * 32
    mission_id = await _create_mission(mission_server, job_id=job_id, token=token)
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="verifying",
    )
    assert await mission_server.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=OWNER_LEASE,
        new_status="waiting_event",
        clear_lease=True,
    )
    await mission_server.save_agent_job(job_id, "running")
    result = await server.agent_result(job_id)
    assert result["status"] == "continue"
    assert result["mission"]["recoverable"] is True
    assert result["continue_token"] == token


@pytest.mark.asyncio
async def test_rejected_candidate_never_enters_session_memory(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rejected_turn(**_kwargs: object) -> dict:
        return {
            "text": "The authentication race is fixed and every test passed.",
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", rejected_turn)
    result = await server.agent(
        task="Fix the authentication race and prove it with runtime test evidence.",
        session="commit-gated-session",
    )
    assert result["status"] == "continue"
    assert result["mission"]["committed"] is False
    assert await mission_server.load_messages("commit-gated-session") == []


@pytest.mark.asyncio
async def test_literal_commit_persists_once_across_terminal_reattach(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def literal_turn(**_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        return {
            "text": "OK",
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", literal_turn)
    first = await server.agent(
        task="Reply with exactly OK",
        acceptance="Reply with exactly OK",
        session="literal-once",
    )
    assert first["status"] == "complete"
    assert first["mission"]["committed"] is True
    assert len(await mission_server.load_messages("literal-once")) == 2

    second = await server.agent(continue_token=first["continue_token"])
    assert second["text"] == "OK"
    assert len(await mission_server.load_messages("literal-once")) == 2
    assert calls == 1


@pytest.mark.asyncio
async def test_session_commit_occurs_only_after_terminal_mission_cas(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def literal_turn(**_kwargs: object) -> dict:
        return {
            "text": "OK",
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    original_append = mission_server.append_turn_once
    observed_statuses: list[str] = []

    async def ordered_append(*args: object, **kwargs: object) -> tuple[int, bool]:
        mission = await mission_server.load_mission_by_job(str(kwargs["commit_key"]))
        assert mission is not None
        observed_statuses.append(str(mission["status"]))
        assert mission["status"] == "complete"
        assert mission["lease_token"] is None
        return await original_append(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(server, "_execute_team_turn", literal_turn)
    monkeypatch.setattr(mission_server, "append_turn_once", ordered_append)
    result = await server.agent(
        task="Reply with exactly OK",
        acceptance="Reply with exactly OK",
        session="terminal-before-session",
    )

    assert result["status"] == "complete"
    assert observed_statuses == ["complete"]


@pytest.mark.asyncio
async def test_live_quantum_runs_mission_heartbeat_until_commit(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    captured: dict[str, object] = {}

    async def fake_heartbeat(
        mission_id: str,
        lease_token: str,
        lease_generation: int,
        *,
        ttl_seconds: int,
        stop: asyncio.Event,
    ) -> None:
        captured.update(
            {
                "mission_id": mission_id,
                "lease_token": lease_token,
                "lease_generation": lease_generation,
                "ttl_seconds": ttl_seconds,
            }
        )
        started.set()
        await stop.wait()

    async def literal_turn(**_kwargs: object) -> dict:
        await started.wait()
        return {
            "text": "OK",
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_heartbeat_owned_mission", fake_heartbeat)
    monkeypatch.setattr(server, "_execute_team_turn", literal_turn)
    result = await server.agent(task="Reply with exactly OK")
    assert result["status"] == "complete"
    assert captured["mission_id"] == f"msn_{result['job_id']}"
    assert captured["lease_generation"] == 1
    assert captured["ttl_seconds"] == server.MISSION_LEASE_TTL_SECONDS


@pytest.mark.asyncio
async def test_ordinary_substantive_agent_commits_from_structural_verification(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explanatory_turn(**_kwargs: object) -> dict:
        return {
            "text": (
                "The staged rollout uses monitored health checks, a bounded canary, "
                "and a rollback window before broad deployment."
            ),
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", explanatory_turn)
    result = await server.agent(
        task="Explain a staged rollout and rollback plan.",
        acceptance=(
            "Explain staged deployment, monitored health checks, a bounded canary, "
            "and the rollback window."
        ),
    )

    assert result["status"] == "complete"
    assert result["mission"]["committed"] is True
    assert result["mission"]["check"]["verification_mode"] == "structural"
    mission = await mission_server.load_mission_by_job(result["job_id"])
    assert mission is not None
    assert mission["package"]["verification_mode"] == "structural"


@pytest.mark.asyncio
async def test_typed_caller_evidence_unblocks_outcome_sensitive_mission(
    mission_server: PublicStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def outcome_turn(**_kwargs: object) -> dict:
        return {
            "text": (
                "The authentication race is covered by the concurrency test suite, "
                "which completed successfully under the requested runtime check."
            ),
            "plane": "test",
            "cost_usd": 0.0,
            "orchestration": {},
        }

    monkeypatch.setattr(server, "_execute_team_turn", outcome_turn)
    result = await server.agent(
        task="Run the concurrency tests and prove the authentication race is fixed.",
        acceptance=(
            "Confirm the authentication race is covered by concurrency tests and the "
            "runtime check passed."
        ),
        caller_evidence=[
            server.CallerEvidenceInput(
                reference="test-run:concurrency-42",
                observation="Independent CI run completed with all concurrency cases green.",
            )
        ],
    )

    assert result["status"] == "complete"
    assert result["mission"]["committed"] is True
    assert result["mission"]["check"]["verification_mode"] == "independent_evidence"
    mission = await mission_server.load_mission_by_job(result["job_id"])
    assert mission is not None
    assert mission["package"]["verification_mode"] == "independent_evidence"
    evidence = await mission_server.list_mission_evidence(mission["mission_id"])
    caller = [item for item in evidence if item["class"] == "caller_evidence"]
    assert len(caller) == 1
    assert caller[0]["payload"]["source"] == "caller"
    assert "candidate_hash" not in caller[0]["payload"]
