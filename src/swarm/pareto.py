"""Constrained multi-objective selection: fast non-dominated sort + crowding.

All objectives are MINIMIZED (latency_ms, peak_mem_bytes, diff_bytes — smaller
is better for each). Feasibility (tests passed) is a CONSTRAINT, not an
objective: infeasible candidates never enter the front. This is the standard
NSGA-II machinery, kept pure and deterministic so it is byte-stable in CI and
matches a brute-force domination check in tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

OBJECTIVES = ("latency_ms", "peak_mem_bytes", "diff_bytes")


def _objtuple(candidate: Dict[str, Any], objectives: Sequence[str]) -> Tuple[float, ...]:
    return tuple(
        float("inf") if candidate.get(key) is None else float(candidate[key])
        for key in objectives
    )


def dominates(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    """a dominates b (minimization): no worse on all objectives, strictly
    better on at least one."""
    no_worse = all(x <= y for x, y in zip(a, b))
    strictly_better = any(x < y for x, y in zip(a, b))
    return no_worse and strictly_better


def fast_non_dominated_sort(
    points: Sequence[Tuple[float, ...]]
) -> List[List[int]]:
    """Return fronts as lists of indices; front 0 is the Pareto-optimal set.
    O(MN^2) — fine for the tens-of-candidates scale here."""
    n = len(points)
    if n == 0:
        return []
    dominated_by: List[List[int]] = [[] for _ in range(n)]
    domination_count = [0] * n
    fronts: List[List[int]] = [[]]
    for p in range(n):
        for q in range(p + 1, n):
            if dominates(points[p], points[q]):
                dominated_by[p].append(q)
                domination_count[q] += 1
            elif dominates(points[q], points[p]):
                dominated_by[q].append(p)
                domination_count[p] += 1
    for p in range(n):
        if domination_count[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt: List[int] = []
        for p in fronts[i]:
            for q in dominated_by[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(sorted(nxt))
    return [f for f in fronts if f]


def crowding_distance(
    points: Sequence[Tuple[float, ...]], indices: Sequence[int]
) -> Dict[int, float]:
    """NSGA-II crowding distance within one front. Boundary points get
    infinity; interior points get the summed normalized neighbor gap."""
    distance = {i: 0.0 for i in indices}
    if len(indices) <= 2:
        return {i: float("inf") for i in indices}
    num_obj = len(points[indices[0]])
    for m in range(num_obj):
        ordered = sorted(indices, key=lambda i: points[i][m])
        lo = points[ordered[0]][m]
        hi = points[ordered[-1]][m]
        span = hi - lo
        distance[ordered[0]] = float("inf")
        distance[ordered[-1]] = float("inf")
        if span <= 0:
            continue
        for k in range(1, len(ordered) - 1):
            prev_v = points[ordered[k - 1]][m]
            next_v = points[ordered[k + 1]][m]
            if distance[ordered[k]] != float("inf"):
                distance[ordered[k]] += (next_v - prev_v) / span
    return distance


def rank_candidates(
    candidates: List[Dict[str, Any]],
    objectives: Sequence[str] = OBJECTIVES,
) -> List[Dict[str, Any]]:
    """Annotate FEASIBLE candidates in place with pareto_rank (0 = optimal
    front) and crowding, and return them ordered (rank asc, crowding desc).
    Infeasible candidates are dropped — they are not selectable."""
    feasible = [c for c in candidates if c.get("feasible")]
    if not feasible:
        return []
    points = [_objtuple(c, objectives) for c in feasible]
    fronts = fast_non_dominated_sort(points)
    for rank, front in enumerate(fronts):
        crowd = crowding_distance(points, front)
        for idx in front:
            feasible[idx]["pareto_rank"] = rank
            feasible[idx]["crowding"] = crowd[idx]
    # id is the final tie-break so the ordering is independent of input order
    # (byte-stable across runs regardless of how candidates were accumulated).
    return sorted(
        feasible,
        key=lambda c: (c["pareto_rank"], -_finite(c["crowding"]), str(c.get("id", ""))),
    )


def select_champion(
    candidates: List[Dict[str, Any]], primary_goal: str = "balanced"
) -> Dict[str, Any] | None:
    """Choose one deterministic CTA candidate from the current Pareto front.

    This does not alter Pareto membership. It only turns a multi-objective
    front into the single "Best verified candidate" action requested by the
    UI, with candidate id as the final stable tie-break.
    """
    front = [candidate for candidate in rank_candidates(candidates) if candidate["pareto_rank"] == 0]
    if not front:
        return None
    goal = str(primary_goal or "balanced")
    goal_orders = {
        "latency": ("latency_ms", "peak_mem_bytes", "diff_bytes"),
        "memory": ("peak_mem_bytes", "latency_ms", "diff_bytes"),
        "size": ("diff_bytes", "latency_ms", "peak_mem_bytes"),
    }
    if goal in goal_orders:
        order = goal_orders[goal]

        def key(candidate):
            return (*(_value(candidate, field) for field in order), str(candidate.get("id", "")))

    elif goal == "balanced":
        ranges = {
            objective: (
                min(_value(candidate, objective) for candidate in front),
                max(_value(candidate, objective) for candidate in front),
            )
            for objective in OBJECTIVES
        }

        def key(candidate):
            distance = 0.0
            for objective in OBJECTIVES:
                low, high = ranges[objective]
                if high > low:
                    distance += (_value(candidate, objective) - low) / (high - low)
            return distance, str(candidate.get("id", ""))
    else:
        raise ValueError(f"unknown primary_goal {primary_goal!r}")
    return min(front, key=key)


def _value(candidate: Dict[str, Any], field: str) -> float:
    value = candidate.get(field)
    return float("inf") if value is None else float(value)


def _finite(value: float) -> float:
    # inf sorts first when negated; a large finite proxy keeps the sort total.
    if value == float("inf"):
        return 1e18
    return float(value)
