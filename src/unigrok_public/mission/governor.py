"""Threshold metacognitive governor. Shadow-safe: recommendations only.

Weights live in a versioned bundle (POSTERIOR defaults), not as scattered
control-flow law. Regex signals are *features*, not final risk law.
See docs/DEOVERFIT.md Phase 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Versioned posterior defaults — swap/pin without rewriting control flow.
WEIGHT_BUNDLE_VERSION = "gov_weights_v1"
WEIGHT_BUNDLE: dict[str, Any] = {
    "version": WEIGHT_BUNDLE_VERSION,
    "score_weights": {
        "uncertainty": 0.25,
        "impact": 0.2,
        "risk": 0.25,
        "irreversibility": 0.15,
        "novelty": 0.1,
        "disagreement": 0.15,
        "evidence_deficit": 0.2,
        "prior_fail": 0.05,
    },
    "belief_defaults": {
        "uncertainty": 0.35,
        "impact": 0.35,
        "risk": 0.25,
        "irreversibility": 0.15,
        "novelty": 0.25,
    },
    "belief_floors": {
        "concurrency": {
            "uncertainty": 0.75,
            "risk": 0.85,
            "impact": 0.8,
            "novelty": 0.55,
        },
        "security": {"risk": 0.9, "impact": 0.75, "irreversibility": 0.55},
        "irreversible": {"irreversibility": 0.85, "impact": 0.8, "risk": 0.7},
        "adversarial_review": {
            "uncertainty": 0.7,
            "novelty": 0.65,
            "risk": 0.65,
        },
        "self_verification": {"risk": 0.6, "uncertainty": 0.55},
        "corruption": {"risk": 0.7, "impact": 0.65},
        "concurrency_security": {
            "uncertainty": 0.8,
            "risk": 0.9,
            "impact": 0.85,
        },
    },
    "score_thresholds": {
        "low": 0.35,
        "medium": 0.55,
        "high": 0.75,
        "ultra": 0.9,
    },
    "budget_cut_ratio": 0.15,
    "budget_cut_factor": 0.5,
    "prior_fail_cap": 5,
    "prior_fail_boost": 0.08,
    "prior_fail_boost_cap": 4,
    "context_low": 4_000,
    "context_high": 12_000,
    "tool_budget_low": 2,
    "tool_budget_high": 6,
}


@dataclass(frozen=True)
class GovernorConfig:
    reasoning_level: str
    voter_roles: tuple[str, ...]
    candidate_count: int
    critique_rounds: int
    context_budget: int
    tool_budget: int
    verification_depth: str
    quantum_size: int
    shadow: bool = True
    signals: tuple[str, ...] = ()
    weight_bundle_version: str = WEIGHT_BUNDLE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "reasoning_level": self.reasoning_level,
            "voter_roles": list(self.voter_roles),
            "candidate_count": self.candidate_count,
            "critique_rounds": self.critique_rounds,
            "context_budget": self.context_budget,
            "tool_budget": self.tool_budget,
            "verification_depth": self.verification_depth,
            "quantum_size": self.quantum_size,
            "shadow": self.shadow,
            "signals": list(self.signals),
            "weight_bundle_version": self.weight_bundle_version,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> GovernorConfig | None:
        """Load an already-frozen mission config without consulting live defaults."""
        if not isinstance(raw, dict):
            return None
        level = str(raw.get("reasoning_level") or "")
        if level not in _LEVELS:
            return None
        allowed_roles = {
            "architect",
            "pm",
            "engineer",
            "qa",
            "security",
            "product",
            "perf",
        }
        roles = tuple(
            dict.fromkeys(
                str(role)
                for role in raw.get("voter_roles") or []
                if str(role) in allowed_roles
            )
        )
        if not roles:
            return None
        try:
            candidate_count = int(raw["candidate_count"])
            critique_rounds = int(raw["critique_rounds"])
            context_budget = int(raw["context_budget"])
            tool_budget = int(raw["tool_budget"])
            quantum_size = int(raw["quantum_size"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            candidate_count < 1
            or critique_rounds < 0
            or context_budget < 1
            or tool_budget < 0
            or quantum_size < 1
        ):
            return None
        verification_depth = str(raw.get("verification_depth") or "")
        if verification_depth not in {"normal", "strict"}:
            return None
        return cls(
            reasoning_level=level,
            voter_roles=roles,
            candidate_count=candidate_count,
            critique_rounds=critique_rounds,
            context_budget=context_budget,
            tool_budget=tool_budget,
            verification_depth=verification_depth,
            quantum_size=quantum_size,
            shadow=bool(raw.get("shadow", True)),
            signals=tuple(str(value) for value in raw.get("signals") or []),
            weight_bundle_version=str(
                raw.get("weight_bundle_version") or WEIGHT_BUNDLE_VERSION
            ),
        )


_LEVELS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

# Feature extractors — not final risk law.
_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "concurrency",
        re.compile(
            r"\b("
            r"lease|fencing|fence[_ -]?gen|cas\b|compare[- ]and[- ]swap|"
            r"race(?:\s+condition)?|concurrent|atomicity|stale\s+worker|"
            r"deadlock|semaphore|lock[_ -]?order|dual[_ -]?writer|"
            r"durable[- ]job|checkpoint|continuation"
            r")\b",
            re.I,
        ),
    ),
    (
        "security",
        re.compile(
            r"\b("
            r"security|secret|redact|leak(?:age)?|credential|token\s+leak|"
            r"injection|authz|attack|exfil|privilege|unsafe"
            r")\b",
            re.I,
        ),
    ),
    (
        "irreversible",
        re.compile(
            r"\b("
            r"irreversible|destructive|delete\b|drop\b|truncate|"
            r"production|prod\b|deploy|no[- ]ship|ship\b|release"
            r")\b",
            re.I,
        ),
    ),
    (
        "adversarial_review",
        re.compile(
            r"\b("
            r"adversarial|audit|review|critique|fault\s+inject|"
            r"threat\s+model|red\s*team|blast[- ]radius"
            r")\b",
            re.I,
        ),
    ),
    (
        "self_verification",
        re.compile(
            r"\b("
            r"self[- ]attest|self[- ]verif|acceptance|commitdone|"
            r"propose[- ]done|evidence|verifier"
            r")\b",
            re.I,
        ),
    ),
    (
        "corruption",
        re.compile(
            r"\b("
            r"corrupt(?:ion)?|hash\b|checksum|artifact|retention|"
            r"truncat|integrity"
            r")\b",
            re.I,
        ),
    ),
)


def classify_task_signals(*texts: str) -> tuple[str, ...]:
    blob = "\n".join(str(t or "") for t in texts)
    if not blob.strip():
        return ()
    return tuple(name for name, pattern in _SIGNAL_PATTERNS if pattern.search(blob))


def beliefs_from_signals(
    signals: tuple[str, ...] | list[str],
    *,
    prior_verify_failures: int = 0,
    bundle: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Map detected task classes onto governor belief dimensions in [0, 1]."""
    b = bundle or WEIGHT_BUNDLE
    defaults = dict(b["belief_defaults"])
    floors = b["belief_floors"]
    s = set(signals)
    beliefs = {k: float(v) for k, v in defaults.items()}

    def _raise(name: str) -> None:
        for key, val in floors.get(name, {}).items():
            beliefs[key] = max(beliefs[key], float(val))

    for name in (
        "concurrency",
        "security",
        "irreversible",
        "adversarial_review",
        "self_verification",
        "corruption",
    ):
        if name in s:
            _raise(name)
    if "concurrency" in s and ("security" in s or "adversarial_review" in s):
        _raise("concurrency_security")

    if prior_verify_failures:
        boost = float(b["prior_fail_boost"]) * min(
            prior_verify_failures, int(b["prior_fail_boost_cap"])
        )
        beliefs["uncertainty"] = min(1.0, beliefs["uncertainty"] + boost)

    evidence_deficit = 0.35 if "self_verification" in s else 0.15
    return {**beliefs, "evidence_deficit": evidence_deficit}


