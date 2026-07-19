from __future__ import annotations

import json
from pathlib import Path

import pytest

from unigrok_public.mission.epoch import merge_mission_billing, seal_mission_epoch
from unigrok_public.mission.evidence import default_agent_policy
from unigrok_public.mission.lease import lease_expiry_iso
from unigrok_public.state import PublicStateStore


async def _create_literal_mission(
    store: PublicStateStore,
    *,
    mission_id: str,
    job_id: str,
    continue_token: str,
    lease_token: str,
) -> None:
    await store.create_mission(
        mission_id,
        job_id=job_id,
        acceptance_hash="acceptance-hash",
        acceptance_text="Reply with exactly OK",
        continue_token=continue_token,
        package={
            "task": "Reply with exactly OK",
            "acceptance": "Reply with exactly OK",
            "task_class": "literal",
            "evidence_policy": default_agent_policy().to_dict(),
        },
        lease_token=lease_token,
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=180),
    )


def test_merge_mission_billing_is_safe_idempotent_and_understands_real_usage() -> None:
    secret = "sk-secret-that-must-not-enter-mission-state"  # noqa: S105
    first = merge_mission_billing(
        {},
        {
            "text": secret,
            "model": secret,
            "resolved_plane": "api",
            "cost_usd": 0.02,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
            "incurred_attempts": [
                {
                    "stage": "completion_initial",
                    "outcome": "rejected_nonanswer",
                    "plane": "api",
                    "model": secret,
                    "raw_error": secret,
                    "cost_usd": 0.005,
                    "input_tokens": 3,
                    "output_tokens": 1,
                    "total_tokens": 4,
                },
                {
                    "stage": secret,
                    "outcome": secret,
                    "cost_usd": 0.0,
                },
            ],
        },
        lease_generation=4,
    )

    assert first["cost_usd"] == pytest.approx(0.02)
    assert first["input_tokens"] == 10
    assert first["output_tokens"] == 2
    assert first["total_tokens"] == 12
    assert first["quanta"] == 1
    assert first["receipts"] == [
        {
            "lease_generation": 4,
            "cost_usd": 0.02,
            "incurred_attempt_count": 2,
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "plane": "api",
        }
    ]
    assert first["incurred_attempts"][0]["stage"] == "completion_initial"
    assert "stage" not in first["incurred_attempts"][1]
    assert secret not in json.dumps(first, sort_keys=True)

    replayed = merge_mission_billing(
        {"billing": first},
        {
            "resolved_plane": "api",
            "cost_usd": 0.03,
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
            },
        },
        lease_generation=4,
    )
    assert replayed["cost_usd"] == pytest.approx(0.03)
    assert replayed["total_tokens"] == 14
    assert replayed["quanta"] == 1

    second = merge_mission_billing(
        {"billing": replayed},
        {"resolved_plane": "api", "cost_usd": 0.01, "total_tokens": 6},
        lease_generation=7,
    )
    assert second["cost_usd"] == pytest.approx(0.04)
    assert second["total_tokens"] == 20
    assert second["quanta"] == 2


def test_merge_mission_billing_keeps_bounded_receipts_with_exact_totals() -> None:
    checkpoint: dict[str, object] = {}
    for generation in range(1, 71):
        billing = merge_mission_billing(
            checkpoint,
            {
                "resolved_plane": "api",
                "cost_usd": 0.01,
                "total_tokens": 2,
                "incurred_attempts": [
                    {
                        "stage": "completion_initial",
                        "outcome": "rejected_nonanswer",
                        "plane": "api",
                        "cost_usd": 0.001,
                    }
                ],
            },
            lease_generation=generation,
        )
        checkpoint = {"billing": billing}

    assert billing["cost_usd"] == pytest.approx(0.70)
    assert billing["total_tokens"] == 140
    assert billing["quanta"] == 70
    assert len(billing["receipts"]) == 32
    assert billing["receipts_truncated"] == 38
    assert len(billing["incurred_attempts"]) == 64
    assert billing["incurred_attempts_truncated"] == 6


