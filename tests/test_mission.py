"""Mission controller v2: truth spine, leases, verifying without self-evidence."""

from __future__ import annotations

import pytest

from unigrok_public.mission.artifacts import artifact_projection, sealed_content_hash
from unigrok_public.mission.evidence import default_agent_policy
from unigrok_public.mission.governor import shadow_recommend
from unigrok_public.mission.task_class import (
    assign_task_class,
    extract_literal_acceptance,
)
from unigrok_public.mission.types import MissionStatus, legal_transition
from unigrok_public.mission.verify import (
    VerifyInput,
    should_terminal_fail,
    verify_commit,
)
from unigrok_public.mission.voters import ShadowVoterExecutor, build_slots, merge_scorecards
from unigrok_public.state import PublicStateStore


def test_sealed_hash_ignores_projection_truncation() -> None:
    raw = "secret xai-testkey1234567890 " + ("word " * 5_000)
    digest = sealed_content_hash(raw, kind="candidate")
    proj = artifact_projection(raw, max_bytes=200)
    assert "xai-testkey" not in proj or "[REDACTED" in proj or "REDACTED" in proj
    assert digest == sealed_content_hash(raw, kind="candidate")
    assert digest != sealed_content_hash(proj, kind="candidate")


def test_complete_only_from_verifying() -> None:
    assert legal_transition(MissionStatus.VERIFYING, MissionStatus.COMPLETE)
    assert not legal_transition(MissionStatus.RUNNING, MissionStatus.COMPLETE)
    assert not legal_transition(MissionStatus.COMPLETE, MissionStatus.RUNNING)
    # Host continue demote + resume (CAS hard-loop fix).
    assert legal_transition(MissionStatus.VERIFYING, MissionStatus.WAITING_EVENT)
    assert legal_transition(MissionStatus.WAITING_EVENT, MissionStatus.RUNNING)
    assert not legal_transition(MissionStatus.VERIFYING, MissionStatus.VERIFYING)


def test_verify_rejects_self_evidence_and_wrong_status() -> None:
    text = (
        "- Build the container image\n"
        "- Run database migrations\n"
        "- Smoke test /healthz\n"
        "- Flip traffic to the new revision\n"
    )
    digest = sealed_content_hash(text, kind="candidate")
    bad = verify_commit(
        VerifyInput(
            candidate_text=text,
            candidate_hash=digest,
            acceptance_text="Return a checklist of deploy steps including healthz",
            evidence_records=[
                {
                    "class": "caller_evidence",
                    "digest": "x",
                    "artifact_refs": [digest],
                }
            ],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="running",
        )
    )
    assert bad.ok is False
    assert "status_not_verifying" in bad.gaps

    good = verify_commit(
        VerifyInput(
            candidate_text=text,
            candidate_hash=digest,
            acceptance_text="Return a checklist of deploy steps including healthz",
            evidence_records=[],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="verifying",
        )
    )
    assert good.ok is True
    assert good.structural_record is not None
    assert good.structural_record["class"] == "structural"


def test_governor_clamps_to_ceiling() -> None:
    cfg = shadow_recommend(uncertainty=0.99, risk=0.99, level_ceiling="medium")
    assert cfg.reasoning_level in {"none", "minimal", "low", "medium"}
    assert cfg.shadow is True


def test_governor_escalates_adversarial_concurrency_review() -> None:
    """Risk classifier must not under-escalate concurrency/security audits."""
    from unigrok_public.mission.governor import WEIGHT_BUNDLE_VERSION, recommend_for_task

    task = (
        "Adversarial review of a Python durable-job controller with lease races, "
        "stale writes, dishonest polling, self-verification, secret leakage, "
        "and production retention faults. Issue a NO-SHIP verdict if needed."
    )
    cfg = recommend_for_task(task, level_ceiling="ultra")
    assert "concurrency" in cfg.signals
    assert "security" in cfg.signals or "adversarial_review" in cfg.signals
    assert cfg.reasoning_level in {"high", "xhigh", "max", "ultra"}
    roles = set(cfg.voter_roles)
    assert {"engineer", "architect", "qa", "security"}.issubset(roles)
    assert cfg.candidate_count >= 2
    assert cfg.critique_rounds >= 1
    assert cfg.verification_depth == "strict"
    assert cfg.shadow is True
    # Bundle version is a contract field — not a specific weight value.
    assert cfg.weight_bundle_version == WEIGHT_BUNDLE_VERSION


