"""Adversarial regressions for mission evidence and literal CommitDone."""

from __future__ import annotations

import pytest

import unigrok_public.mission.verify as verify_module
from unigrok_public.mission.artifacts import sealed_content_hash
from unigrok_public.mission.evidence import (
    EVIDENCE_CALLER,
    EVIDENCE_HUMAN,
    EVIDENCE_RUNTIME,
    EVIDENCE_STRUCTURAL,
    candidate_is_forbidden_evidence,
    default_agent_policy,
)
from unigrok_public.mission.task_class import (
    assign_task_class,
    assign_verification_mode,
    extract_literal_acceptance,
)
from unigrok_public.mission.verify import VerifyInput, verify_commit


def _verify(
    candidate: str,
    acceptance: str,
    *,
    task: str | None = None,
    evidence: list[dict[str, object]] | None = None,
    frozen_task_class: str | None = None,
    frozen_verification_mode: str | None = None,
    candidate_artifact_refs: tuple[str, ...] = (),
    destructive: bool = False,
):
    digest = sealed_content_hash(candidate, kind="candidate")
    return verify_commit(
        VerifyInput(
            candidate_text=candidate,
            candidate_hash=digest,
            acceptance_text=acceptance,
            task_text=task if task is not None else acceptance,
            evidence_records=evidence or [],
            policy=default_agent_policy(),
            lease_generation=1,
            expected_lease_generation=1,
            status="verifying",
            destructive=destructive,
            frozen_task_class=frozen_task_class,
            frozen_verification_mode=frozen_verification_mode,
            candidate_artifact_refs=candidate_artifact_refs,
        )
    )


@pytest.mark.parametrize(
    "klass", [EVIDENCE_RUNTIME, EVIDENCE_CALLER, EVIDENCE_HUMAN]
)
@pytest.mark.parametrize(
    "location", ["digest", "candidate_hash", "artifact_refs", "payload"]
)
def test_all_external_evidence_rejects_candidate_digest_references(
    klass: str, location: str
) -> None:
    candidate_hash = "a" * 64
    record: dict[str, object] = {
        "class": klass,
        "digest": "b" * 64,
        "artifact_refs": ["runtime-log-42"],
        "payload": {"observation": "independent"},
    }
    if location == "digest":
        record["digest"] = candidate_hash
    elif location == "candidate_hash":
        record["candidate_hash"] = candidate_hash
    elif location == "artifact_refs":
        record["artifact_refs"] = [candidate_hash]
    else:
        record["payload"] = {"nested": {"candidate_hash": candidate_hash}}

    assert candidate_is_forbidden_evidence(record, candidate_hash) is True


def test_structural_verifier_may_reference_candidate_it_checked() -> None:
    candidate_hash = "a" * 64
    record = {
        "class": EVIDENCE_STRUCTURAL,
        "digest": "b" * 64,
        "artifact_refs": [candidate_hash],
        "payload": {"candidate_hash": candidate_hash},
    }
    assert candidate_is_forbidden_evidence(record, candidate_hash) is False


def test_structural_verification_completes_ordinary_information_task() -> None:
    task = "Explain the staged deployment and rollback plan."
    acceptance = (
        "Explain staged deployment, database migrations, monitored health checks, "
        "and the rollback window."
    )
    candidate = (
        "The staged deployment uses database migrations, monitored health checks, "
        "and a carefully controlled rollback window."
    )
    result = _verify(candidate, acceptance, task=task)

    assert result.task_class == "substantial"
    assert result.verification_mode == "structural"
    assert result.structural_record is not None
    assert result.ok is True
    assert result.gaps == []


def test_generated_adversarial_review_can_commit_structurally() -> None:
    task = "Perform an adversarial security review of lease fencing."
    acceptance = (
        "Provide an adversarial security review of lease fencing under stale-worker "
        "races and deployment risk."
    )
    candidate = (
        "The adversarial security review finds lease fencing sound under stale-worker "
        "races and recommends a monitored deployment."
    )
    result = _verify(candidate, acceptance, task=task)

    assert result.task_class == "adversarial"
    assert result.verification_mode == "structural"
    assert result.structural_record is not None
    assert result.ok is True
    assert result.gaps == []


