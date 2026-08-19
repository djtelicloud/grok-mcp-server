"""Retrieval algebra: RRF, recency, kind prior, decay, hierarchical blend."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

RRF_K = 60
TIME_WINDOW_DAYS = 1.0
TIME_LAMBDA = 0.08
DECAY_LAMBDA = 0.02
DECAY_SIGMA = 0.6
DECAY_MU = 0.04
COLD_THRESHOLD = 0.20
WALK_ALPHA = 0.75
W_TIME = 0.15
W_BUSI = 0.10

_KIND_PRIOR = {
    "skills": 1.0,
    "memories": 0.75,
    "peers": 0.85,
    "resources": 0.70,
    "sessions": 0.30,
}

_PATH_PRIOR = (
    ("decisions", 0.90),
    ("gotchas", 0.88),
    ("preferences", 0.85),
    ("facts", 0.60),
    ("handoff", 0.80),
    ("last-job", 0.85),
)


def rrf(rank: int, *, k: int = RRF_K) -> float:
    if rank < 1:
        raise ValueError("rank is 1-based and must be >= 1")
    return 1.0 / (k + rank)


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for index, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + rrf(index, k=k)
    return scores


def time_score(
    age_days: float, *, window: float = TIME_WINDOW_DAYS, lam: float = TIME_LAMBDA
) -> float:
    age = max(0.0, float(age_days))
    if age <= window:
        return 1.0
    return math.exp(-lam * (age - window))


def business_score(kind: str, path: str = "") -> float:
    base = float(_KIND_PRIOR.get(kind, 0.5))
    lowered = str(path or "").lower()
    for needle, prior in _PATH_PRIOR:
        if needle in lowered:
            return max(base, float(prior))
    return base


def authority_multiplier(*, pinned: bool = False, tags: Sequence[str] = ()) -> float:
    value = 1.0
    if pinned:
        value += 0.10
    lowered = {str(tag).lower() for tag in tags}
    if lowered & {"canonical", "source-of-truth", "active"}:
        value += 0.05
    if lowered & {"superseded", "historical"}:
        value -= 0.15
    if lowered & {"test-fixture", "do-not-answer-from"}:
        value -= 0.25
    return min(1.15, max(0.70, value))


def final_score(
    origin: float,
    *,
    age_days: float,
    kind: str,
    path: str = "",
    pinned: bool = False,
    tags: Sequence[str] = (),
    w_time: float = W_TIME,
    w_busi: float = W_BUSI,
) -> float:
    w_t = min(1.0, max(0.0, float(w_time)))
    w_b = min(1.0 - w_t, max(0.0, float(w_busi)))
    mixed = (1.0 - w_t - w_b) * float(origin) + w_t * time_score(age_days) + w_b * business_score(
        kind, path
    )
    return mixed * authority_multiplier(pinned=pinned, tags=tags)


def child_score(self_score: float, parent_score: float, *, alpha: float = WALK_ALPHA) -> float:
    a = min(1.0, max(0.0, float(alpha)))
    return a * float(self_score) + (1.0 - a) * float(parent_score)


def decay_rho(
    *,
    salience: float,
    age_days: float,
    access_count: int,
    idle_days: float,
    distinct_actors: int = 1,
    breadth_weight: float = 0.0,
    pinned: bool = False,
) -> float:
    if pinned:
        return 1.0
    age = max(0.0, float(age_days))
    idle = max(0.0, float(idle_days))
    n_access = max(0, int(access_count))
    actors = max(1, int(distinct_actors))
    breadth = 1.0 + float(breadth_weight) * math.log(1.0 + max(actors - 1, 0))
    return (
        float(salience) * math.exp(-DECAY_LAMBDA * age)
        + DECAY_SIGMA * math.log(1.0 + n_access) * math.exp(-DECAY_MU * idle) * breadth
    )


def rank_by_score(scores: Mapping[str, float]) -> list[str]:
    return sorted(scores, key=lambda key: (-float(scores[key]), key))