def _clamp_level(level: str, ceiling: str) -> str:
    try:
        li = _LEVELS.index(level)
    except ValueError:
        li = _LEVELS.index("medium")
    try:
        ci = _LEVELS.index(ceiling)
    except ValueError:
        ci = _LEVELS.index("high")
    return _LEVELS[min(li, ci)]


def _floor_level(level: str, floor: str) -> str:
    try:
        li = _LEVELS.index(level)
    except ValueError:
        li = _LEVELS.index("medium")
    try:
        fi = _LEVELS.index(floor)
    except ValueError:
        return level
    return _LEVELS[max(li, fi)]


def _merge_roles(
    base: tuple[str, ...], extra: tuple[str, ...]
) -> tuple[str, ...]:
    order = (
        "architect",
        "pm",
        "engineer",
        "qa",
        "security",
        "product",
        "perf",
    )
    seen = set(base) | set(extra)
    return tuple(r for r in order if r in seen)


def shadow_recommend(
    *,
    uncertainty: float = 0.3,
    impact: float = 0.3,
    risk: float = 0.2,
    irreversibility: float = 0.1,
    novelty: float = 0.2,
    disagreement: float = 0.0,
    evidence_deficit: float = 0.0,
    prior_verify_failures: int = 0,
    remaining_budget_ratio: float = 1.0,
    level_ceiling: str = "ultra",
    destructive: bool = False,
    signals: tuple[str, ...] | list[str] = (),
    bundle: dict[str, Any] | None = None,
) -> GovernorConfig:
    """Threshold governor. Always marked shadow=True until Phase G / Phase 5."""
    b = bundle or WEIGHT_BUNDLE
    w = b["score_weights"]
    th = b["score_thresholds"]
    signal_t = tuple(signals)
    score = (
        float(w["uncertainty"]) * uncertainty
        + float(w["impact"]) * impact
        + float(w["risk"]) * risk
        + float(w["irreversibility"]) * irreversibility
        + float(w["novelty"]) * novelty
        + float(w["disagreement"]) * disagreement
        + float(w["evidence_deficit"]) * evidence_deficit
        + float(w["prior_fail"]) * min(prior_verify_failures, int(b["prior_fail_cap"]))
    )
    if remaining_budget_ratio < float(b["budget_cut_ratio"]):
        score *= float(b["budget_cut_factor"])

    if destructive:
        return GovernorConfig(
            reasoning_level=_clamp_level("max", level_ceiling),
            voter_roles=("architect", "security", "qa"),
            candidate_count=1,
            critique_rounds=1,
            context_budget=8_000,
            tool_budget=0,
            verification_depth="strict",
            quantum_size=1,
            shadow=True,
            signals=signal_t,
            weight_bundle_version=str(b.get("version") or WEIGHT_BUNDLE_VERSION),
        )

    if score < float(th["low"]):
        level, roles, cands = "low", ("engineer",), 1
    elif score < float(th["medium"]):
        level, roles, cands = "medium", ("engineer", "qa"), 1
    elif score < float(th["high"]):
        level, roles, cands = "high", ("engineer", "architect", "qa"), 2
    else:
        level, roles, cands = "xhigh", ("architect", "engineer", "qa", "security"), 2

    if score >= float(th["ultra"]) and remaining_budget_ratio > 0.4:
        level, roles = "ultra", ("architect", "pm", "engineer", "qa", "security")

    s = set(signal_t)
    if "concurrency" in s and ("adversarial_review" in s or "security" in s):
        level = _floor_level(level, "high")
        roles = _merge_roles(roles, ("engineer", "architect", "qa", "security"))
        cands = max(cands, 2)
    elif "concurrency" in s:
        level = _floor_level(level, "high")
        roles = _merge_roles(roles, ("engineer", "architect", "qa"))
        cands = max(cands, 2)
    if "security" in s:
        level = _floor_level(level, "high")
        roles = _merge_roles(roles, ("security", "qa", "engineer"))
    if "irreversible" in s:
        level = _floor_level(level, "xhigh")
        roles = _merge_roles(roles, ("architect", "security", "qa"))

    high_stakes = bool(s & {"concurrency", "security", "irreversible"})
    verification = (
        "strict" if score >= float(th["medium"]) or high_stakes else "normal"
    )
    critique = 2 if score >= float(th["medium"]) or "concurrency" in s else 1
    rich = score >= float(th["medium"])

    return GovernorConfig(
        reasoning_level=_clamp_level(level, level_ceiling),
        voter_roles=roles,
        candidate_count=cands,
        critique_rounds=critique,
        context_budget=int(b["context_high"] if rich else b["context_low"]),
        tool_budget=int(b["tool_budget_high"] if rich else b["tool_budget_low"]),
        verification_depth=verification,
        quantum_size=1,
        shadow=True,
        signals=signal_t,
        weight_bundle_version=str(b.get("version") or WEIGHT_BUNDLE_VERSION),
    )


def recommend_for_task(
    task: str,
    *,
    acceptance: str = "",
    prior_verify_failures: int = 0,
    remaining_budget_ratio: float = 1.0,
    level_ceiling: str = "ultra",
    destructive: bool = False,
    bundle: dict[str, Any] | None = None,
) -> GovernorConfig:
    """Classify task text then recommend cognition (shadow)."""
    signals = classify_task_signals(task, acceptance)
    beliefs = beliefs_from_signals(
        signals,
        prior_verify_failures=prior_verify_failures,
        bundle=bundle,
    )
    return shadow_recommend(
        **beliefs,
        prior_verify_failures=prior_verify_failures,
        remaining_budget_ratio=remaining_budget_ratio,
        level_ceiling=level_ceiling,
        destructive=destructive,
        signals=signals,
        bundle=bundle,
    )
