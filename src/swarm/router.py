"""Discounted-UCB mutator-arm selection with a Pareto-aligned reward.

The reward the bandit learns from is the SAME thing selection values — a
4-level scale keyed to how far a candidate got in the funnel — so the router
can never drift toward a policy the front rejects (the rev-1 latency-only
reward was a design error: it starved memory/diff-winning arms). Generation 1
is fixed round-robin (cold start: ~24 pulls/task is too sparse to trust UCB
immediately); from generation 2 it is discounted UCB with a seeded tie-break.
Every choice emits a receipt so the deterministic-and-explainable contract
holds even though the search itself is stochastic.
"""

from __future__ import annotations

import json
import math
import random
from typing import Dict, Optional

ARMS = ("algorithmic", "allocation", "hot_loop", "simplify")

# Reward levels — identical to what SELECT values, so router ⟂ front never
# diverge. Dense enough (4 levels) for a discounted bandit at small N.
REWARD_SYNTAX_FAIL = 0.0     # died at parse/compile
REWARD_TESTS_FAIL = 0.15     # ran, but broke the oracle
REWARD_FEASIBLE_DOMINATED = 0.5   # correct, but not on the front
REWARD_FRONT_MEMBER = 1.0    # correct and Pareto-optimal


def reward_for(stage_reached: str, feasible: bool, on_front: bool) -> float:
    if on_front:
        return REWARD_FRONT_MEMBER
    if feasible:
        return REWARD_FEASIBLE_DOMINATED
    if stage_reached in ("tests", "bench"):
        return REWARD_TESTS_FAIL
    return REWARD_SYNTAX_FAIL


class DiscountedUCBRouter:
    """Per-task bandit over the four mutator arms."""

    def __init__(self, seed: int, decay: float = 0.95, exploration: float = 1.0):
        self.decay = float(decay)
        self.exploration = float(exploration)
        self._rng = random.Random(int(seed))
        # Discounted sums per arm.
        self._reward = {arm: 0.0 for arm in ARMS}
        self._count = {arm: 0.0 for arm in ARMS}
        self._pulls = {arm: 0 for arm in ARMS}  # raw pulls, for receipts
        self._step = 0

    def select(self, generation: int) -> Dict[str, object]:
        """Return {'arm', 'receipt'} for the next mutant slot."""
        self._step += 1
        if generation <= 1:
            arm = ARMS[(self._step - 1) % len(ARMS)]
            reason = "round_robin_cold_start"
            scores: Dict[str, Optional[float]] = {a: None for a in ARMS}
        else:
            scores = {a: self._ucb_score(a) for a in ARMS}
            best = max(scores.values())
            # Seeded tie-break preserves determinism AND diversity at small N.
            leaders = [a for a in ARMS if scores[a] == best]
            arm = leaders[0] if len(leaders) == 1 else self._rng.choice(leaders)
            reason = "discounted_ucb"
        self._pulls[arm] += 1
        receipt = json.dumps({
            "arm": arm,
            "reason": reason,
            "step": self._step,
            "generation": generation,
            "decay": self.decay,
            "scores": {
                a: (round(s, 4) if s is not None and math.isfinite(s) else None)
                for a, s in scores.items()
            },
            "pulls": dict(self._pulls),
        }, separators=(",", ":"))
        return {
            "arm": arm,
            "pull": self._pulls[arm],
            "step": self._step,
            "receipt": receipt,
        }

    def update(self, arm: str, reward: float) -> None:
        """Discount every arm, then credit the pulled arm's reward — standard
        discounted UCB bookkeeping (non-stationary: the folded prompt context
        shifts each generation, so recent evidence should dominate)."""
        if arm not in self._reward:
            return
        for a in ARMS:
            self._reward[a] *= self.decay
            self._count[a] *= self.decay
        self._reward[arm] += float(reward)
        self._count[arm] += 1.0

    def _ucb_score(self, arm: str) -> float:
        n = self._count[arm]
        if n <= 0:
            return float("inf")  # never-pulled arms are explored first
        mean = self._reward[arm] / n
        total = sum(self._count.values())
        bonus = self.exploration * math.sqrt(2.0 * math.log(max(total, 1.0)) / n)
        return mean + bonus

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            arm: {
                "mean_reward": (self._reward[arm] / self._count[arm]) if self._count[arm] > 0 else 0.0,
                "discounted_count": round(self._count[arm], 3),
                "pulls": self._pulls[arm],
            }
            for arm in ARMS
        }