def test_governor_stays_cheap_for_mechanical_task() -> None:
    from unigrok_public.mission.governor import recommend_for_task

    cfg = recommend_for_task("Rename the variable foo to bar in one file.")
    assert cfg.reasoning_level in {"none", "minimal", "low", "medium"}
    assert cfg.voter_roles in {("engineer",), ("engineer", "qa")}


@pytest.mark.asyncio
async def test_shadow_voters_do_not_block_merge_schema() -> None:
    slots = build_slots(("engineer", "qa", "security"))
    ex = ShadowVoterExecutor()
    cards = [await ex.invoke(s, task="t", draft="x" * 80) for s in slots]
    merged = merge_scorecards(cards)
    assert "action" in merged
    assert merged.get("shadow") is True


@pytest.mark.asyncio
async def test_mission_cas_and_lease_fence(tmp_path) -> None:  # noqa: ANN001
    store = PublicStateStore(tmp_path / "m.db")
    await store.initialize()
    from unigrok_public.mission.lease import lease_expiry_iso

    await store.create_mission(
        "msn_1",
        job_id="job_1",
        acceptance_hash="abc",
        acceptance_text="Return a checklist of deploy steps including healthz",
        continue_token="a" * 32,
        package={"evidence_policy": default_agent_policy().to_dict()},
        lease_token="tok_a",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )
    ok = await store.cas_mission_status(
        "msn_1",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        new_status="verifying",
    )
    assert ok is True
    # Stale generation cannot complete.
    stale = await store.cas_mission_status(
        "msn_1",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=99,
        new_status="complete",
        clear_lease=True,
    )
    assert stale is False
    done = await store.cas_mission_status(
        "msn_1",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        new_status="complete",
        clear_lease=True,
    )
    assert done is True
    row = await store.load_mission("msn_1")
    assert row is not None
    assert row["status"] == "complete"
    # Terminal demotion blocked.
    demote = await store.cas_mission_status(
        "msn_1",
        expect_status="complete",
        expect_version=2,
        expect_lease_generation=1,
        new_status="running",
    )
    assert demote is False


@pytest.mark.asyncio
async def test_claim_refuses_steal_mid_verifying(tmp_path) -> None:  # noqa: ANN001
    """P0: reattach must not bump lease_generation while verifying + active lease."""
    store = PublicStateStore(tmp_path / "m_steal.db")
    await store.initialize()
    from unigrok_public.mission.lease import lease_expiry_iso

    await store.create_mission(
        "msn_steal",
        job_id="job_steal",
        acceptance_hash="steal",
        acceptance_text="Return a checklist of deploy steps including healthz",
        continue_token="c" * 32,
        package={"evidence_policy": default_agent_policy().to_dict()},
        lease_token="tok_owner",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=120),
    )
    assert await store.cas_mission_status(
        "msn_steal",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        new_status="verifying",
    )
    stole, gen = await store.claim_mission(
        "msn_steal",
        lease_token="tok_reattach",  # noqa: S106
        ttl_seconds=120,
    )
    assert stole is False
    assert gen == 1
    row = await store.load_mission("msn_steal")
    assert row is not None
    assert row["status"] == "verifying"
    assert int(row["lease_generation"]) == 1
    assert row["lease_token"] == "tok_owner"  # noqa: S105
    # Owner can still CommitDone.
    assert await store.cas_mission_status(
        "msn_steal",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        new_status="complete",
        clear_lease=True,
    )


