"""Typed evidence records. Candidate answer text is never evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

EVIDENCE_STRUCTURAL = "structural"
EVIDENCE_RUNTIME = "runtime_observation"
EVIDENCE_CALLER = "caller_evidence"
EVIDENCE_HUMAN = "human_approval"

ALL_EVIDENCE_CLASSES = frozenset(
    {
        EVIDENCE_STRUCTURAL,
        EVIDENCE_RUNTIME,
        EVIDENCE_CALLER,
        EVIDENCE_HUMAN,
    }
)


@dataclass(frozen=True)
class EvidencePolicy:
    """Which evidence classes may satisfy CommitDone for a mission."""

    allowed_classes: frozenset[str] = field(
        default_factory=lambda: frozenset({EVIDENCE_STRUCTURAL, EVIDENCE_CALLER})
    )
    require_human_for_destructive: bool = True
    min_records: int = 1

    def allows(self, klass: str) -> bool:
        return klass in self.allowed_classes and klass in ALL_EVIDENCE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_classes": sorted(self.allowed_classes),
            "require_human_for_destructive": self.require_human_for_destructive,
            "min_records": self.min_records,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EvidencePolicy:
        if not data:
            return default_agent_policy()
        classes = frozenset(str(x) for x in (data.get("allowed_classes") or []))
        classes = classes & ALL_EVIDENCE_CLASSES
        if not classes:
            classes = frozenset({EVIDENCE_STRUCTURAL, EVIDENCE_CALLER})
        return cls(
            allowed_classes=classes,
            require_human_for_destructive=bool(
                data.get("require_human_for_destructive", True)
            ),
            min_records=max(1, int(data.get("min_records") or 1)),
        )


def default_agent_policy() -> EvidencePolicy:
    """Text missions accept verifier, runtime, caller, and human provenance."""
    return EvidencePolicy(
        allowed_classes=frozenset(
            {EVIDENCE_STRUCTURAL, EVIDENCE_RUNTIME, EVIDENCE_CALLER, EVIDENCE_HUMAN}
        ),
        require_human_for_destructive=True,
        min_records=1,
    )


def candidate_is_forbidden_evidence(
    record: dict[str, Any], candidate_refs: str | Iterable[str]
) -> bool:
    """True when a record tries to use the candidate artifact as its own proof."""
    refs = (
        (candidate_refs,)
        if isinstance(candidate_refs, str)
        else tuple(str(value) for value in candidate_refs)
    )
    needles = {value.casefold() for value in refs if value}
    if not isinstance(record, dict) or not needles:
        return False
    if str(record.get("class") or "") == EVIDENCE_STRUCTURAL:
        # Structural evidence may *reference* the candidate hash it checked.
        return False

    def contains_candidate(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                contains_candidate(key) or contains_candidate(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(contains_candidate(item) for item in value)
        if isinstance(value, str):
            folded = value.casefold()
            return any(needle in folded for needle in needles)
        return False

    # External proof must be independently addressable. A candidate digest in
    # any field (including digest, artifact refs, or nested payload) makes it
    # self-referential.
    return contains_candidate(record)
