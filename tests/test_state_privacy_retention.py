from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from unigrok_public.mission.artifacts import sealed_content_hash
from unigrok_public.mission.epoch import seal_mission_epoch
from unigrok_public.mission.evidence import default_agent_policy
from unigrok_public.mission.lease import lease_expiry_iso
from unigrok_public.state import DURABLE_TEXT_MAX_BYTES, PublicStateStore

CONTINUE_A = "a" * 32  # noqa: S105
CONTINUE_B = "b" * 32  # noqa: S105
RAW_SECRET = "durable-store-must-not-keep-this"  # noqa: S105
OLD_TIMESTAMP = "2000-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_session_commit_key_persists_exactly_one_turn(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "session-once.db")
    first_count, first_inserted = await store.append_turn_once(
        "durable-session",
        "user request",
        "committed answer",
        commit_key="job-session-once",
    )
    second_count, second_inserted = await store.append_turn_once(
        "durable-session",
        "duplicate user request",
        "duplicate answer",
        commit_key="job-session-once",
    )

    assert (first_count, first_inserted) == (2, True)
    assert (second_count, second_inserted) == (2, False)
    messages = await store.load_messages("durable-session")
    assert [message["content"] for message in messages] == [
        "user request",
        "committed answer",
    ]


@pytest.mark.asyncio
async def test_structured_durable_payloads_redact_secrets_but_keep_control_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "privacy.db"
    store = PublicStateStore(path)
    await store.save_agent_job(
        "job-private",
        "complete",
        {
            "continue_token": CONTINUE_A,
            "token_usage": {"input_tokens": 7},
            "password": RAW_SECRET,
            "OPENAI_API_KEY": RAW_SECRET,
            "GITHUB_TOKEN": RAW_SECRET,
            "AWS_SECRET_ACCESS_KEY": RAW_SECRET,
            "DATABASE_PASSWORD": RAW_SECRET,
            "nested": {
                "authorization": f"Bearer {RAW_SECRET}",
                "note": f"DATABASE_PASSWORD={RAW_SECRET}",
            },
        },
    )
    job = await store.load_agent_job("job-private")
    assert job is not None and isinstance(job["payload"], dict)
    assert job["payload"]["continue_token"] == CONTINUE_A
    assert job["payload"]["token_usage"] == {"input_tokens": 7}
    assert job["payload"]["password"] == "[REDACTED]"  # noqa: S105
    assert RAW_SECRET not in json.dumps(job["payload"])

    await store.create_autonomy_job(
        "aut-private",
        acceptance_hash="accept",
        acceptance_text="Return a safe result",
        continue_token=CONTINUE_B,
        request={"client_secret": RAW_SECRET, "prompt": f"TOKEN={RAW_SECRET}"},
    )
    autonomy = await store.load_autonomy_job("aut-private")
    assert autonomy is not None
    assert RAW_SECRET not in str(autonomy["request_json"])

    await store.create_mission(
        "mission-private",
        job_id="mission-job-private",
        acceptance_hash="accept",
        acceptance_text="Return a safe result",
        continue_token="c" * 32,
        package={"task": f"XAI_API_KEY={RAW_SECRET}", "cookie": RAW_SECRET},
        lease_token="lease-private",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )
    await store.append_mission_event(
        "mission-private", "Observed", {"access_token": RAW_SECRET}
    )
    mission = await store.load_mission("mission-private")
    assert mission is not None
    assert RAW_SECRET not in json.dumps(mission["package"])
    assert not await store.put_mission_artifact(
        "mission-private",
        "stale-artifact",
        kind="candidate_projection:mission-private",
        sealed="stale",
        projection="stale",
        lease_token="wrong-owner",  # noqa: S106
        lease_generation=1,
    )
    assert (
        await store.append_mission_event(
            "mission-private",
            "StaleWorkerEvent",
            {},
            lease_token="wrong-owner",  # noqa: S106
            lease_generation=1,
        )
        == 0
    )
    assert await store.get_mission_artifact("stale-artifact") is None
    with sqlite3.connect(path) as connection:
        event = connection.execute(
            "SELECT payload FROM mission_ledger WHERE mission_id=?",
            ("mission-private",),
        ).fetchone()
    assert event is not None and RAW_SECRET not in str(event[0])


