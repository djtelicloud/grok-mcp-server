"""One bounded mission epoch: freeze → verifying → CommitDone or continue."""

from __future__ import annotations

import contextlib
import math
from typing import Any

from unigrok_public.autonomy import continue_envelope

from .artifacts import artifact_projection, sealed_content_hash
from .evidence import default_agent_policy
from .governor import GovernorConfig, recommend_for_task
from .types import MissionStatus
from .verify import VerifyInput, should_terminal_fail, verify_commit
from .voters import ShadowVoterExecutor, build_slots, merge_scorecards

_BILLING_SCHEMA_VERSION = 1
_BILLING_RECEIPT_LIMIT = 32
_BILLING_ATTEMPT_LIMIT = 64
_BILLING_INT_LIMIT = 10**15
_BILLING_COST_LIMIT = 10**9
_BILLING_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
_SAFE_BILLING_PLANES = {
    "api": "api",
    "cli": "cli",
    "grok_build_oauth": "cli",
    "xai_api_key": "api",
    "mixed": "mixed",
}
_SAFE_BILLING_STAGES = {
    "agent_result",
    "agent_turn",
    "agent_work",
    "completion_initial",
    "completion_retry",
    "final_polish",
    "hive_draft",
    "hive_merge",
    "hive_vote",
    "mission_quantum",
    "route_vote",
    "router_vote",
    "routing",
    "semantic_router",
}
_SAFE_BILLING_OUTCOMES = {
    "completed",
    "completed_before_projection_failure",
    "completed_before_state_failure",
    "failed_after_reported_usage",
    "rejected_cleanup",
    "rejected_nonanswer",
}


def _billing_cost(value: Any) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return min(_BILLING_COST_LIMIT, max(0.0, parsed))


def _billing_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(_BILLING_INT_LIMIT, max(0, parsed))


def _billing_plane(value: Any) -> str | None:
    return _SAFE_BILLING_PLANES.get(str(value or "").strip().lower())


def _usage_numbers(value: dict[str, Any]) -> dict[str, int]:
    nested = value.get("usage") if isinstance(value.get("usage"), dict) else {}

    def _first(*keys: str) -> tuple[bool, int]:
        for source in (value, nested):
            for key in keys:
                if key in source:
                    return True, _billing_int(source.get(key))
        return False, 0

    input_present, input_tokens = _first(
        "input_tokens", "prompt_tokens", "inputTokens", "promptTokens"
    )
    output_present, output_tokens = _first(
        "output_tokens", "completion_tokens", "outputTokens", "completionTokens"
    )
    total_present, total_tokens = _first("total_tokens", "totalTokens")
    if not total_present and (input_present or output_present):
        total_present = True
        total_tokens = _billing_int(input_tokens + output_tokens)
    numbers: dict[str, int] = {}
    if input_present:
        numbers["input_tokens"] = input_tokens
    if output_present:
        numbers["output_tokens"] = output_tokens
    if total_present:
        numbers["total_tokens"] = total_tokens
    return numbers


def _safe_billing_attempt(
    value: dict[str, Any], *, lease_generation: int
) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "lease_generation": _billing_int(lease_generation),
        "cost_usd": _billing_cost(value.get("cost_usd")),
    }
    attempt.update(_usage_numbers(value))
    plane = _billing_plane(value.get("plane") or value.get("resolved_plane"))
    if plane:
        attempt["plane"] = plane
    stage = str(value.get("stage") or "").strip().lower()
    if stage in _SAFE_BILLING_STAGES:
        attempt["stage"] = stage
    outcome = str(value.get("outcome") or "").strip().lower()
    if outcome in _SAFE_BILLING_OUTCOMES:
        attempt["outcome"] = outcome
    return attempt


