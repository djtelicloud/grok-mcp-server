"""One bounded mission epoch: freeze → verifying → CommitDone or continue."""

from __future__ import annotations

import contextlib
from typing import Any

from unigrok_public.autonomy import continue_envelope

from .artifacts import artifact_projection, sealed_content_hash
from .evidence import default_agent_policy
from .governor import recommend_for_task
from .types import MissionStatus
from .verify import VerifyInput, should_terminal_fail, verify_commit
from .voters import ShadowVoterExecutor, build_slots, merge_scorecards


async def seal_mission_epoch(
    store: Any,
    *,
    mission_id: str,
    job_id: str,
    acceptance_text: str,
    result: dict[str, Any],
    lease_generation: int,
    continue_token: str,
    envelope_version: int = 1,
    shadow_cognition: bool = True,
) -> dict[str, Any]:
    """Run verifying CommitDone for a finished agent quantum.

    Candidate text is frozen as a sealed artifact. Evidence is verifier-authored
    structural records (and any prior typed evidence) — never the answer body.
    """
    mission = await store.load_mission(mission_id)
    if mission is None:
        out = dict(result)
        out.setdefault("job_id", job_id)
        return out

    if str(mission.get("status")) in {
        MissionStatus.COMPLETE.value,
        MissionStatus.FAILED.value,
        MissionStatus.CANCELLED.value,
        MissionStatus.BUDGET_EXHAUSTED.value,
    }:
        out = dict(result)
        out["status"] = "complete" if mission["status"] == MissionStatus.COMPLETE.value else "error"
        out["job_id"] = job_id
        out["mission"] = {"status": mission["status"], "terminal": True}
        return out

    candidate = str(result.get("text") or "")
    digest = sealed_content_hash(candidate, kind="candidate")
    projection = artifact_projection(candidate)

    # Epoch-wide cognition shadow (does not select action when shadow=True).
    # Classify the frozen task/acceptance — never hardcode low beliefs.
    package = mission.get("package") or {}
    task_text = str(package.get("task") or acceptance_text or "")
    gov = recommend_for_task(
        task_text,
        acceptance=str(acceptance_text or package.get("acceptance") or ""),
        prior_verify_failures=int(mission.get("verify_failures") or 0),
        level_ceiling=str(package.get("level_ceiling") or "ultra"),
        destructive=bool(package.get("destructive")),
    )
    cards = []
    if shadow_cognition:
        executor = ShadowVoterExecutor()
        for slot in build_slots(gov.voter_roles):
            cards.append(
                await executor.invoke(slot, task=acceptance_text, draft=candidate)
            )
    merge = merge_scorecards(cards) if cards else {"action": "propose_done", "shadow": True}

    # Persist projection only in SQLite; digest commits the in-memory sealed bytes.
    await store.put_mission_artifact(
        mission_id,
        digest,
        kind="candidate",
        sealed=projection,
        projection=projection,
    )
    await store.append_mission_event(
        mission_id,
        "CandidateFrozen",
        {"hash": digest, "bytes": len(candidate.encode("utf-8")), "governor": gov.to_dict()},
    )
    if cards:
        await store.append_mission_event(
            mission_id,
            "CouncilShadow",
            {"merge": merge, "votes": [c.to_dict() for c in cards]},
        )

    version = int(mission.get("checkpoint_version") or 0)
    claimed_gen = int(mission.get("lease_generation") or 0)
    if int(lease_generation) != claimed_gen:
        # Stale worker — do not advance truth.
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(mission.get("ledger_cursor") or 0),
            acceptance_hash_value=str(mission.get("acceptance_hash") or ""),
            gaps=["stale_lease_generation"],
            artifact_refs=[digest],
            text="Mission lease lost before verifying; re-invoke continue_token.",
            poll=False,
        )
        sealed["proposed_text"] = projection
        sealed["mission"] = {"status": mission.get("status"), "lease_lost": True}
        return sealed

    # Bind envelope: clamp only (never raise). Record version.
    await store.touch_mission_envelope(mission_id, envelope_version=int(envelope_version))

    ok_cas = await store.cas_mission_status(
        mission_id,
        expect_status=str(mission.get("status") or MissionStatus.RUNNING.value),
        expect_version=version,
        expect_lease_generation=claimed_gen,
        new_status=MissionStatus.VERIFYING.value,
    )
    if not ok_cas:
        # Also allow queued→ already moved; try from running only. Fail closed to continue.
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(mission.get("ledger_cursor") or 0),
            acceptance_hash_value=str(mission.get("acceptance_hash") or ""),
            gaps=["cas_verifying_failed"],
            artifact_refs=[digest],
            poll=False,
        )
        sealed["proposed_text"] = projection
        return sealed

    refreshed = await store.load_mission(mission_id) or mission
    evidence = await store.list_mission_evidence(mission_id)
    policy = default_agent_policy()
    package = refreshed.get("package") or {}
    if isinstance(package.get("evidence_policy"), dict):
        from .evidence import EvidencePolicy

        policy = EvidencePolicy.from_dict(package["evidence_policy"])

    # Shadow cognition must not affect CommitDone (recommendations only).
    live_veto = bool(merge.get("hard_veto")) and not shadow_cognition
    check = verify_commit(
        VerifyInput(
            candidate_text=candidate,
            candidate_hash=digest,
            acceptance_text=acceptance_text,
            task_text=task_text,
            evidence_records=evidence,
            policy=policy,
            lease_generation=claimed_gen,
            expected_lease_generation=int(refreshed.get("lease_generation") or 0),
            status=MissionStatus.VERIFYING.value,
            destructive=bool(package.get("destructive")),
            security_veto=live_veto and "security" in (merge.get("veto_roles") or []),
            qa_veto=live_veto and "qa" in (merge.get("veto_roles") or []),
        )
    )
    await store.append_mission_event(mission_id, "VerifyChecked", check.to_dict())

    new_version = int(refreshed.get("checkpoint_version") or 0)
    if check.ok and check.structural_record is not None:
        await store.append_mission_evidence(
            mission_id,
            klass="structural",
            digest=str(check.structural_record["digest"]),
            payload=check.structural_record.get("payload") or {},
            artifact_refs=[digest],
            lease_generation=claimed_gen,
        )
        committed = await store.cas_mission_status(
            mission_id,
            expect_status=MissionStatus.VERIFYING.value,
            expect_version=new_version,
            expect_lease_generation=claimed_gen,
            new_status=MissionStatus.COMPLETE.value,
            checkpoint_update={
                "candidate_hash": digest,
                "last_verify": check.to_dict(),
            },
            clear_lease=True,
        )
        if committed:
            out = dict(result)
            out["status"] = "complete"
            out["job_id"] = job_id
            out["acceptance_hash"] = refreshed.get("acceptance_hash")
            out["continue_token"] = continue_token
            out["artifact_refs"] = [digest]
            out["mission"] = {
                "protocol": "unigrok_mission_v2",
                "status": MissionStatus.COMPLETE.value,
                "committed": True,
                "gaps": [],
                "check": check.to_dict(),
                "governor_shadow": gov.to_dict(),
            }
            out["autonomy"] = {
                "protocol": "unigrok_continue_v1",
                "committed": True,
                "gaps": [],
                "check": {"ok": True, "gaps": []},
            }
            # missions row is source of truth; mirror into agent_jobs poll surface.
            with contextlib.suppress(Exception):
                await store.save_agent_job(job_id, "complete", out)
            return out

    # A1.5: unrepairable / same-state literal failures → FailDone (no continue loop).
    raw_checkpoint = refreshed.get("checkpoint")
    checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
    last_verify_raw = checkpoint.get("last_verify")
    last_verify: dict[str, Any] | None = None
    if isinstance(last_verify_raw, dict):
        last_verify = {
            **last_verify_raw,
            "candidate_hash": last_verify_raw.get("candidate_hash")
            or checkpoint.get("candidate_hash"),
        }
    elif checkpoint.get("candidate_hash"):
        last_verify = {"candidate_hash": checkpoint.get("candidate_hash"), "gaps": []}
    terminal = should_terminal_fail(
        gaps=list(check.gaps),
        task_class=str(check.task_class or "substantial"),
        candidate_hash=digest,
        prior_verify_failures=int(refreshed.get("verify_failures") or 0),
        last_verify=last_verify,
    )
    if terminal:
        await store.cas_mission_status(
            mission_id,
            expect_status=MissionStatus.VERIFYING.value,
            expect_version=new_version,
            expect_lease_generation=claimed_gen,
            new_status=MissionStatus.FAILED.value,
            checkpoint_update={
                "candidate_hash": digest,
                "last_verify": check.to_dict(),
                "terminal_reason": "unrepairable_gaps",
            },
            clear_lease=True,
            bump_verify_failure=True,
        )
        out = dict(result)
        out["status"] = "error"
        out["job_id"] = job_id
        out["acceptance_hash"] = refreshed.get("acceptance_hash")
        out["continue_token"] = continue_token
        out["artifact_refs"] = [digest]
        out["stop_reason"] = "FailDone"
        out["text"] = (
            "Mission verifier failed permanently (unrepairable gaps under task "
            f"class {check.task_class}): {', '.join(check.gaps)}."
        )
        out["mission"] = {
            "protocol": "unigrok_mission_v2",
            "status": MissionStatus.FAILED.value,
            "committed": False,
            "gaps": list(check.gaps),
            "check": check.to_dict(),
            "governor_shadow": gov.to_dict(),
            "terminal_reason": "unrepairable_gaps",
        }
        out["autonomy"] = {
            "protocol": "unigrok_continue_v1",
            "committed": False,
            "gaps": list(check.gaps),
            "check": {"ok": False, "gaps": list(check.gaps)},
        }
        with contextlib.suppress(Exception):
            await store.save_agent_job(job_id, "error", out)
        return out

    # Not complete — back to waiting_event for host continue.
    await store.cas_mission_status(
        mission_id,
        expect_status=MissionStatus.VERIFYING.value,
        expect_version=new_version,
        expect_lease_generation=claimed_gen,
        new_status=MissionStatus.WAITING_EVENT.value,
        checkpoint_update={
            "candidate_hash": digest,
            "last_verify": {
                **check.to_dict(),
                "candidate_hash": digest,
            },
        },
        clear_lease=True,
        bump_verify_failure=True,
    )
    cursor_row = await store.load_mission(mission_id)
    sealed = continue_envelope(
        job_id=job_id,
        continue_token=continue_token,
        ledger_cursor=int((cursor_row or {}).get("ledger_cursor") or 0),
        acceptance_hash_value=str(refreshed.get("acceptance_hash") or ""),
        gaps=list(check.gaps),
        artifact_refs=[digest],
        text=(
            "Mission verifier rejected CommitDone. Re-invoke agent with "
            f"continue_token to close gaps: {', '.join(check.gaps)}."
        ),
        poll=False,
    )
    for key in (
        "model",
        "plane",
        "resolved_plane",
        "cost_usd",
        "orchestration",
        "telemetry_id",
        "harness",
        "requested_mode",
        "level",
        "resolved_depth",
        "agent_tools",
        "session",
    ):
        if key in result:
            sealed[key] = result[key]
    sealed["proposed_text"] = projection
    sealed["mission"] = {
        "protocol": "unigrok_mission_v2",
        "status": MissionStatus.WAITING_EVENT.value,
        "committed": False,
        "gaps": list(check.gaps),
        "check": check.to_dict(),
        "governor_shadow": gov.to_dict(),
    }
    sealed["autonomy"] = {
        "protocol": "unigrok_continue_v1",
        "committed": False,
        "gaps": list(check.gaps),
        "check": {"ok": False, "gaps": list(check.gaps)},
    }
    return sealed