@pytest.mark.asyncio
async def test_retention_prunes_only_old_terminal_runtime_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIGROK_STATE_RETENTION_HOURS", "1")
    path = tmp_path / "retention.db"
    store = PublicStateStore(path)
    await store.initialize()

    await store.save_agent_job("agent-done", "complete", {"text": "done"})
    await store.save_agent_job("agent-running", "running", {"text": "working"})
    await store.create_autonomy_job(
        "aut-done",
        acceptance_hash="one",
        acceptance_text="done",
        continue_token=CONTINUE_A,
    )
    await store.set_autonomy_status("aut-done", "committed")
    await store.create_autonomy_job(
        "aut-waiting",
        acceptance_hash="two",
        acceptance_text="waiting",
        continue_token=CONTINUE_B,
    )
    await store.set_autonomy_status("aut-waiting", "needs_continuation")

    await store.create_autonomy_job(
        "mission-job-terminal",
        acceptance_hash="terminal",
        acceptance_text="terminal",
        continue_token="f" * 32,
    )
    await store.create_mission(
        "mission-terminal",
        job_id="mission-job-terminal",
        acceptance_hash="terminal",
        acceptance_text="terminal",
        continue_token="f" * 32,
        package={},
        lease_token="lease-terminal",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )
    assert await store.cas_mission_status(
        "mission-terminal",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token="lease-terminal",  # noqa: S106
        new_status="failed",
        clear_lease=True,
    )
    terminal_compat = await store.load_autonomy_job("mission-job-terminal")
    assert terminal_compat is not None
    assert terminal_compat["status"] == "terminal"

    for suffix, status in (("done", "complete"), ("running", "running")):
        await store.create_mission(
            f"mission-{suffix}",
            job_id=f"mission-job-{suffix}",
            acceptance_hash=suffix,
            acceptance_text=suffix,
            continue_token=("d" if suffix == "done" else "e") * 32,
            package={},
            lease_token=f"lease-{suffix}",  # noqa: S106
            lease_generation=1,
            lease_expires_at=lease_expiry_iso(ttl_seconds=60),
        )
        if status == "complete":
            assert await store.cas_mission_status(
                f"mission-{suffix}",
                expect_status="running",
                expect_version=0,
                expect_lease_generation=1,
                expect_lease_token=f"lease-{suffix}",
                new_status="verifying",
            )
            assert await store.cas_mission_status(
                f"mission-{suffix}",
                expect_status="verifying",
                expect_version=1,
                expect_lease_generation=1,
                expect_lease_token=f"lease-{suffix}",
                new_status="complete",
                clear_lease=True,
            )
    await store.put_mission_artifact(
        "mission-done",
        "old-artifact",
        kind="candidate_projection",
        sealed="old",
        projection="old",
    )
    await store.save_telemetry(
        {"caller": "test", "request_kind": "agent", "metadata": {}}
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE agent_jobs SET created_at=?", (OLD_TIMESTAMP,)
        )
        connection.execute(
            "UPDATE autonomy_jobs SET updated_at=?", (OLD_TIMESTAMP,)
        )
        connection.execute("UPDATE missions SET updated_at=?", (OLD_TIMESTAMP,))
        connection.execute("UPDATE telemetry SET created_at=?", (OLD_TIMESTAMP,))
        connection.commit()

    assert await store.prune_retention() == 6
    assert await store.load_agent_job("agent-done") is None
    assert await store.load_agent_job("agent-running") is not None
    assert await store.load_autonomy_job("aut-done") is None
    assert await store.load_autonomy_job("aut-waiting") is not None
    assert await store.load_autonomy_job("mission-job-terminal") is None
    assert await store.load_autonomy_by_token("f" * 32) is None
    assert await store.load_mission("mission-done") is None
    assert await store.load_mission("mission-terminal") is None
    assert await store.load_mission("mission-running") is not None
    assert await store.get_mission_artifact("old-artifact") is None
    assert (await store.telemetry_summary())["sample_size"] == 0


@pytest.mark.asyncio
async def test_terminal_job_retention_starts_when_result_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIGROK_STATE_RETENTION_HOURS", "1")
    path = tmp_path / "terminal-result-age.db"
    store = PublicStateStore(path)
    await store.save_agent_job("long-job", "running", {"status": "pending"})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE agent_jobs SET created_at=? WHERE job_id=?",
            (OLD_TIMESTAMP, "long-job"),
        )
        connection.commit()

    await store.save_agent_job(
        "long-job", "complete", {"status": "complete", "text": "finished"}
    )
    assert await store.load_agent_job("long-job") == {
        "status": "complete",
        "payload": {"status": "complete", "text": "finished"},
    }
    assert await store.prune_retention() == 0


@pytest.mark.asyncio
async def test_mission_artifact_ref_hashes_the_persisted_projection(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "artifact-integrity.db")
    candidate = (
        "- Check the /healthz endpoint before rollout.\n"
        "- Confirm the ready signal remains stable for several samples.\n"
        f"- Record the deployment result; XAI_API_KEY={RAW_SECRET}."
        + (" Additional bounded deployment detail." * 3_500)
    )
    await store.create_mission(
        "mission-artifact",
        job_id="job-artifact",
        acceptance_hash="artifact",
        acceptance_text="Return a checklist of deployment steps including healthz",
        continue_token=CONTINUE_A,
        package={
            "task": "Return a checklist of deployment steps including healthz",
            "acceptance": "Return a checklist of deployment steps including healthz",
            "task_class": "substantial",
            "verification_mode": "structural",
            "evidence_policy": default_agent_policy().to_dict(),
        },
        lease_token="lease-artifact",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )

    result = await seal_mission_epoch(
        store,
        mission_id="mission-artifact",
        job_id="job-artifact",
        acceptance_text="Return a checklist of deployment steps including healthz",
        result={"text": candidate, "model": "test"},
        lease_generation=1,
        lease_token="lease-artifact",  # noqa: S106
        continue_token=CONTINUE_A,
        shadow_cognition=False,
    )

    assert result["status"] == "complete"
    assert result["text"] == candidate
    artifact_ref = result["artifact_refs"][0]
    artifact = await store.get_mission_artifact(artifact_ref)
    assert artifact is not None
    assert artifact["kind"] == "candidate_projection:mission-artifact"
    assert sealed_content_hash(artifact["sealed"], kind=artifact["kind"]) == artifact_ref
    assert RAW_SECRET not in artifact["sealed"]
    stored = await store.load_agent_job("job-artifact")
    assert stored is not None and isinstance(stored["payload"], dict)
    assert stored["payload"]["text"] == artifact["sealed"]
    assert len(stored["payload"]["text"].encode("utf-8")) <= DURABLE_TEXT_MAX_BYTES
    assert len(stored["payload"]["text"].encode("utf-8")) < len(
        candidate.encode("utf-8")
    )
    mission = await store.load_mission("mission-artifact")
    assert mission is not None
    assert mission["checkpoint"]["candidate_hash"] == sealed_content_hash(
        candidate, kind="candidate"
    )
    assert mission["checkpoint"]["candidate_projection_hash"] == artifact_ref