def test_independent_caller_evidence_can_verify_substantial_claim() -> None:
    task = "Run the deployment rehearsal and prove it passed with test evidence."
    acceptance = (
        "Confirm the deployment rehearsal passed using monitored health checks and "
        "the rollback window."
    )
    candidate = (
        "The staged deployment uses database migrations, monitored health checks, "
        "and a carefully controlled rollback window."
    )
    evidence = [
        {
            "class": EVIDENCE_CALLER,
            "digest": "b" * 64,
            "artifact_refs": ["runtime-log-42"],
            "payload": {"observation": "deployment rehearsal passed"},
        }
    ]

    result = _verify(candidate, acceptance, task=task, evidence=evidence)

    assert result.ok is True
    assert result.verification_mode == "independent_evidence"
    assert result.gaps == []


def test_candidate_digest_disqualifies_purported_caller_evidence() -> None:
    task = "Run the deployment rehearsal and prove it passed with test evidence."
    acceptance = (
        "Explain staged deployment, database migrations, monitored health checks, "
        "and the rollback window."
    )
    candidate = (
        "The staged deployment uses database migrations, monitored health checks, "
        "and a carefully controlled rollback window."
    )
    candidate_hash = sealed_content_hash(candidate, kind="candidate")
    evidence = [
        {
            "class": EVIDENCE_CALLER,
            "digest": candidate_hash,
            "artifact_refs": [],
            "payload": {"observation": "candidate says it passed"},
        }
    ]

    result = _verify(candidate, acceptance, task=task, evidence=evidence)

    assert result.ok is False
    assert "self_evidence_forbidden" in result.gaps
    assert "insufficient_evidence" in result.gaps


def test_projection_artifact_disqualifies_purported_caller_evidence() -> None:
    task = "Run the deployment rehearsal and prove it passed with test evidence."
    acceptance = "Confirm the deployment rehearsal passed using independent evidence."
    candidate = (
        "The deployment rehearsal completed with monitored health checks and a "
        "controlled rollback window."
    )
    projection_hash = "c" * 64
    evidence = [
        {
            "class": EVIDENCE_CALLER,
            "digest": "d" * 64,
            "artifact_refs": [projection_hash],
            "payload": {"observation": "the candidate projection says it passed"},
        }
    ]

    result = _verify(
        candidate,
        acceptance,
        task=task,
        evidence=evidence,
        candidate_artifact_refs=(projection_hash,),
    )

    assert result.ok is False
    assert "self_evidence_forbidden" in result.gaps
    assert "insufficient_evidence" in result.gaps


@pytest.mark.parametrize(
    ("acceptance", "expected"),
    [
        ("Reply with exactly OK", "OK"),
        ("Reply with exactly YES", "YES"),
        ("Reply with exactly OK/YES", "OK/YES"),
    ],
)
def test_exact_output_commands_accept_exact_answer(
    acceptance: str, expected: str
) -> None:
    assert extract_literal_acceptance(acceptance) == expected
    assert assign_task_class(acceptance, acceptance) == "literal"

    result = _verify(expected, acceptance)

    assert result.ok is True
    assert result.task_class == "literal"
    assert result.gaps == []


@pytest.mark.parametrize("candidate", ["OK with extra detail", "OK.", '"OK"'])
def test_exact_output_command_rejects_non_exact_answer(candidate: str) -> None:
    result = _verify(candidate, "Reply with exactly OK")

    assert result.ok is False
    assert result.task_class == "literal"
    assert "literal_mismatch" in result.gaps
    assert "insufficient_evidence" not in result.gaps


