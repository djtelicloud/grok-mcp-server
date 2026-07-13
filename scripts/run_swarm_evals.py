#!/usr/bin/env python3
"""Run the manifest-backed Swarm golden targets sequentially.

This is an opt-in live contributor check, never a CI job. It uses the real
subscription-backed CLI generation plane, so it consumes provider quota and
wall-clock time even though UniGrok's metered receipt must remain exactly $0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import src.tools.swarm as swarm_tools
from src.swarm import config as swarm_config
from src.utils import PathResolver, grok_cli_plane_status, is_cloudrun_runtime


@dataclass(frozen=True)
class TargetSpec:
    name: str
    target_path: str
    focus_node: str
    test_target: str
    bench_command: str


@dataclass
class SweepResult:
    target: str
    payload: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None


def _workspace_relative(workspace: Path, target_dir: Path, value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw or Path(raw).name != raw:
        raise ValueError(f"{target_dir.name}: {field} must be one file name")
    resolved = (target_dir / raw).resolve()
    try:
        relative = resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{target_dir.name}: {field} escapes the workspace") from exc
    if not resolved.is_file():
        raise ValueError(f"{target_dir.name}: {field} not found: {raw}")
    return relative.as_posix()


def discover_targets(workspace: Path) -> List[TargetSpec]:
    root = workspace / "evals" / "tasks" / "swarm_targets"
    if not root.is_dir():
        raise ValueError(f"golden target root not found in attached workspace: {root}")
    specs: List[TargetSpec] = []
    for manifest_path in sorted(root.glob("*/target.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid target manifest {manifest_path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError(f"{manifest_path}: expected manifest version 1")
        target_dir = manifest_path.parent
        focus_node = str(data.get("focus_node") or "").strip()
        if not focus_node:
            raise ValueError(f"{manifest_path}: focus_node is required")
        target_path = _workspace_relative(
            workspace, target_dir, data.get("target"), "target"
        )
        test_target = _workspace_relative(
            workspace, target_dir, data.get("test_target"), "test_target"
        )
        bench_script = _workspace_relative(
            workspace, target_dir, data.get("bench_script"), "bench_script"
        )
        specs.append(
            TargetSpec(
                name=target_dir.name,
                target_path=target_path,
                focus_node=focus_node,
                test_target=test_target,
                bench_command=f"python {bench_script}",
            )
        )
    if not specs:
        raise ValueError(f"no target.json manifests found under {root}")
    return specs


def gate_errors() -> List[str]:
    if os.environ.get("UNIGROK_SWARM_EVALS_LIVE", "").strip() != "1":
        return [
            "set UNIGROK_SWARM_EVALS_LIVE=1 to acknowledge real CLI quota and wall time"
        ]
    errors = []
    if swarm_config.swarm_mode() != "dry_run":
        errors.append("set UNIGROK_SWARM=dry_run; live sweeps must never enable Apply")
    if is_cloudrun_runtime():
        errors.append("live Swarm evals are unavailable in Cloud Run")
    if not PathResolver.contributor_mode():
        errors.append("run in contributor mode (UNIGROK_CONTRIBUTOR_MODE=1)")
    workspace = PathResolver.get_workspace_root()
    if workspace is None or not Path(workspace).is_dir():
        errors.append("attach an existing workspace (WORKSPACE_ROOT)")
    cli = grok_cli_plane_status(timeout_sec=10.0, force=True)
    if not cli.get("ready"):
        errors.append(
            "the Docker contributor CLI plane is not ready; authenticate it before the sweep"
        )
    return errors


async def run_target(
    spec: TargetSpec,
    *,
    timeout: float,
    cancel_grace: float,
) -> tuple[Dict[str, Any], bool]:
    task_id, message = await swarm_tools._launch_code_swarm(
        spec.target_path,
        spec.focus_node,
        spec.test_target,
        spec.bench_command,
    )
    if task_id is None:
        raise RuntimeError(message)

    runner = swarm_tools._get_runner()
    completed = await runner.wait(task_id, timeout=timeout)
    timed_out = not completed
    if timed_out:
        await swarm_tools.cancel_swarm(task_id)
        await runner.wait(task_id, timeout=cancel_grace)

    raw = await swarm_tools.get_swarm_status(task_id, view="json")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{spec.name}: status payload is not an object")
    return payload, timed_out


def payload_errors(payload: Dict[str, Any], *, timed_out: bool = False) -> List[str]:
    errors = []
    if payload.get("format") != "unigrok-swarm-status-v2":
        errors.append("unexpected status format")
    status = str(payload.get("status") or "unknown")
    if timed_out:
        errors.append("timed out; cancellation requested and partial results retained")
    if status != "completed":
        errors.append(f"terminal status is {status!r}, expected 'completed'")
    aggregates = payload.get("aggregates") or {}
    budget = payload.get("budget") or {}
    candidates_total = int(aggregates.get("candidates_total") or 0)
    if candidates_total <= 0:
        errors.append("no persisted candidates were evaluated")
    aggregate_cost = float(aggregates.get("cost_to_optimize_usd") or 0.0)
    spent = float(budget.get("spent_usd") or 0.0)
    costs_valid = (
        math.isfinite(aggregate_cost)
        and math.isfinite(spent)
        and aggregate_cost == 0.0
        and spent == 0.0
    )
    if not costs_valid:
        errors.append(
            "invalid or nonzero metered cost violates the exact-zero CLI contract "
            f"(aggregate=${aggregate_cost:.6f}, spent=${spent:.6f})"
        )
    if math.isfinite(aggregate_cost) and math.isfinite(spent) and aggregate_cost != spent:
        errors.append("aggregate cost and durable budget spend disagree")
    return errors


def resolve_output(workspace: Path, value: Optional[Path]) -> Path:
    candidate = value or Path("evals/out/swarm_sweep.md")
    resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("report output must stay inside the attached workspace") from exc
    return resolved


def _metric(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def render_report(results: List[SweepResult]) -> str:
    lines = [
        "# UniGrok Swarm golden-target sweep",
        "",
        "Candidates are persisted candidates; duplicate/no-op free discards are not counted.",
        "",
        "| target | status | persisted candidates | feasibility | latency win | memory win | metered cost | result |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        payload = result.payload or {}
        aggregates = payload.get("aggregates") or {}
        errors = result.errors or []
        note = "; ".join(errors).replace("|", "\\|") if errors else "OK"
        lines.append(
            f"| {result.target} | {payload.get('status') or '—'} "
            f"| {_metric(aggregates.get('candidates_total'))} "
            f"| {_metric(aggregates.get('feasibility_rate'))} "
            f"| {_metric(aggregates.get('best_latency_improvement_pct'), '%')} "
            f"| {_metric(aggregates.get('best_memory_improvement_pct'), '%')} "
            f"| ${float(aggregates.get('cost_to_optimize_usd') or 0.0):.4f} "
            f"| {note} |"
        )
    return "\n".join(lines) + "\n"


async def async_main(options: argparse.Namespace) -> int:
    errors = gate_errors()
    if errors:
        for error in errors:
            print(f"gate failed: {error}", file=sys.stderr)
        return 2

    workspace = PathResolver.get_workspace_root()
    assert workspace is not None
    try:
        targets = discover_targets(Path(workspace))
        output = resolve_output(Path(workspace), options.out)
    except ValueError as exc:
        print(f"sweep setup failed: {exc}", file=sys.stderr)
        return 2

    results: List[SweepResult] = []
    try:
        for spec in targets:
            try:
                payload, timed_out = await run_target(
                    spec,
                    timeout=options.timeout,
                    cancel_grace=options.cancel_grace,
                )
                found = payload_errors(payload, timed_out=timed_out)
                results.append(SweepResult(spec.name, payload, found))
                if timed_out:
                    break  # never overlap a timed-out target with the next one
            except Exception as exc:  # noqa: BLE001 - one row records the failure
                results.append(
                    SweepResult(spec.name, {}, [f"{type(exc).__name__}: {exc}"])
                )
                break

        report = render_report(results)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(report, end="")
        print(f"report: {output}")
        return 1 if any(result.errors for result in results) else 0
    finally:
        # A timed-out generation may still be inside one provider call. Hard
        # cancellation here prevents interpreter shutdown from leaving a
        # background task racing the SQLite close.
        await swarm_tools._shutdown_runner()
        await swarm_tools.store.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per target")
    parser.add_argument(
        "--cancel-grace",
        type=float,
        default=150.0,
        help="seconds to wait after cooperative cancellation",
    )
    parser.add_argument("--out", type=Path, default=None, help="markdown report path")
    return asyncio.run(async_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