@pytest.mark.asyncio
async def test_terminal_cas_checkpoint_reconstructs_billing_without_job_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublicStateStore(tmp_path / "billing-restart.db")
    await store.initialize()
    mission_id = "msn_billing_restart"
    job_id = "a" * 32
    continue_token = "b" * 32
    lease_token = "billing-owner"  # noqa: S105
    await _create_literal_mission(
        store,
        mission_id=mission_id,
        job_id=job_id,
        continue_token=continue_token,
        lease_token=lease_token,
    )

    async def fail_job_mirror(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash before agent-job mirror")

    monkeypatch.setattr(store, "save_agent_job", fail_job_mirror)
    completed = await seal_mission_epoch(
        store,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={
            "text": "OK",
            "resolved_plane": "api",
            "cost_usd": 0.02,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
            "incurred_attempts": [
                {
                    "stage": "completion_initial",
                    "outcome": "rejected_nonanswer",
                    "plane": "api",
                    "cost_usd": 0.005,
                }
            ],
        },
        lease_generation=1,
        lease_token=lease_token,
        continue_token=continue_token,
        shadow_cognition=False,
    )
    assert completed["status"] == "complete"
    assert completed["cost_usd"] == pytest.approx(0.02)
    assert await store.load_agent_job(job_id) is None

    mission = await store.load_mission(mission_id)
    assert mission is not None and mission["status"] == "complete"
    assert mission["checkpoint"]["billing"]["cost_usd"] == pytest.approx(0.02)

    reconstructed = await seal_mission_epoch(
        store,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={"text": "stale candidate must not win"},
        lease_generation=1,
        lease_token=lease_token,
        continue_token=continue_token,
        shadow_cognition=False,
    )
    assert reconstructed["status"] == "complete"
    assert reconstructed["text"] == "OK"
    assert reconstructed["cost_usd"] == pytest.approx(0.02)
    assert reconstructed["total_tokens"] == 12
    assert reconstructed["mission_billing"]["quanta"] == 1
    assert reconstructed["incurred_attempts"][0]["lease_generation"] == 1


@pytest.mark.asyncio
async def test_later_quantum_terminal_result_carries_prior_waiting_spend(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "billing-quanta.db")
    await store.initialize()
    mission_id = "msn_billing_quanta"
    job_id = "c" * 32
    continue_token = "d" * 32
    first_lease = "billing-first"
    await _create_literal_mission(
        store,
        mission_id=mission_id,
        job_id=job_id,
        continue_token=continue_token,
        lease_token=first_lease,
    )

    waiting = await seal_mission_epoch(
        store,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={
            "text": "NO",
            "resolved_plane": "api",
            "cost_usd": 0.02,
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            },
        },
        lease_generation=1,
        lease_token=first_lease,
        continue_token=continue_token,
        shadow_cognition=False,
    )
    assert waiting["status"] == "continue"
    assert waiting["cost_usd"] == pytest.approx(0.02)

    second_lease = "billing-second"
    claimed, second_generation = await store.claim_mission(
        mission_id,
        lease_token=second_lease,
        ttl_seconds=180,
    )
    assert claimed is True
    completed = await seal_mission_epoch(
        store,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={
            "text": "OK",
            "resolved_plane": "api",
            "cost_usd": 0.03,
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 1,
                "total_tokens": 10,
            },
        },
        lease_generation=second_generation,
        lease_token=second_lease,
        continue_token=continue_token,
        shadow_cognition=False,
    )
    assert completed["status"] == "complete"
    assert completed["cost_usd"] == pytest.approx(0.05)
    assert completed["input_tokens"] == 17
    assert completed["output_tokens"] == 3
    assert completed["total_tokens"] == 20
    assert completed["mission_billing"]["quanta"] == 2
    assert [
        receipt["lease_generation"]
        for receipt in completed["mission_billing"]["receipts"]
    ] == [1, second_generation]


@pytest.mark.asyncio
async def test_stale_worker_envelope_still_reports_its_unpersisted_spend(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "billing-stale.db")
    await store.initialize()
    mission_id = "msn_billing_stale"
    job_id = "e" * 32
    continue_token = "f" * 32
    await _create_literal_mission(
        store,
        mission_id=mission_id,
        job_id=job_id,
        continue_token=continue_token,
        lease_token="real-owner",  # noqa: S106
    )

    stale = await seal_mission_epoch(
        store,
        mission_id=mission_id,
        job_id=job_id,
        acceptance_text="Reply with exactly OK",
        result={
            "text": "OK",
            "resolved_plane": "api",
            "cost_usd": 0.04,
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        },
        lease_generation=1,
        lease_token="intruder",  # noqa: S106
        continue_token=continue_token,
        shadow_cognition=False,
    )
    assert stale["status"] == "continue"
    assert stale["mission"]["lease_lost"] is True
    assert stale["cost_usd"] == pytest.approx(0.04)
    assert stale["total_tokens"] == 6

    mission = await store.load_mission(mission_id)
    assert mission is not None
    assert "billing" not in mission["checkpoint"]