def _safe_billing_receipt(value: dict[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "lease_generation": _billing_int(value.get("lease_generation")),
        "cost_usd": _billing_cost(value.get("cost_usd")),
        "incurred_attempt_count": _billing_int(value.get("incurred_attempt_count")),
    }
    receipt.update(_usage_numbers(value))
    plane = _billing_plane(value.get("plane"))
    if plane:
        receipt["plane"] = plane
    return receipt


def _empty_billing() -> dict[str, Any]:
    return {
        "schema_version": _BILLING_SCHEMA_VERSION,
        "cost_usd": 0.0,
        "quanta": 0,
        "last_lease_generation": 0,
        "receipts": [],
        "receipts_truncated": 0,
        "incurred_attempts": [],
        "incurred_attempts_truncated": 0,
    }


def _normalized_billing(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    raw = checkpoint.get("billing") if isinstance(checkpoint, dict) else None
    if not isinstance(raw, dict):
        return _empty_billing()
    normalized = _empty_billing()
    normalized["cost_usd"] = _billing_cost(raw.get("cost_usd"))
    for key in _BILLING_TOKEN_FIELDS:
        if key in raw:
            normalized[key] = _billing_int(raw.get(key))
    normalized["quanta"] = _billing_int(raw.get("quanta"))
    normalized["last_lease_generation"] = _billing_int(
        raw.get("last_lease_generation")
    )
    raw_receipts = raw.get("receipts") if isinstance(raw.get("receipts"), list) else []
    receipts = [
        _safe_billing_receipt(item) for item in raw_receipts if isinstance(item, dict)
    ][-_BILLING_RECEIPT_LIMIT:]
    normalized["receipts"] = receipts
    normalized["receipts_truncated"] = _billing_int(
        raw.get("receipts_truncated")
    ) + max(0, len(raw_receipts) - len(receipts))
    raw_attempts = (
        raw.get("incurred_attempts")
        if isinstance(raw.get("incurred_attempts"), list)
        else []
    )
    attempts = [
        _safe_billing_attempt(
            item,
            lease_generation=_billing_int(item.get("lease_generation")),
        )
        for item in raw_attempts
        if isinstance(item, dict)
    ][-_BILLING_ATTEMPT_LIMIT:]
    normalized["incurred_attempts"] = attempts
    normalized["incurred_attempts_truncated"] = _billing_int(
        raw.get("incurred_attempts_truncated")
    ) + max(0, len(raw_attempts) - len(attempts))
    return normalized


def _billing_present(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return any(
        key in value
        for key in (
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "usage",
            "incurred_attempts",
        )
    )


def _attempt_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        value.get(key)
        for key in (
            "lease_generation",
            "stage",
            "outcome",
            "plane",
            "cost_usd",
            *_BILLING_TOKEN_FIELDS,
        )
    )


def merge_mission_billing(
    checkpoint: dict[str, Any] | None,
    usage: dict[str, Any] | None,
    *,
    lease_generation: int,
) -> dict[str, Any]:
    """Return a bounded cumulative billing snapshot for one fenced mission quantum.

    The lease generation is the idempotency key. Replaying the same generation
    max-merges newly reported totals; a later generation adds one quantum. Only
    numeric usage and closed-enum labels survive, so prompts, provider text, raw
    errors, credentials, and arbitrary metadata never enter the checkpoint.
    """
    billing = _normalized_billing(checkpoint)
    if not _billing_present(usage):
        return billing

    generation = _billing_int(lease_generation)
    current_numbers = _usage_numbers(usage or {})
    raw_attempts = (
        usage.get("incurred_attempts")
        if isinstance(usage, dict) and isinstance(usage.get("incurred_attempts"), list)
        else []
    )
    current_receipt: dict[str, Any] = {
        "lease_generation": generation,
        "cost_usd": _billing_cost((usage or {}).get("cost_usd")),
        "incurred_attempt_count": len(
            [item for item in raw_attempts if isinstance(item, dict)]
        ),
        **current_numbers,
    }
    plane = _billing_plane(
        (usage or {}).get("resolved_plane") or (usage or {}).get("plane")
    )
    if plane:
        current_receipt["plane"] = plane

    receipts = [dict(item) for item in billing["receipts"]]
    existing_index = next(
        (
            index
            for index, item in enumerate(receipts)
            if int(item.get("lease_generation") or 0) == generation
        ),
        None,
    )
    prior_receipt: dict[str, Any] = {}
    is_new_generation = existing_index is None and generation > int(
        billing.get("last_lease_generation") or 0
    )
    if existing_index is not None:
        prior_receipt = receipts[existing_index]
        merged_receipt = dict(prior_receipt)
        merged_receipt["cost_usd"] = max(
            _billing_cost(prior_receipt.get("cost_usd")),
            _billing_cost(current_receipt.get("cost_usd")),
        )
        for key in (*_BILLING_TOKEN_FIELDS, "incurred_attempt_count"):
            if key in prior_receipt or key in current_receipt:
                merged_receipt[key] = max(
                    _billing_int(prior_receipt.get(key)),
                    _billing_int(current_receipt.get(key)),
                )
        if current_receipt.get("plane"):
            merged_receipt["plane"] = current_receipt["plane"]
        receipts[existing_index] = _safe_billing_receipt(merged_receipt)
        current_receipt = receipts[existing_index]
    elif is_new_generation:
        receipts.append(_safe_billing_receipt(current_receipt))
    else:
        # Generations are monotonic. An old receipt that has rolled out of the
        # bounded window is a replay, not a new billable quantum.
        current_receipt = {}

    if current_receipt:
        billing["cost_usd"] = _billing_cost(
            float(billing.get("cost_usd") or 0.0)
            + max(
                0.0,
                _billing_cost(current_receipt.get("cost_usd"))
                - _billing_cost(prior_receipt.get("cost_usd")),
            )
        )
        for key in _BILLING_TOKEN_FIELDS:
            if key in current_receipt or key in prior_receipt:
                billing[key] = _billing_int(
                    int(billing.get(key) or 0)
                    + max(
                        0,
                        _billing_int(current_receipt.get(key))
                        - _billing_int(prior_receipt.get(key)),
                    )
                )
        if is_new_generation:
            billing["quanta"] = _billing_int(int(billing.get("quanta") or 0) + 1)
        billing["last_lease_generation"] = max(
            int(billing.get("last_lease_generation") or 0), generation
        )

    if len(receipts) > _BILLING_RECEIPT_LIMIT:
        dropped = len(receipts) - _BILLING_RECEIPT_LIMIT
        receipts = receipts[dropped:]
        billing["receipts_truncated"] = _billing_int(
            int(billing.get("receipts_truncated") or 0) + dropped
        )
    billing["receipts"] = receipts

    attempts = [dict(item) for item in billing["incurred_attempts"]]
    seen = {_attempt_key(item) for item in attempts}
    for item in raw_attempts:
        if not isinstance(item, dict):
            continue
        safe = _safe_billing_attempt(item, lease_generation=generation)
        key = _attempt_key(safe)
        if key not in seen:
            attempts.append(safe)
            seen.add(key)
    if len(attempts) > _BILLING_ATTEMPT_LIMIT:
        dropped = len(attempts) - _BILLING_ATTEMPT_LIMIT
        attempts = attempts[dropped:]
        billing["incurred_attempts_truncated"] = _billing_int(
            int(billing.get("incurred_attempts_truncated") or 0) + dropped
        )
    billing["incurred_attempts"] = attempts
    return _normalized_billing({"billing": billing})


def _checkpoint_billing(row: dict[str, Any]) -> dict[str, Any]:
    checkpoint = row.get("checkpoint") if isinstance(row.get("checkpoint"), dict) else {}
    return _normalized_billing(checkpoint)


def _billing_with_result(
    row: dict[str, Any], result: dict[str, Any], lease_generation: int
) -> dict[str, Any]:
    checkpoint = row.get("checkpoint") if isinstance(row.get("checkpoint"), dict) else {}
    return merge_mission_billing(
        checkpoint,
        result,
        lease_generation=lease_generation,
    )


def _apply_mission_billing(
    payload: dict[str, Any], billing: dict[str, Any]
) -> dict[str, Any]:
    safe = _normalized_billing({"billing": billing})
    if not safe.get("quanta") and not safe.get("cost_usd"):
        return payload
    payload["cost_usd"] = safe["cost_usd"]
    for key in _BILLING_TOKEN_FIELDS:
        if key in safe:
            payload[key] = safe[key]
    attempts = safe.get("incurred_attempts") or []
    if attempts:
        payload["incurred_attempts"] = attempts
    else:
        payload.pop("incurred_attempts", None)
    payload["mission_billing"] = safe
    return payload


def apply_checkpoint_billing(
    payload: dict[str, Any], checkpoint: dict[str, Any] | None
) -> dict[str, Any]:
    """Apply one sanitized durable billing projection to a public envelope."""
    return _apply_mission_billing(payload, _normalized_billing(checkpoint))


async def seal_mission_epoch(
    store: Any,
    *,
    mission_id: str,
    job_id: str,
    acceptance_text: str,
    result: dict[str, Any],
    lease_generation: int,
    continue_token: str,
    lease_token: str | None = None,
    envelope_version: int = 1,
    shadow_cognition: bool = True,
    lease_ttl_seconds: int = 180,
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
    worker_generation = int(lease_generation)
    entry_billing = _billing_with_result(mission, result, worker_generation)
    raw_package = mission.get("package")
    package = raw_package if isinstance(raw_package, dict) else {}
    try:
        frozen_lease_ttl = int(package.get("lease_ttl_seconds", lease_ttl_seconds))
    except (TypeError, ValueError):
        frozen_lease_ttl = int(lease_ttl_seconds)
    frozen_lease_ttl = max(30, min(frozen_lease_ttl, 900))

    terminal_statuses = {
        MissionStatus.COMPLETE.value,
        MissionStatus.FAILED.value,
        MissionStatus.CANCELLED.value,
        MissionStatus.BUDGET_EXHAUSTED.value,
    }

    async def _terminal_payload(
        row: dict[str, Any], billing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return the durable winner, never the duplicate worker's candidate."""
        terminal_billing = billing if billing is not None else _checkpoint_billing(row)
        status = str(row.get("status") or "")
        stored = await store.load_agent_job(str(row.get("job_id") or job_id))
        expected_job_status = (
            "complete" if status == MissionStatus.COMPLETE.value else "error"
        )
        stored_payload = stored.get("payload") if stored else None
        stored_mission = (
            stored_payload.get("mission")
            if isinstance(stored_payload, dict)
            and isinstance(stored_payload.get("mission"), dict)
            else {}
        )
        if (
            stored
            and str(stored.get("status") or "") == expected_job_status
            and isinstance(stored_payload, dict)
            and str(stored_payload.get("status") or "") == expected_job_status
            and str(stored_payload.get("job_id") or "")
            == str(row.get("job_id") or job_id)
            and str(stored_payload.get("acceptance_hash") or "")
            == str(row.get("acceptance_hash") or "")
            and str(stored_mission.get("status") or "") == status
        ):
            payload = dict(stored_payload)
            payload.setdefault("job_id", job_id)
            return _apply_mission_billing(payload, terminal_billing)
        checkpoint = row.get("checkpoint") if isinstance(row.get("checkpoint"), dict) else {}
        candidate_hash = str(checkpoint.get("candidate_hash") or "")
        projection_hash = str(
            checkpoint.get("candidate_projection_hash") or candidate_hash
        )
        artifact = (
            await store.get_mission_artifact(projection_hash)
            if projection_hash and hasattr(store, "get_mission_artifact")
            else None
        )
        last_verify = (
            checkpoint.get("last_verify")
            if isinstance(checkpoint.get("last_verify"), dict)
            else {}
        )
        gaps = [] if status == MissionStatus.COMPLETE.value else [
            str(value) for value in (last_verify.get("gaps") or []) if value
        ]
        if status != MissionStatus.COMPLETE.value and not gaps:
            gaps = [str(checkpoint.get("terminal_reason") or status or "mission_failed")]
        out: dict[str, Any] = {
            "status": "complete" if status == MissionStatus.COMPLETE.value else "error",
            "job_id": job_id,
            "continue_token": str(row.get("continue_token") or continue_token),
            "acceptance_hash": row.get("acceptance_hash"),
            "artifact_refs": [projection_hash] if projection_hash else [],
            "mission": {
                "protocol": "unigrok_mission_v2",
                "status": status,
                "terminal": True,
                "committed": status == MissionStatus.COMPLETE.value,
                "gaps": gaps,
            },
            "autonomy": {
                "protocol": "unigrok_continue_v1",
                "committed": status == MissionStatus.COMPLETE.value,
                "gaps": gaps,
            },
        }
        if artifact and status == MissionStatus.COMPLETE.value:
            out["text"] = str(artifact.get("sealed") or artifact.get("projection") or "")
        elif status == MissionStatus.COMPLETE.value:
            out["text"] = "Mission completed; the durable answer projection is unavailable."
        else:
            out["text"] = (
                f"Mission ended with terminal status {status}: {', '.join(gaps)}."
            )
            if artifact:
                out["proposed_text"] = str(
                    artifact.get("projection") or artifact.get("sealed") or ""
                )
        return _apply_mission_billing(out, terminal_billing)

    if str(mission.get("status")) in terminal_statuses:
        return await _terminal_payload(mission, entry_billing)

    worker_token = str(lease_token or mission.get("lease_token") or "")
    if (
        worker_generation != int(mission.get("lease_generation") or 0)
        or not worker_token
        or worker_token != str(mission.get("lease_token") or "")
    ):
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(mission.get("ledger_cursor") or 0),
            acceptance_hash_value=str(mission.get("acceptance_hash") or ""),
            gaps=["stale_lease_owner"],
            text="Mission lease is not owned by this worker; re-invoke continue_token.",
            poll=False,
        )
        sealed["mission"] = {
            "status": mission.get("status"),
            "lease_lost": True,
            "committed": False,
        }
        return _apply_mission_billing(sealed, entry_billing)

    # Renew the exact worker capability before any candidate artifact/event write.
    # A worker that cannot heartbeat is stale and must remain read-only.
    if hasattr(store, "heartbeat_mission"):
        renewed = await store.heartbeat_mission(
            mission_id,
            lease_token=worker_token,
            lease_generation=worker_generation,
            ttl_seconds=frozen_lease_ttl,
        )
        if not renewed:
            sealed = continue_envelope(
                job_id=job_id,
                continue_token=continue_token,
                ledger_cursor=int(mission.get("ledger_cursor") or 0),
                acceptance_hash_value=str(mission.get("acceptance_hash") or ""),
                gaps=["stale_lease_owner"],
                text="Mission lease was lost before candidate freeze.",
                poll=False,
            )
            sealed["mission"] = {
                "status": mission.get("status"),
                "lease_lost": True,
                "committed": False,
            }
            return _apply_mission_billing(sealed, entry_billing)

    candidate = str(result.get("text") or "")
    digest = sealed_content_hash(candidate, kind="candidate")
    projection = artifact_projection(candidate)
    projection_kind = f"candidate_projection:{mission_id}"
    projection_digest = sealed_content_hash(projection, kind=projection_kind)

    # Epoch-wide cognition uses the mission's frozen config. Legacy rows without a
    # valid record get one compatibility recommendation; current missions never
    # drift when process settings or governor weights change between quanta.
    task_text = str(package.get("task") or acceptance_text or "")
    gov = GovernorConfig.from_dict(package.get("governor_config"))
    governor_source = "frozen_mission"
    if gov is None:
        governor_source = "legacy_mission_fallback"
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

    # Persist only the redacted projection. The separate raw candidate hash is
    # retained as a commitment, while this key remains verifiable from SQLite.
    await store.put_mission_artifact(
        mission_id,
        projection_digest,
        kind=projection_kind,
        sealed=projection,
        projection=projection,
        lease_token=worker_token,
        lease_generation=worker_generation,
    )
    await store.append_mission_event(
        mission_id,
        "CandidateFrozen",
        {
            "candidate_hash": digest,
            "projection_hash": projection_digest,
            "bytes": len(candidate.encode("utf-8")),
            "governor": gov.to_dict(),
            "governor_source": governor_source,
        },
        lease_token=worker_token,
        lease_generation=worker_generation,
    )
    if cards:
        await store.append_mission_event(
            mission_id,
            "CouncilShadow",
            {"merge": merge, "votes": [c.to_dict() for c in cards]},
            lease_token=worker_token,
            lease_generation=worker_generation,
        )

    # Always re-read fencing immediately before CAS — rapid reattach can race.
    fresh = await store.load_mission(mission_id) or mission
    fresh_billing = _billing_with_result(fresh, result, worker_generation)
    version = int(fresh.get("checkpoint_version") or 0)
    claimed_gen = worker_generation
    status_now = str(fresh.get("status") or MissionStatus.RUNNING.value)
    if (
        claimed_gen != int(fresh.get("lease_generation") or 0)
        or worker_token != str(fresh.get("lease_token") or "")
    ):
        # Stale worker — do not advance truth.
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(fresh.get("ledger_cursor") or 0),
            acceptance_hash_value=str(fresh.get("acceptance_hash") or ""),
            gaps=["stale_lease_generation"],
            artifact_refs=[projection_digest],
            text="Mission lease lost before verifying; re-invoke continue_token.",
            poll=False,
        )
        sealed["proposed_text"] = projection
        sealed["mission"] = {"status": fresh.get("status"), "lease_lost": True}
        return _apply_mission_billing(sealed, fresh_billing)

    # Bind envelope: clamp only (never raise). Record version.
    envelope_bound = await store.touch_mission_envelope(
        mission_id,
        envelope_version=int(envelope_version),
        lease_token=worker_token,
        lease_generation=claimed_gen,
    )
    if not envelope_bound:
        current = await store.load_mission(mission_id) or fresh
        if str(current.get("status") or "") in terminal_statuses:
            return await _terminal_payload(
                current,
                _billing_with_result(current, result, worker_generation),
            )
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(current.get("ledger_cursor") or 0),
            acceptance_hash_value=str(current.get("acceptance_hash") or ""),
            gaps=["stale_lease_generation"],
            artifact_refs=[projection_digest],
            text="Mission lease lost while binding the envelope; re-invoke continue_token.",
            poll=False,
        )
        sealed["proposed_text"] = projection
        sealed["mission"] = {
            "status": current.get("status"),
            "lease_lost": True,
            "committed": False,
        }
        return _apply_mission_billing(
            sealed,
            _billing_with_result(current, result, worker_generation),
        )

    if status_now == MissionStatus.VERIFYING.value:
        # Self-heal sticky verifying: never CAS verifying→verifying (illegal).
        # Continue verify→CommitDone with the fresh version + lease fence.
        ok_cas = True
        refreshed = fresh
    elif status_now == MissionStatus.RUNNING.value:
        ok_cas = await store.cas_mission_status(
            mission_id,
            expect_status=MissionStatus.RUNNING.value,
            expect_version=version,
            expect_lease_generation=claimed_gen,
            expect_lease_token=worker_token,
            new_status=MissionStatus.VERIFYING.value,
            checkpoint_update={"billing": fresh_billing},
        )
        refreshed = await store.load_mission(mission_id) or fresh
    else:
        ok_cas = False
        refreshed = fresh

    if not ok_cas:
        # Fail closed to continue — do not hard-loop on illegal transitions.
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(refreshed.get("ledger_cursor") or 0),
            acceptance_hash_value=str(refreshed.get("acceptance_hash") or ""),
            gaps=["cas_verifying_failed"],
            artifact_refs=[projection_digest],
            poll=False,
        )
        sealed["proposed_text"] = projection
        sealed["mission"] = {
            "protocol": "unigrok_mission_v2",
            "status": refreshed.get("status"),
            "committed": False,
            "gaps": ["cas_verifying_failed"],
            "status_at_cas": status_now,
        }
        return _apply_mission_billing(
            sealed,
            _billing_with_result(refreshed, result, worker_generation),
        )
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
            frozen_task_class=package.get("task_class"),
            frozen_verification_mode=package.get("verification_mode"),
            candidate_artifact_refs=(projection_digest,),
        )
    )
    await store.append_mission_event(
        mission_id,
        "VerifyChecked",
        check.to_dict(),
        lease_token=worker_token,
        lease_generation=claimed_gen,
    )

    # Re-load fencing after verify — claim must not have stolen mid-flight.
    pre_commit = await store.load_mission(mission_id) or refreshed
    commit_billing = _billing_with_result(pre_commit, result, worker_generation)
    if str(pre_commit.get("status")) == MissionStatus.COMPLETE.value:
        return await _terminal_payload(pre_commit, commit_billing)
    new_version = int(pre_commit.get("checkpoint_version") or 0)
    commit_gen = int(pre_commit.get("lease_generation") or 0)
    if (
        commit_gen != claimed_gen
        or worker_token != str(pre_commit.get("lease_token") or "")
    ):
        sealed = continue_envelope(
            job_id=job_id,
            continue_token=continue_token,
            ledger_cursor=int(pre_commit.get("ledger_cursor") or 0),
            acceptance_hash_value=str(pre_commit.get("acceptance_hash") or ""),
            gaps=["stale_lease_generation"],
            artifact_refs=[projection_digest],
            text="Mission lease lost during verifying; re-invoke continue_token.",
            poll=False,
        )
        sealed["proposed_text"] = projection
        sealed["mission"] = {
            "status": pre_commit.get("status"),
            "lease_lost": True,
            "committed": False,
        }
        return _apply_mission_billing(sealed, commit_billing)

    if check.ok and check.structural_record is not None:
        await store.append_mission_evidence(
            mission_id,
            klass="structural",
            digest=str(check.structural_record["digest"]),
            payload=check.structural_record.get("payload") or {},
            artifact_refs=[projection_digest],
            lease_generation=commit_gen,
            lease_token=worker_token,
        )
        committed = await store.cas_mission_status(
            mission_id,
            expect_status=MissionStatus.VERIFYING.value,
            expect_version=new_version,
            expect_lease_generation=commit_gen,
            expect_lease_token=worker_token,
            new_status=MissionStatus.COMPLETE.value,
            checkpoint_update={
                "candidate_hash": digest,
                "candidate_projection_hash": projection_digest,
                "last_verify": check.to_dict(),
                "billing": commit_billing,
            },
            clear_lease=True,
        )
        if committed:
            out = dict(result)
            out["status"] = "complete"
            out["job_id"] = job_id
            out["acceptance_hash"] = pre_commit.get("acceptance_hash")
            out["continue_token"] = continue_token
            out["artifact_refs"] = [projection_digest]
            out["mission"] = {
                "protocol": "unigrok_mission_v2",
                "status": MissionStatus.COMPLETE.value,
                "committed": True,
                "gaps": [],
                "check": check.to_dict(),
                "governor_shadow": gov.to_dict(),
                "governor_source": governor_source,
            }
            out["autonomy"] = {
                "protocol": "unigrok_continue_v1",
                "committed": True,
                "gaps": [],
                "check": {"ok": True, "gaps": []},
            }
            _apply_mission_billing(out, commit_billing)
            # missions row is source of truth; mirror into agent_jobs poll surface.
            with contextlib.suppress(Exception):
                await store.save_agent_job(job_id, "complete", out)
            return out

    # A1.5: unrepairable / same-state literal failures → FailDone (no continue loop).
    raw_checkpoint = pre_commit.get("checkpoint")
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
        prior_verify_failures=int(pre_commit.get("verify_failures") or 0),
        last_verify=last_verify,
    )
    if terminal:
        failed = await store.cas_mission_status(
            mission_id,
            expect_status=MissionStatus.VERIFYING.value,
            expect_version=new_version,
            expect_lease_generation=commit_gen,
            expect_lease_token=worker_token,
            new_status=MissionStatus.FAILED.value,
            checkpoint_update={
                "candidate_hash": digest,
                "candidate_projection_hash": projection_digest,
                "last_verify": check.to_dict(),
                "terminal_reason": "unrepairable_gaps",
                "billing": commit_billing,
            },
            clear_lease=True,
            bump_verify_failure=True,
        )
        if not failed:
            current = await store.load_mission(mission_id) or pre_commit
            if str(current.get("status") or "") in terminal_statuses:
                return await _terminal_payload(
                    current,
                    _billing_with_result(current, result, worker_generation),
                )
            conflict_gaps = [*list(check.gaps), "cas_faildone_failed"]
            sealed = continue_envelope(
                job_id=job_id,
                continue_token=continue_token,
                ledger_cursor=int(current.get("ledger_cursor") or 0),
                acceptance_hash_value=str(current.get("acceptance_hash") or ""),
                gaps=conflict_gaps,
                artifact_refs=[projection_digest],
                text=(
                    "Mission ownership changed before FailDone; re-invoke the "
                    "same continue_token to read durable truth."
                ),
                poll=False,
            )
            sealed["proposed_text"] = projection
            sealed["mission"] = {
                "protocol": "unigrok_mission_v2",
                "status": str(current.get("status") or ""),
                "committed": False,
                "gaps": conflict_gaps,
                "cas_failed": True,
            }
            return _apply_mission_billing(
                sealed,
                _billing_with_result(current, result, worker_generation),
            )
        out = dict(result)
        out["status"] = "error"
        out["job_id"] = job_id
        out["acceptance_hash"] = pre_commit.get("acceptance_hash")
        out["continue_token"] = continue_token
        out["artifact_refs"] = [projection_digest]
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
            "governor_source": governor_source,
            "terminal_reason": "unrepairable_gaps",
            "cas_failed": False,
        }
        out["autonomy"] = {
            "protocol": "unigrok_continue_v1",
            "committed": False,
            "gaps": list(check.gaps),
            "check": {"ok": False, "gaps": list(check.gaps)},
        }
        _apply_mission_billing(out, commit_billing)
        with contextlib.suppress(Exception):
            await store.save_agent_job(job_id, "error", out)
        return out

    # Not complete — back to waiting_event for host continue.
    waiting = await store.cas_mission_status(
        mission_id,
        expect_status=MissionStatus.VERIFYING.value,
        expect_version=new_version,
        expect_lease_generation=commit_gen,
        expect_lease_token=worker_token,
        new_status=MissionStatus.WAITING_EVENT.value,
        checkpoint_update={
            "candidate_hash": digest,
            "candidate_projection_hash": projection_digest,
            "last_verify": {
                **check.to_dict(),
                "candidate_hash": digest,
            },
            "billing": commit_billing,
        },
        clear_lease=True,
        bump_verify_failure=True,
    )
    cursor_row = await store.load_mission(mission_id)
    # If WAITING_EVENT CAS failed, surface it — silent ignore left sticky VERIFYING.
    continue_gaps = list(check.gaps)
    if not waiting:
        if str((cursor_row or {}).get("status")) in terminal_statuses:
            terminal_row = cursor_row or {}
            return await _terminal_payload(
                terminal_row,
                _billing_with_result(terminal_row, result, worker_generation),
            )
        continue_gaps = [*continue_gaps, "cas_waiting_event_failed"]
    sealed = continue_envelope(
        job_id=job_id,
        continue_token=continue_token,
        ledger_cursor=int((cursor_row or {}).get("ledger_cursor") or 0),
        acceptance_hash_value=str(pre_commit.get("acceptance_hash") or ""),
        gaps=continue_gaps,
        artifact_refs=[projection_digest],
        text=(
            "Mission verifier rejected CommitDone. Re-invoke agent with "
            f"continue_token to close gaps: {', '.join(continue_gaps)}."
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
        "status": str((cursor_row or {}).get("status") or MissionStatus.WAITING_EVENT.value),
        "committed": False,
        "gaps": continue_gaps,
        "check": check.to_dict(),
        "governor_shadow": gov.to_dict(),
        "governor_source": governor_source,
        "waiting_cas_ok": waiting,
    }
    sealed["autonomy"] = {
        "protocol": "unigrok_continue_v1",
        "committed": False,
        "gaps": continue_gaps,
        "check": {"ok": False, "gaps": continue_gaps},
    }
    row_for_billing = cursor_row or pre_commit
    return _apply_mission_billing(
        sealed,
        _billing_with_result(row_for_billing, result, worker_generation),
    )