@pytest.mark.asyncio
async def test_seal_self_heals_sticky_verifying(tmp_path) -> None:  # noqa: ANN001
    """Sticky verifying + correct candidate must CommitDone (no verifying→verifying)."""
    from unigrok_public.mission.epoch import seal_mission_epoch
    from unigrok_public.mission.lease import lease_expiry_iso

    store = PublicStateStore(tmp_path / "m_sticky.db")
    await store.initialize()
    acceptance = (
        "- Build the container image\n"
        "- Run database migrations\n"
        "- Smoke test /healthz\n"
        "- Flip traffic to the new revision\n"
    )
    await store.create_mission(
        "msn_sticky",
        job_id="job_sticky",
        acceptance_hash="sticky",
        acceptance_text="Return a checklist of deploy steps including healthz",
        continue_token="d" * 32,
        package={
            "task": "Return a checklist of deploy steps including healthz",
            "acceptance": "Return a checklist of deploy steps including healthz",
            "evidence_policy": default_agent_policy().to_dict(),
        },
        lease_token="tok_sticky",  # noqa: S106
        lease_generation=2,
        lease_expires_at=lease_expiry_iso(ttl_seconds=120),
    )
    assert await store.cas_mission_status(
        "msn_sticky",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=2,
        new_status="verifying",
    )
    out = await seal_mission_epoch(
        store,
        mission_id="msn_sticky",
        job_id="job_sticky",
        acceptance_text="Return a checklist of deploy steps including healthz",
        result={"text": acceptance, "model": "test"},
        lease_generation=2,
        continue_token="d" * 32,
        envelope_version=1,
        shadow_cognition=False,
    )
    assert out.get("mission", {}).get("committed") is True
    assert out.get("status") == "complete"
    row = await store.load_mission("msn_sticky")
    assert row is not None
    assert row["status"] == "complete"


@pytest.mark.asyncio
async def test_verifying_can_demote_to_waiting_event(tmp_path) -> None:  # noqa: ANN001
    store = PublicStateStore(tmp_path / "m_wait.db")
    await store.initialize()
    from unigrok_public.mission.lease import lease_expiry_iso

    await store.create_mission(
        "msn_wait",
        job_id="job_wait",
        acceptance_hash="wait",
        acceptance_text="task",
        continue_token="e" * 32,
        package={},
        lease_token="tok_wait",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )
    assert await store.cas_mission_status(
        "msn_wait",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        new_status="verifying",
    )
    assert await store.cas_mission_status(
        "msn_wait",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        new_status="waiting_event",
        clear_lease=True,
    )
    row = await store.load_mission("msn_wait")
    assert row is not None
    assert row["status"] == "waiting_event"


@pytest.mark.asyncio
async def test_side_effect_idempotent(tmp_path) -> None:  # noqa: ANN001
    store = PublicStateStore(tmp_path / "m2.db")
    await store.initialize()
    from unigrok_public.mission.lease import lease_expiry_iso

    await store.create_mission(
        "msn_2",
        job_id="job_2",
        acceptance_hash="def",
        acceptance_text="task",
        continue_token="b" * 32,
        package={},
        lease_token="tok_b",  # noqa: S106
        lease_generation=1,
        lease_expires_at=lease_expiry_iso(ttl_seconds=60),
    )
    key = "msn_2:1:0:tool"
    assert await store.put_mission_side_effect(
        key, mission_id="msn_2", receipt={"ok": True}, lease_generation=1
    )
    assert await store.put_mission_side_effect(
        key, mission_id="msn_2", receipt={"ok": True}, lease_generation=1
    )
    assert not await store.put_mission_side_effect(
        key + ":other",
        mission_id="msn_2",
        receipt={"ok": True},
        lease_generation=9,
    )


