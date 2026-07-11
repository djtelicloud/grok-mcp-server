# tests/test_swarm_pareto.py
# Constrained non-dominated sort + crowding. The load-bearing property is that
# rank_candidates matches a brute-force domination oracle and that infeasible
# candidates never enter the front.

import itertools
import random

import pytest

from src.swarm.pareto import (
    OBJECTIVES,
    crowding_distance,
    dominates,
    fast_non_dominated_sort,
    rank_candidates,
)


def _brute_front(points):
    """Indices not dominated by any other point (minimization)."""
    front = []
    for i, p in enumerate(points):
        if not any(dominates(points[j], p) for j in range(len(points)) if j != i):
            front.append(i)
    return set(front)


class TestDomination:
    def test_strict_domination(self):
        assert dominates((1.0, 1.0), (2.0, 2.0))
        assert not dominates((1.0, 2.0), (2.0, 1.0))  # trade-off, neither dominates
        assert not dominates((1.0, 1.0), (1.0, 1.0))  # equal is not domination


class TestNonDominatedSort:
    def test_matches_brute_force_on_random_fixtures(self):
        rng = random.Random(7)
        for _ in range(50):
            n = rng.randint(1, 12)
            points = [
                (rng.randint(0, 5), rng.randint(0, 5), rng.randint(0, 5))
                for _ in range(n)
            ]
            fronts = fast_non_dominated_sort(points)
            assert set(fronts[0]) == _brute_front(points)
            # Every index appears in exactly one front.
            flat = list(itertools.chain.from_iterable(fronts))
            assert sorted(flat) == list(range(n))

    def test_single_point(self):
        assert fast_non_dominated_sort([(1.0, 2.0, 3.0)]) == [[0]]


class TestCrowding:
    def test_boundary_points_are_infinite(self):
        points = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
        dist = crowding_distance(points, [0, 1, 2])
        assert dist[0] == float("inf")
        assert dist[2] == float("inf")
        assert dist[1] < float("inf")

    def test_two_points_all_infinite(self):
        dist = crowding_distance([(0.0,), (1.0,)], [0, 1])
        assert all(v == float("inf") for v in dist.values())


class TestRankCandidates:
    def test_infeasible_never_selected(self):
        cands = [
            {"id": "a", "feasible": True, "latency_ms": 10, "peak_mem_bytes": 100, "diff_bytes": 5},
            {"id": "b", "feasible": False, "latency_ms": 1, "peak_mem_bytes": 1, "diff_bytes": 1},
        ]
        ranked = rank_candidates(cands)
        assert [c["id"] for c in ranked] == ["a"]

    def test_empty_when_none_feasible(self):
        assert rank_candidates([{"id": "x", "feasible": False}]) == []

    def test_missing_objective_is_not_treated_as_zero(self):
        cands = [
            {"id": "complete", "feasible": True, "latency_ms": 10,
             "peak_mem_bytes": 100, "diff_bytes": 5},
            {"id": "missing", "feasible": True, "latency_ms": None,
             "peak_mem_bytes": None, "diff_bytes": None},
        ]
        ranked = rank_candidates(cands)
        assert ranked[0]["id"] == "complete"
        missing = next(c for c in ranked if c["id"] == "missing")
        assert missing["pareto_rank"] > 0

    def test_front_members_get_rank_zero(self):
        cands = [
            {"id": "fast", "feasible": True, "latency_ms": 5, "peak_mem_bytes": 200, "diff_bytes": 10},
            {"id": "light", "feasible": True, "latency_ms": 20, "peak_mem_bytes": 50, "diff_bytes": 10},
            {"id": "dominated", "feasible": True, "latency_ms": 25, "peak_mem_bytes": 210, "diff_bytes": 12},
        ]
        ranked = rank_candidates(cands)
        front = {c["id"] for c in ranked if c["pareto_rank"] == 0}
        # fast and light are a trade-off (both optimal); dominated is not.
        assert front == {"fast", "light"}
        assert next(c for c in ranked if c["id"] == "dominated")["pareto_rank"] == 1

    def test_deterministic_order(self):
        cands = [
            {"id": str(i), "feasible": True, "latency_ms": i, "peak_mem_bytes": 10 - i, "diff_bytes": 1}
            for i in range(5)
        ]
        first = [c["id"] for c in rank_candidates([dict(c) for c in cands])]
        second = [c["id"] for c in rank_candidates([dict(c) for c in reversed(cands)])]
        assert first == second
