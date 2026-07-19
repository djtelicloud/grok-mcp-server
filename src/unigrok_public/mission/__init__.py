"""Durable adaptive mission controller (feature-flagged).

Phases A–C: truth spine, fenced leases, verifying CommitDone.
Phases D–E: voter/governor interfaces in shadow by default.
"""

from __future__ import annotations

from .artifacts import (
    PROJECTION_MAX_BYTES,
    artifact_projection,
    sealed_content_hash,
)
from .evidence import (
    EVIDENCE_CALLER,
    EVIDENCE_HUMAN,
    EVIDENCE_RUNTIME,
    EVIDENCE_STRUCTURAL,
    EvidencePolicy,
    default_agent_policy,
)
from .governor import (
    WEIGHT_BUNDLE_VERSION,
    GovernorConfig,
    classify_task_signals,
    recommend_for_task,
    shadow_recommend,
)
from .physics import NEEDLE_RUNTIME_DEFAULT
from .task_class import (
    assign_task_class,
    extract_literal_acceptance,
    literal_commit_ready,
    matches_literal,
)
from .types import (
    TERMINAL_STATUSES,
    MissionStatus,
    legal_transition,
)
from .verify import VerifyInput, VerifyResult, should_terminal_fail, verify_commit

__all__ = [
    "PROJECTION_MAX_BYTES",
    "EVIDENCE_CALLER",
    "EVIDENCE_HUMAN",
    "EVIDENCE_RUNTIME",
    "EVIDENCE_STRUCTURAL",
    "EvidencePolicy",
    "NEEDLE_RUNTIME_DEFAULT",
    "WEIGHT_BUNDLE_VERSION",
    "GovernorConfig",
    "MissionStatus",
    "TERMINAL_STATUSES",
    "VerifyInput",
    "VerifyResult",
    "artifact_projection",
    "assign_task_class",
    "classify_task_signals",
    "default_agent_policy",
    "extract_literal_acceptance",
    "legal_transition",
    "literal_commit_ready",
    "matches_literal",
    "recommend_for_task",
    "sealed_content_hash",
    "shadow_recommend",
    "should_terminal_fail",
    "verify_commit",
]