def test_assign_literal_for_exact_probe() -> None:
    acceptance = "Reply with exactly MCP_LIVE_OK"
    assert extract_literal_acceptance(acceptance) == "MCP_LIVE_OK"
    assert assign_task_class(acceptance, acceptance) == "literal"
    # Colon after exactly must not capture "exactly:" as the token.
    colon = "Reply with exactly: MCP_LIVE_OK"
    assert extract_literal_acceptance(colon) == "MCP_LIVE_OK"
    assert assign_task_class(colon, colon) == "literal"
    assert assign_task_class(
        "Return a checklist of deploy steps including healthz",
        "Return a checklist of deploy steps including healthz",
    ) == "substantial"


def test_assign_adversarial_outranks_literal_token() -> None:
    text = "Adversarial security review; reply with exactly NO_SHIP"
    assert assign_task_class(text, text) == "adversarial"


def test_assign_dual_intent_stays_substantial() -> None:
    text = "Apply the fix, then reply with exactly MCP_LIVE_OK"
    assert assign_task_class(text, text) == "substantial"
    assert extract_literal_acceptance(text) == "MCP_LIVE_OK"


def test_api_docs_return_x_is_not_literal() -> None:
    text = "The function should return null on error"
    assert extract_literal_acceptance(text) is None
    assert assign_task_class(text, text) == "substantial"


def test_should_terminal_fail_literal_mismatch_after_one_retry() -> None:
    digest = "abc"
    assert (
        should_terminal_fail(
            gaps=["literal_mismatch"],
            task_class="literal",
            candidate_hash=digest,
            prior_verify_failures=0,
        )
        is False
    )
    assert (
        should_terminal_fail(
            gaps=["literal_mismatch"],
            task_class="literal",
            candidate_hash=digest,
            prior_verify_failures=1,
        )
        is True
    )
    assert (
        should_terminal_fail(
            gaps=["literal_mismatch"],
            task_class="literal",
            candidate_hash=digest,
            prior_verify_failures=0,
            last_verify={"gaps": ["literal_mismatch"], "candidate_hash": digest},
        )
        is True
    )
    assert (
        should_terminal_fail(
            gaps=["answer_too_short"],
            task_class="substantial",
            candidate_hash=digest,
            prior_verify_failures=5,
        )
        is False
    )


@pytest.mark.parametrize(
    "acceptance",
    [
        "Reply with exactly MCP_LIVE_OK",
        "Reply with exactly: MCP_LIVE_OK",
        "Reply with exactly: MCP_LIVE_OK.",
    ],
)
def test_verify_literal_exact_match_commits(acceptance: str) -> None:
    text = "MCP_LIVE_OK"
    digest = sealed_content_hash(text, kind="candidate")
    result = verify_commit(
        VerifyInput(
            candidate_text=text,
            candidate_hash=digest,
            acceptance_text=acceptance,
            task_text=acceptance,
            evidence_records=[],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="verifying",
        )
    )
    assert result.ok is True
    assert result.task_class == "literal"
    assert result.gaps == []
    assert result.structural_record is not None
    assert result.structural_record["payload"].get("literal_match") is True


def test_verify_literal_mismatch_skips_essay_gates() -> None:
    text = "WRONG"
    digest = sealed_content_hash(text, kind="candidate")
    result = verify_commit(
        VerifyInput(
            candidate_text=text,
            candidate_hash=digest,
            acceptance_text="Reply with exactly MCP_LIVE_OK",
            evidence_records=[],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="verifying",
        )
    )
    assert result.ok is False
    assert "literal_mismatch" in result.gaps
    assert "answer_too_short" not in result.gaps
    assert "token_echo" not in result.gaps
    assert "insufficient_evidence" not in result.gaps


def test_verify_checklist_still_rejects_short_echo() -> None:
    text = "healthz"
    digest = sealed_content_hash(text, kind="candidate")
    result = verify_commit(
        VerifyInput(
            candidate_text=text,
            candidate_hash=digest,
            acceptance_text="Return a checklist of deploy steps including healthz",
            evidence_records=[],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="verifying",
        )
    )
    assert result.ok is False
    assert any(
        gap.startswith("checklist_") or gap in {"answer_too_short", "token_echo"}
        for gap in result.gaps
    )
