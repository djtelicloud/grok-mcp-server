"""Deterministic CommitDone verifier. No generative calls. Candidate ≠ evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from unigrok_public.harness import is_nonanswer_completion
from unigrok_public.state import redact_secrets

from .artifacts import sealed_content_hash
from .evidence import (
    EVIDENCE_HUMAN,
    EVIDENCE_STRUCTURAL,
    EvidencePolicy,
    candidate_is_forbidden_evidence,
)
from .task_class import (
    TASK_CLASSES,
    VERIFICATION_INDEPENDENT,
    VERIFICATION_MODES,
    assign_task_class,
    assign_verification_mode,
    literal_commit_ready,
    matches_literal,
)

_TERM = re.compile(r"[A-Za-z0-9_]{3,}")
@dataclass(frozen=True)
class VerifyInput:
    candidate_text: str
    candidate_hash: str
    acceptance_text: str
    evidence_records: list[dict[str, Any]]
    policy: EvidencePolicy
    lease_generation: int
    expected_lease_generation: int
    status: str
    destructive: bool = False
    security_veto: bool = False
    qa_veto: bool = False
    task_text: str = ""
    frozen_task_class: str | None = None
    frozen_verification_mode: str | None = None
    candidate_artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    gaps: list[str]
    structural_record: dict[str, Any] | None
    task_class: str = "substantial"
    verification_mode: str = VERIFICATION_INDEPENDENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "gaps": list(self.gaps),
            "structural_record": self.structural_record,
            "task_class": self.task_class,
            "verification_mode": self.verification_mode,
        }


def _significant_terms(text: str, *, limit: int = 24) -> list[str]:
    seen: dict[str, None] = {}
    for match in _TERM.findall(text.lower()):
        if match in seen:
            continue
        seen[match] = None
        if len(seen) >= limit:
            break
    return list(seen)


def _checklist_item_count(answer: str) -> int:
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    bullets = [
        line for line in lines if re.match(r"^([-*•]|\d+[.)])\s+\S", line)
    ]
    if bullets:
        return len(bullets)
    if len(lines) >= 3:
        return len(lines)
    parts = re.split(r"[,;]", answer)
    return sum(1 for part in parts if len(part.strip()) >= 4)


def _structural_gaps(acceptance: str, candidate: str) -> list[str]:
    gaps: list[str] = []
    answer = candidate.strip()
    acceptance_clean = redact_secrets(acceptance).strip()
    if not answer:
        gaps.append("empty_answer")
    if answer and is_nonanswer_completion(answer, prompt=acceptance_clean):
        gaps.append("nonanswer_completion")
    words = answer.split()
    if answer and len(words) < 8:
        gaps.append("answer_too_short")
    terms = _significant_terms(acceptance_clean)
    acceptance_l = acceptance_clean.lower()
    stop = {
        "return",
        "provide",
        "include",
        "including",
        "with",
        "the",
        "and",
        "for",
        "from",
        "that",
        "this",
        "your",
        "into",
    }
    structured_ok = False
    if "checklist" in acceptance_l or "steps" in acceptance_l:
        items = _checklist_item_count(answer)
        if items < 3:
            gaps.append(f"checklist_too_thin:{items}")
        elif len(answer) < 80:
            gaps.append("checklist_too_short")
        else:
            structured_ok = True
            distinctive = [term for term in terms if term not in stop]
            if distinctive and not any(term in answer.lower() for term in distinctive):
                gaps.append("missing_key_term")
                structured_ok = False
    if terms and answer and not structured_ok:
        hit = sum(1 for term in terms if term in answer.lower())
        if len(terms) >= 8:
            need = max(3, len(terms) // 5)
        elif len(terms) >= 5:
            need = 2
        else:
            need = 0
        if need and hit < need:
            gaps.append(f"acceptance_coverage:{hit}/{need}")
        if len(words) <= 2 and len(terms) >= 3:
            gaps.append("token_echo")
    return gaps


def _literal_gaps(acceptance: str, candidate: str, *, expected: str | None) -> list[str]:
    """G_literal: empty / nonanswer / exact match only — no essay gates."""
    gaps: list[str] = []
    answer = candidate.strip()
    acceptance_clean = redact_secrets(acceptance).strip()
    if not answer:
        gaps.append("empty_answer")
        return gaps
    if is_nonanswer_completion(answer, prompt=acceptance_clean):
        gaps.append("nonanswer_completion")
        return gaps
    if not expected:
        gaps.append("literal_expected_missing")
        return gaps
    if not matches_literal(answer, expected):
        gaps.append("literal_mismatch")
    return gaps


def _record_digest(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# A1.5: gaps with no legal repair under the assigned class (never Continue).
_UNREPAIRABLE_GAPS = frozenset({"literal_expected_missing"})


def should_terminal_fail(
    *,
    gaps: list[str],
    task_class: str,
    candidate_hash: str,
    prior_verify_failures: int = 0,
    last_verify: dict[str, Any] | None = None,
) -> bool:
    """True when Continue cannot make progress (FailDone, not another quantum)."""
    gap_set = set(gaps or [])
    if gap_set & _UNREPAIRABLE_GAPS:
        return True
    if task_class not in {"literal", "echo_ok"}:
        return False
    if "literal_mismatch" not in gap_set:
        return False
    last = last_verify if isinstance(last_verify, dict) else {}
    last_gaps = set(last.get("gaps") or [])
    last_hash = str(last.get("candidate_hash") or "")
    # Same wrong candidate sealed again — no Lyapunov progress.
    if "literal_mismatch" in last_gaps and last_hash and last_hash == candidate_hash:
        return True
    # One repair quantum already spent on this literal mission.
    if int(prior_verify_failures or 0) >= 1:
        return True
    return False


def verify_commit(inp: VerifyInput) -> VerifyResult:
    gaps: list[str] = []
    if inp.frozen_task_class is None:
        task_class = assign_task_class(inp.task_text, inp.acceptance_text)
    elif inp.frozen_task_class in TASK_CLASSES:
        task_class = inp.frozen_task_class
    else:
        gaps.append("invalid_frozen_task_class")
        task_class = assign_task_class(inp.task_text, inp.acceptance_text)
    if inp.frozen_verification_mode is None:
        verification_mode = assign_verification_mode(
            inp.task_text,
            inp.acceptance_text,
            assigned_class=task_class,
            destructive=inp.destructive,
        )
    elif inp.frozen_verification_mode in VERIFICATION_MODES:
        verification_mode = inp.frozen_verification_mode
    else:
        # An invalid frozen control must never silently weaken verification.
        gaps.append("invalid_frozen_verification_mode")
        verification_mode = VERIFICATION_INDEPENDENT
    if str(inp.status) != "verifying":
        gaps.append("status_not_verifying")
    if int(inp.lease_generation) != int(inp.expected_lease_generation):
        gaps.append("stale_lease_generation")
    if inp.security_veto:
        gaps.append("security_veto")
    if inp.qa_veto:
        gaps.append("qa_veto")

    expected_hash = sealed_content_hash(inp.candidate_text, kind="candidate")
    if expected_hash != inp.candidate_hash:
        gaps.append("candidate_hash_mismatch")

    matched, expected_token, ready_class = literal_commit_ready(
        task=inp.task_text,
        acceptance=inp.acceptance_text,
        candidate=inp.candidate_text,
        assigned_class=task_class,
    )
    task_class = ready_class
    literal_path = task_class in {"literal", "echo_ok"} and expected_token is not None

    if literal_path:
        structural_gaps = _literal_gaps(
            inp.acceptance_text, inp.candidate_text, expected=expected_token
        )
    else:
        structural_gaps = _structural_gaps(inp.acceptance_text, inp.candidate_text)
    gaps.extend(structural_gaps)

    # Build structural evidence from verifier observations — never the answer body.
    structural_payload: dict[str, Any] = {
        "class": EVIDENCE_STRUCTURAL,
        "source": "deterministic_verifier",
        "candidate_hash": inp.candidate_hash,
        "structural_gaps": list(structural_gaps),
        "checks_passed": not structural_gaps,
        "task_class": task_class,
        "verification_mode": verification_mode,
    }
    if literal_path and matched and not structural_gaps:
        structural_payload["literal_match"] = True
        structural_payload["expected_token_hash"] = _record_digest(
            {"expected": expected_token}
        )

    # Reject smuggled candidate bodies inside purported structural payloads.
    for record in inp.evidence_records or []:
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict):
            continue
        blob = json.dumps(payload, default=str)
        if inp.candidate_text and len(inp.candidate_text) >= 40:
            if inp.candidate_text[:40] in blob:
                gaps.append("self_evidence_forbidden")
    structural_record = {
        "class": EVIDENCE_STRUCTURAL,
        "digest": _record_digest(structural_payload),
        "payload": structural_payload,
        "artifact_refs": [inp.candidate_hash],
    }

    records = list(inp.evidence_records or [])
    # If structural checks passed, the verifier-authored record counts.
    if not structural_gaps:
        records = [*records, structural_record]

    forbidden_refs = (inp.candidate_hash, *inp.candidate_artifact_refs)
    for record in records:
        if not isinstance(record, dict):
            gaps.append("invalid_evidence_record")
            continue
        if candidate_is_forbidden_evidence(record, forbidden_refs):
            gaps.append("self_evidence_forbidden")
        klass = str(record.get("class") or "")
        if not inp.policy.allows(klass):
            gaps.append(f"evidence_class_denied:{klass}")

    acceptable = [
        r
        for r in records
        if isinstance(r, dict)
        and inp.policy.allows(str(r.get("class") or ""))
        and not candidate_is_forbidden_evidence(r, forbidden_refs)
    ]
    independent = [
        r for r in acceptable if str(r.get("class") or "") != EVIDENCE_STRUCTURAL
    ]
    # Literal and ordinary output-generation paths may commit from deterministic
    # structural verification. Outcome-sensitive missions require a record that
    # existed outside the candidate answer.
    insufficient = len(acceptable) < inp.policy.min_records
    if verification_mode == VERIFICATION_INDEPENDENT and not independent:
        insufficient = True
    if insufficient:
        if not (literal_path and structural_gaps):
            gaps.append("insufficient_evidence")

    if inp.destructive and inp.policy.require_human_for_destructive:
        if not any(str(r.get("class")) == EVIDENCE_HUMAN for r in acceptable):
            gaps.append("human_approval_required")

    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for gap in gaps:
        if gap not in seen:
            seen.add(gap)
            uniq.append(gap)

    ok = not uniq
    return VerifyResult(
        ok=ok,
        gaps=uniq,
        structural_record=structural_record if not structural_gaps else None,
        task_class=task_class,
        verification_mode=verification_mode,
    )
