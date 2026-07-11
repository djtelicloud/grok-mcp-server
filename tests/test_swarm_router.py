# tests/test_swarm_router.py
# Discounted-UCB router. The design-critical property (Grok's finding) is that
# the reward levels ARE the selection outcome, so the router cannot drift
# toward a policy the Pareto front rejects.

import json

import pytest

from src.swarm.router import (
    ARMS,
    REWARD_FEASIBLE_DOMINATED,
    REWARD_FRONT_MEMBER,
    REWARD_SYNTAX_FAIL,
    REWARD_TESTS_FAIL,
    DiscountedUCBRouter,
    reward_for,
)


class TestAlignedReward:
    def test_four_levels_match_funnel_progress(self):
        assert reward_for("bench", feasible=True, on_front=True) == REWARD_FRONT_MEMBER
        assert reward_for("bench", feasible=True, on_front=False) == REWARD_FEASIBLE_DOMINATED
        assert reward_for("tests", feasible=False, on_front=False) == REWARD_TESTS_FAIL
        assert reward_for("parse", feasible=False, on_front=False) == REWARD_SYNTAX_FAIL

    def test_front_beats_dominated_beats_tests_beats_syntax(self):
        assert (
            REWARD_FRONT_MEMBER > REWARD_FEASIBLE_DOMINATED
            > REWARD_TESTS_FAIL > REWARD_SYNTAX_FAIL
        )


class TestRouterSelection:
    def test_generation_one_is_round_robin(self):
        router = DiscountedUCBRouter(seed=1)
        picks = [router.select(generation=1)["arm"] for _ in range(len(ARMS))]
        assert set(picks) == set(ARMS)  # each arm once, no repeats

    def test_receipt_is_complete_and_json(self):
        router = DiscountedUCBRouter(seed=1)
        pick = router.select(generation=1)
        receipt = json.loads(pick["receipt"])
        assert receipt["arm"] == pick["arm"]
        assert receipt["reason"] == "round_robin_cold_start"
        assert set(receipt["scores"].keys()) == set(ARMS)
        assert "pulls" in receipt and "decay" in receipt

    def test_converges_to_higher_reward_arm(self):
        """After enough evidence, the consistently-rewarded arm dominates."""
        router = DiscountedUCBRouter(seed=3)
        # Warm all arms once (round-robin gen 1).
        for _ in ARMS:
            router.select(generation=1)
        # 'algorithmic' always wins the front; others always fail tests.
        for _ in range(60):
            pick = router.select(generation=2)
            reward = REWARD_FRONT_MEMBER if pick["arm"] == "algorithmic" else REWARD_TESTS_FAIL
            router.update(pick["arm"], reward)
        snapshot = router.snapshot()
        best = max(snapshot, key=lambda a: snapshot[a]["mean_reward"])
        assert best == "algorithmic"
        # And it earned the most pulls.
        assert snapshot["algorithmic"]["pulls"] == max(
            snapshot[a]["pulls"] for a in ARMS
        )

    def test_seeded_determinism(self):
        def _trace():
            r = DiscountedUCBRouter(seed=42)
            picks = []
            for gen in (1, 1, 1, 1, 2, 2, 2):
                pick = r.select(generation=gen)
                picks.append(pick["arm"])
                r.update(pick["arm"], 0.5)  # symmetric reward → tie-breaks exercised
            return picks
        assert _trace() == _trace()

    def test_decay_favors_recent_evidence(self):
        router = DiscountedUCBRouter(seed=1, decay=0.5)
        for _ in ARMS:
            router.select(generation=1)
        # Early good, then consistently bad for one arm.
        router.update("hot_loop", 1.0)
        for _ in range(5):
            router.update("hot_loop", 0.0)
        # Heavy decay means the stale 1.0 is nearly forgotten.
        assert router.snapshot()["hot_loop"]["mean_reward"] < 0.2