@pytest.mark.parametrize(
    "text",
    [
        "The proposed explanation is exactly correct and covers the relevant detail.",
        "Make the deployment explanation exactly correct before publishing it.",
    ],
)
def test_incidental_exactly_correct_prose_is_not_literal(text: str) -> None:
    assert extract_literal_acceptance(text) is None
    assert assign_task_class(text, text) == "substantial"


def test_valid_frozen_task_class_overrides_reclassification() -> None:
    result = _verify(
        "OK",
        "Reply with exactly OK",
        task="Perform an adversarial security review, then report the result.",
        frozen_task_class="literal",
    )

    assert result.ok is True
    assert result.task_class == "literal"


def test_valid_frozen_task_class_skips_reclassification(monkeypatch) -> None:  # noqa: ANN001
    def fail_reclassification(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("frozen task class must be used directly")

    monkeypatch.setattr(verify_module, "assign_task_class", fail_reclassification)

    result = _verify(
        "OK",
        "Reply with exactly OK",
        frozen_task_class="literal",
    )

    assert result.ok is True
    assert result.task_class == "literal"


def test_invalid_frozen_task_class_fails_closed() -> None:
    result = _verify(
        "OK",
        "Reply with exactly OK",
        frozen_task_class="invented",
    )

    assert result.ok is False
    assert result.task_class == "literal"
    assert "invalid_frozen_task_class" in result.gaps


@pytest.mark.parametrize(
    ("task", "acceptance"),
    [
        ("Run the test suite and report whether it passed.", "Tests must pass."),
        ("Prove the migration succeeds.", "Provide a verified migration result."),
        ("Check the live runtime and show evidence it is healthy.", "Runtime is healthy."),
        (
            "Run an adversarial penetration test and prove it passed.",
            "Provide penetration-test evidence.",
        ),
    ],
)
def test_outcome_semantics_require_independent_evidence(
    task: str, acceptance: str
) -> None:
    assert assign_verification_mode(task, acceptance) == "independent_evidence"


@pytest.mark.parametrize(
    ("task", "acceptance"),
    [
        ("Explain the runtime architecture.", "Describe the runtime architecture."),
        ("Write unit tests for this parser.", "Return Python unit tests."),
        ("Draft a plan for how to verify deployment.", "Provide verification steps."),
        ("Summarize these test results.", "Return a concise summary."),
        ("Write a threat model for this architecture.", "Return the threat model."),
        ("Perform a security review of this patch.", "Find security failures."),
    ],
)
def test_generation_and_information_use_structural_verification(
    task: str, acceptance: str
) -> None:
    assert assign_verification_mode(task, acceptance) == "structural"


def test_destructive_flag_requires_independent_human_evidence() -> None:
    result = _verify(
        "The requested destructive operation has safely completed with rollback ready.",
        "Complete the destructive operation with rollback ready.",
        destructive=True,
    )

    assert result.verification_mode == "independent_evidence"
    assert "insufficient_evidence" in result.gaps
    assert "human_approval_required" in result.gaps


def test_invalid_frozen_verification_mode_fails_closed() -> None:
    result = _verify(
        "This ordinary generated explanation is long enough for structural verification.",
        "Provide an ordinary generated explanation with enough useful detail.",
        frozen_verification_mode="trust_me",
    )

    assert result.verification_mode == "independent_evidence"
    assert "invalid_frozen_verification_mode" in result.gaps
    assert "insufficient_evidence" in result.gaps


def test_valid_frozen_verification_mode_skips_reclassification(monkeypatch) -> None:
    def fail_reclassification(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("frozen verification mode must be used directly")

    monkeypatch.setattr(
        verify_module, "assign_verification_mode", fail_reclassification
    )
    result = _verify(
        "This ordinary generated explanation is long enough for structural verification.",
        "Provide an ordinary generated explanation with enough useful detail.",
        frozen_verification_mode="structural",
    )

    assert result.ok is True
    assert result.verification_mode == "structural"
