"""SwarmRunner: durable-row lifecycle around the engine (JobManager pattern).

Persists a queued row first, runs preflight then the engine in an
asyncio.create_task, touches updated_at after every candidate as a heartbeat,
and supports cooperative cancel between candidates. The asyncio task does not
survive a restart; a running row whose heartbeat is older than the staleness
horizon reads as failed_stale (the row is the durable record, exactly as
JobManager treats jobs).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from . import config as swarm_config
from .ast_utils import extract_node_span, span_line_range
from .engine import EngineConfig, SwarmEngine
from .preflight import PreflightError, run_preflight
from .sandbox import SandboxError, SwarmSandbox


def _now() -> str:
    return datetime.now().isoformat()


def is_stale(row: Dict[str, Any], stale_after_sec: float) -> bool:
    """A running/queued task whose heartbeat is older than the horizon — the
    process that owned its asyncio task almost certainly died."""
    if row.get("status") not in ("queued", "preflight", "running"):
        return False
    updated = str(row.get("updated_at") or "")
    if not updated:
        return True
    try:
        age = (datetime.now() - datetime.fromisoformat(updated)).total_seconds()
    except ValueError:
        return True
    return age > stale_after_sec


def effective_status(row: Dict[str, Any]) -> str:
    """Status a reader should see: failed_stale overrides a stuck row."""
    if is_stale(row, swarm_config.swarm_stale_after_sec()):
        return "failed_stale"
    return str(row.get("status") or "unknown")


class SwarmRunner:
    def __init__(self, store: Any, state_root: Path):
        self._store = store
        self._state_root = Path(state_root)
        self._tasks: Dict[str, asyncio.Task] = {}
        self._cancelled: Set[str] = set()

    def cancel(self, task_id: str) -> None:
        """Cooperative cancel — the engine checks between candidates."""
        self._cancelled.add(str(task_id))

    async def wait(self, task_id: str, timeout: float = 30.0) -> None:
        task = self._tasks.get(str(task_id))
        if task is not None:
            await asyncio.wait({task}, timeout=timeout)

    def launch(self, task_id: str, spec: Dict[str, Any]) -> asyncio.Task:
        task = asyncio.create_task(self._run(task_id, spec))
        self._tasks[str(task_id)] = task
        task.add_done_callback(lambda _t: self._tasks.pop(str(task_id), None))
        return task

    async def _run(self, task_id: str, spec: Dict[str, Any]) -> None:
        work_root = self._state_root / "swarm" / str(task_id)
        sandbox = SwarmSandbox(
            workspace_root=spec["workspace_root"],
            work_root=work_root,
            target_rel=spec["target_rel"],
            max_copy_mb=swarm_config.swarm_max_copy_mb(),
            child_mem_mb=swarm_config.swarm_child_mem_mb(),
        )
        try:
            await self._store.update_swarm_task(task_id, status="preflight")
            sandbox.create()
            file_source = sandbox.read_target()
            start, end = extract_node_span(file_source, spec["focus_node"])
            span_lines = span_line_range(file_source, start, end)
            bench_argv = spec["bench_argv"]

            oracle = await run_preflight(
                sandbox,
                target_rel=spec["target_rel"],
                span_lines=span_lines,
                test_target=spec["test_target"],
                bench_argv=bench_argv,
                bench_repeats=swarm_config.swarm_bench_repeats(),
                eval_timeout=swarm_config.swarm_eval_timeout(),
                stage_budget_fraction=swarm_config.swarm_stage_budget_fraction(),
                allow_unstable_bench=spec.get("allow_unstable_bench", False),
            )
            baseline = dict(oracle.get("bench") or {})
            await self._store.update_swarm_task(
                task_id,
                status="running",
                oracle_json=json.dumps(oracle, separators=(",", ":")),
                baseline_json=json.dumps(baseline, separators=(",", ":")),
            )

            engine = SwarmEngine(
                sandbox=sandbox,
                task_id=task_id,
                focus_node=spec["focus_node"],
                target_rel=spec["target_rel"],
                test_target=spec["test_target"],
                bench_argv=bench_argv,
                baseline=baseline,
                span=(start, end),
                original_span=file_source[start:end],
                file_source=file_source,
                config=EngineConfig(
                    population=swarm_config.swarm_population(),
                    max_generations=swarm_config.swarm_max_generations(),
                    max_concurrent_gen=swarm_config.swarm_max_concurrent_gen(),
                    bench_repeats=swarm_config.swarm_bench_repeats(),
                    eval_timeout=swarm_config.swarm_eval_timeout(),
                    budget_usd=spec["budget_usd"],
                    seed=spec["seed"],
                    allow_unstable_bench=spec.get("allow_unstable_bench", False),
                ),
                goal=spec.get("goal", ""),
                on_candidate=lambda c: self._persist_candidate(task_id, c),
                cancelled=lambda: str(task_id) in self._cancelled,
            )
            outcomes = await engine.run()
            if outcomes:
                await self._store.update_swarm_task(
                    task_id,
                    generation=outcomes[-1].generation,
                    spent_usd=engine.spent_usd,
                    folded_state=outcomes[-1].folded_state,
                )
            final = "cancelled" if str(task_id) in self._cancelled else "completed"
            await self._store.update_swarm_task(task_id, status=final)
        except PreflightError as exc:
            await self._store.update_swarm_task(
                task_id,
                status="failed",
                oracle_json=json.dumps(exc.oracle, separators=(",", ":")),
            )
        except (SandboxError, ValueError) as exc:
            await self._store.update_swarm_task(
                task_id,
                status="failed",
                oracle_json=json.dumps({"error": str(exc)[:400]}, separators=(",", ":")),
            )
        finally:
            self._cancelled.discard(str(task_id))
            sandbox.destroy()

    async def _persist_candidate(self, task_id: str, candidate: Dict[str, Any]) -> None:
        # Heartbeat + durable candidate row. Only rows that reached the dedupe
        # stage carry code; earlier discards (empty generation) are skipped.
        await self._store.update_swarm_task(task_id, spent_usd=None)
        if not candidate.get("code"):
            return
        try:
            await self._store.insert_swarm_candidate(candidate)
        except ValueError:
            # Oversized/secret-bearing candidate — recorded as a discard, not
            # a crash (the store guards apply-time integrity).
            pass
