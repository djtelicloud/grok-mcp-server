"""Typed voter council: slots + executor interface. Shadow-safe by default."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

ROLE_BRIEF: dict[str, str] = {
    "architect": "Boundaries, maintainability, reversibility.",
    "pm": "Sequencing, dependencies, scope, budget.",
    "engineer": "Implementation correctness and simplicity.",
    "qa": "Evidence, recovery, operability; veto unsupported completion.",
    "security": "Secret safety, abuse, attack surface; veto unsafe execution.",
    "product": "User value and honest acceptance.",
    "perf": "Latency and resource efficiency.",
}


@dataclass(frozen=True)
class VoterSlot:
    role_id: str
    brief: str
    budget_tokens: int = 256
    timeout_s: float = 30.0
    runtime_binding: str = "default"


@dataclass
class Scorecard:
    role_id: str
    recommended_action: str
    assumptions: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    p_success: float = 0.5
    evidence_refs_needed: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    reversibility: str = "unknown"
    time_compute_estimate: str = ""
    confidence: float = 0.0
    verification_method: str = ""
    hard_veto: bool = False
    veto_reason: str = ""
    valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "recommended_action": self.recommended_action,
            "assumptions": list(self.assumptions),
            "expected_outcome": self.expected_outcome,
            "p_success": self.p_success,
            "evidence_refs_needed": list(self.evidence_refs_needed),
            "risks": list(self.risks),
            "reversibility": self.reversibility,
            "time_compute_estimate": self.time_compute_estimate,
            "confidence": self.confidence,
            "verification_method": self.verification_method,
            "hard_veto": self.hard_veto,
            "veto_reason": self.veto_reason,
            "valid": self.valid,
        }


class VoterExecutor(Protocol):
    async def invoke(self, slot: VoterSlot, *, task: str, draft: str) -> Scorecard:
        """Run one voter slot in an isolated context."""


def build_slots(role_ids: tuple[str, ...] | list[str], *, cap: int = 7) -> list[VoterSlot]:
    slots: list[VoterSlot] = []
    for role in list(role_ids)[: max(1, min(int(cap), 7))]:
        rid = str(role).strip().lower()
        if rid not in ROLE_BRIEF:
            continue
        slots.append(
            VoterSlot(
                role_id=rid,
                brief=ROLE_BRIEF[rid],
                runtime_binding="default",
            )
        )
    if not slots:
        slots.append(
            VoterSlot(role_id="engineer", brief=ROLE_BRIEF["engineer"])
        )
    return slots


class ShadowVoterExecutor:
    """Deterministic local stub — records scorecards without model calls."""

    async def invoke(self, slot: VoterSlot, *, task: str, draft: str) -> Scorecard:
        _ = task
        thin = len((draft or "").strip()) < 40
        veto = slot.role_id in {"qa", "security"} and thin
        return Scorecard(
            role_id=slot.role_id,
            recommended_action="continue" if thin else "propose_done",
            assumptions=["shadow_executor"],
            expected_outcome="shadow_only",
            p_success=0.4 if thin else 0.7,
            risks=["insufficient_draft"] if thin else [],
            reversibility="high",
            confidence=0.3,
            verification_method="structural",
            hard_veto=veto,
            veto_reason="thin_draft" if veto else "",
            valid=True,
        )


def merge_scorecards(
    cards: list[Scorecard],
) -> dict[str, Any]:
    """Select action from scorecards. Hard vetoes from qa/security win."""
    valid = [c for c in cards if c.valid]
    vetoes = [
        c
        for c in valid
        if c.hard_veto and c.role_id in {"qa", "security"}
    ]
    if vetoes:
        return {
            "action": "revise",
            "hard_veto": True,
            "veto_roles": [c.role_id for c in vetoes],
            "reasons": [c.veto_reason for c in vetoes if c.veto_reason],
            "shadow": True,
        }
    propose = sum(1 for c in valid if c.recommended_action == "propose_done")
    return {
        "action": "propose_done" if propose >= max(1, len(valid) // 2) else "continue",
        "hard_veto": False,
        "votes": len(valid),
        "shadow": True,
    }
