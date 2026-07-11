"""Swarm optimizer configuration (UNIGROK_SWARM_*).

All knobs are read lazily per call (the rag.py idiom — no import-time freeze)
and clamped to sane ranges so a typo'd env value degrades instead of
exploding. The rollout ladder follows the repo convention: `off` (default),
`dry_run` (run swarms, score candidates, show the Pareto front, but NEVER
apply), `active` (apply_swarm_winner enabled). An unknown mode warns ONCE and
reads as off.
"""

from __future__ import annotations

import logging
import os

_LOGGER = "GrokMCP"

_VALID_MODES = ("off", "dry_run", "active")
_MODE_WARNED = False


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def swarm_mode() -> str:
    """The rollout mode, defaulting to 'off'. Unknown values warn once and
    read as 'off' (same rationale as rag.task_rag_mode / semantic_evals_mode:
    a loud log plus status visibility beats aborting a shared local server)."""
    global _MODE_WARNED
    raw = os.environ.get("UNIGROK_SWARM", "").strip().lower() or "off"
    if raw in _VALID_MODES:
        return raw
    if not _MODE_WARNED:
        _MODE_WARNED = True
        logging.getLogger(_LOGGER).warning(
            f"unknown UNIGROK_SWARM={raw!r}; treating as off "
            f"(valid: {', '.join(_VALID_MODES)})"
        )
    return "off"


def swarm_max_generations() -> int:
    return _env_int("UNIGROK_SWARM_MAX_GENERATIONS", 6, 1, 20)


def swarm_population() -> int:
    return _env_int("UNIGROK_SWARM_POPULATION", 4, 1, 16)


def swarm_max_concurrent_gen() -> int:
    return _env_int("UNIGROK_SWARM_MAX_CONCURRENT_GEN", 2, 1, 8)


def swarm_max_concurrent_tasks() -> int:
    return _env_int("UNIGROK_SWARM_MAX_CONCURRENT_TASKS", 1, 1, 4)


def swarm_eval_timeout() -> float:
    """Per-candidate evaluation (tests + bench) wall-clock ceiling."""
    return _env_float("UNIGROK_SWARM_EVAL_TIMEOUT", 120.0, 10.0, 600.0)


def swarm_stage_budget_fraction() -> float:
    """Fraction of the eval timeout the preflight baseline run must fit in —
    a test_target slower than this fails the task at start instead of
    producing a multi-hour zombie."""
    return _env_float("UNIGROK_SWARM_STAGE_BUDGET_FRACTION", 0.5, 0.1, 1.0)


def swarm_bench_repeats() -> int:
    """Measured bench repeats (an additional first warmup run is discarded)."""
    return _env_int("UNIGROK_SWARM_BENCH_REPEATS", 5, 3, 20)


def swarm_default_budget_usd() -> float:
    return _env_float("UNIGROK_SWARM_DEFAULT_BUDGET_USD", 2.00, 0.0, 100.0)


def swarm_max_budget_usd() -> float:
    return _env_float("UNIGROK_SWARM_MAX_BUDGET_USD", 10.00, 0.0, 1000.0)


def swarm_max_copy_mb() -> int:
    """Workspace-copy size guard for the per-task sandbox."""
    return _env_int("UNIGROK_SWARM_MAX_COPY_MB", 500, 10, 10000)


def swarm_child_mem_mb() -> int:
    """RLIMIT_AS ceiling for mutant test/bench child processes."""
    return _env_int("UNIGROK_SWARM_CHILD_MEM_MB", 2048, 128, 16384)


def swarm_stale_after_sec() -> float:
    """Heartbeat staleness horizon: a running task whose row has not been
    touched for this long is reported failed_stale (the runner touches
    updated_at after every candidate). Deliberately derived from the eval
    timeout — a healthy swarm can far exceed JobManager's global default."""
    return 3.0 * swarm_eval_timeout()


def reset_swarm_state() -> None:
    """Test isolation for module-level flags."""
    global _MODE_WARNED
    _MODE_WARNED = False
