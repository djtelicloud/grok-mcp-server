"""Compose the per-task swarm fold IN CODE from candidate rows.

Structured state needs no model to fold (unlike prose chat histories): the
swarm's working memory is already rows, so this reuses the FoldedSessionState
schema + _render_folded_state renderer from utils with zero model calls. The
fold is a prompt-compression convenience for the next generation's mutator
context — NOT the state of record (that is always the two tables). Ordering is
deterministic (stable sort keys) so the rendered block is byte-stable in CI.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from ..utils import FoldedSessionState, _render_folded_state


def _reason_key(candidate: Dict[str, Any]) -> tuple:
    """Deterministic ordering for 'worst attempt' dead-ends: by stage, mutator,
    then a stable hash of the summary so shuffled insertion order renders
    identically."""
    stage = str(candidate.get("stage_reached") or "")
    mutator = str(candidate.get("mutator") or "")
    summary = _dead_end_summary(candidate)
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:8]
    return (stage, mutator, digest)


def _dead_end_summary(candidate: Dict[str, Any]) -> str:
    mutator = str(candidate.get("mutator") or "unknown")
    stage = str(candidate.get("stage_reached") or "unknown")
    if not candidate.get("feasible"):
        return f"{mutator}: failed at {stage}"
    return f"{mutator}: correct but slower/heavier than the front"


def build_folded_state(
    *,
    goal: str,
    target_path: str,
    test_target: str,
    bench_command: str,
    candidates: List[Dict[str, Any]],
    front_size: int,
    best_delta_pct: Optional[float],
    generation: int,
) -> str:
    """Render the swarm's working state for the next mutator prompt."""
    # Dead ends: non-front candidates, worst-first by deterministic key, capped
    # by the schema's list bound (8).
    non_front = [c for c in candidates if not _on_front(c)]
    non_front_sorted = sorted(non_front, key=_reason_key)
    seen: set = set()
    dead_ends: List[str] = []
    for candidate in non_front_sorted:
        summary = _dead_end_summary(candidate)
        if summary in seen:
            continue
        seen.add(summary)
        dead_ends.append(summary)

    constraints = [
        f"tests that must pass: {test_target}",
        f"benchmark: {bench_command}",
        "improvements below the noise floor do not count",
    ]
    delta = f"{best_delta_pct:.1f}% faster" if best_delta_pct is not None else "none yet"
    narrative = (
        f"Generation {generation}. Pareto front holds {front_size} candidate(s); "
        f"best latency improvement so far: {delta}."
    )
    fold = FoldedSessionState(
        user_goal=goal[:400],
        established_constraints=constraints,
        failed_attempts=dead_ends[:8],
        active_files=[target_path],
        narrative=narrative,
    )
    return _render_folded_state(fold, len(candidates))


def _on_front(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("feasible")) and candidate.get("pareto_rank") == 0
