"""MCP tools for the swarm code optimizer (contributor-mode only).

Public surface: start_code_swarm, get_swarm_status, apply_swarm_winner,
cancel_swarm. Every tool is triple-gated (contributor mode + attached
workspace + not Cloud Run) — the stable public MCP is workspace-neutral and
must never mutate a caller's files. apply_swarm_winner is additionally gated on
UNIGROK_SWARM=active and guarded by the base_file_hash staleness check plus
post-apply re-verification, so a candidate can never land over a changed file
or leave the tests broken.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import signal
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..swarm import config as swarm_config
from ..swarm.ast_utils import (
    apply_byte_replacement,
    extract_node_span,
    parse_ok,
    signature_fingerprint,
)
from ..swarm.pareto import rank_candidates
from ..swarm.runner import SwarmRunner, effective_status
from ..utils import (
    GrokInvocationContext,
    PathResolver,
    is_cloudrun_runtime,
    redact_secrets,
    register_internal_tool,
    run_blocking,
    store,
)

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE_TOOL = ToolAnnotations(destructiveHint=True)

import logging

logger = logging.getLogger("GrokMCP")

_RUNNER: Optional[SwarmRunner] = None


def _get_runner() -> SwarmRunner:
    global _RUNNER
    if _RUNNER is None:
        state_base = PathResolver.get_state_base_dir() or PathResolver.get_service_root()
        _RUNNER = SwarmRunner(store, Path(state_base))
    return _RUNNER


def _gate() -> Optional[str]:
    """Return a refusal string when the swarm may not run here, else None."""
    if swarm_config.swarm_mode() == "off":
        return (
            "The swarm optimizer is off. Set UNIGROK_SWARM=dry_run (search + "
            "score, never apply) or active (apply enabled) to use it."
        )
    if is_cloudrun_runtime():
        return "The swarm optimizer is unavailable in the Cloud Run runtime."
    if not PathResolver.contributor_mode():
        return (
            "The swarm optimizer is a contributor-mode feature; the stable "
            "service is workspace-neutral and cannot mutate project files."
        )
    if PathResolver.get_workspace_root() is None:
        return "The swarm optimizer needs an attached workspace."
    return None


def _resolve_target(target_path: str) -> Path:
    """Resolve a workspace-relative target, refusing traversal and non-.py."""
    workspace = PathResolver.get_workspace_root()
    assert workspace is not None  # _gate() guarantees this
    candidate = (workspace / target_path).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"target_path escapes the workspace: {target_path!r}")
    if candidate.suffix != ".py":
        raise ValueError("the swarm optimizer only handles Python (.py) targets in v1")
    if not candidate.is_file():
        raise FileNotFoundError(f"target not found: {target_path}")
    return candidate


def _resolve_workspace_input(path_value: str, label: str) -> Path:
    """Resolve an existing test/bench input inside the attached workspace."""
    workspace = PathResolver.get_workspace_root()
    assert workspace is not None
    raw = str(path_value or "")
    if not raw or raw.startswith("-"):
        raise ValueError(f"{label} must be a workspace-relative path")
    candidate = (workspace / raw).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"{label} escapes the workspace: {path_value!r}")
    if not candidate.exists():
        raise FileNotFoundError(f"{label} not found: {path_value}")
    return candidate


def _validate_test_target(test_target: str) -> None:
    # Preserve pytest node ids while validating the file/directory prefix.
    path_part = str(test_target or "").split("::", 1)[0]
    _resolve_workspace_input(path_part, "test_target")


def _parse_bench_command(bench_command: str) -> List[str]:
    """Accept only a workspace Python script plus literal argv.

    Arbitrary executables or ``python -c`` would turn a model-callable tool
    into a generic command runner. The runner substitutes its scrubbed sandbox
    interpreter for the user-facing ``python`` token.
    """
    argv = shlex.split(bench_command)
    if len(argv) < 2 or Path(argv[0]).name not in {"python", "python3"}:
        raise ValueError(
            "bench_command must be 'python <workspace-relative-script.py> [args...]'"
        )
    script = argv[1]
    if script.startswith("-") or Path(script).suffix != ".py":
        raise ValueError("bench_command must name a Python script, not -c/-m or a module")
    _resolve_workspace_input(script, "benchmark script")
    return argv[1:]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def start_code_swarm(
    target_path: str,
    focus_node: str,
    test_target: str,
    bench_command: str,
    budget_usd: Optional[float] = None,
    allow_unstable_bench: bool = False,
) -> str:
    """Launch a swarm that searches rewrites of ONE focus function for
    latency/memory wins verified by your tests. Returns a task id to poll with
    get_swarm_status. focus_node is 'function:<name>' or 'method:<Class>.<name>';
    test_target and bench_command define the correctness oracle and the
    benchmark (the command must print a single SWARM_BENCH JSON line —
    scripts/swarm_bench.py is the easy path)."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        refusal = _gate()
        if refusal:
            return ctx.format_output(refusal)
        try:
            target = _resolve_target(target_path)
            source = target.read_bytes()
            if not parse_ok(source):
                return ctx.format_output(f"target {target_path!r} does not parse.")
            extract_node_span(source, focus_node)  # validate focus now, not mid-run
            _validate_test_target(test_target)
            bench_args = _parse_bench_command(bench_command)
        except (ValueError, FileNotFoundError) as exc:
            return ctx.format_output(f"cannot start swarm: {exc}")

        budget = swarm_config.swarm_default_budget_usd() if budget_usd is None else float(budget_usd)
        budget = max(0.0, min(budget, swarm_config.swarm_max_budget_usd()))
        # Deterministic per-task seed (Date/random are unavailable to workflow
        # scripts, but here a stable hash of the task identity suffices and
        # keeps the run reproducible from its receipt).
        task_id = uuid.uuid4().hex
        seed = int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16)
        workspace = PathResolver.get_workspace_root()
        target_rel = str(target.relative_to(workspace.resolve()))

        await store.create_swarm_task(
            task_id,
            target_path=target_rel,
            focus_node=focus_node,
            base_file_hash=_file_hash(target),
            test_target=test_target,
            bench_command=bench_command,
            budget_usd=budget,
            seed=seed,
        )
        _get_runner().launch(task_id, {
            "workspace_root": workspace,
            "target_rel": target_rel,
            "focus_node": focus_node,
            "test_target": test_target,
            "bench_args": bench_args,
            "budget_usd": budget,
            "seed": seed,
            "allow_unstable_bench": bool(allow_unstable_bench),
            "goal": f"optimize {focus_node} in {target_rel}",
        })
        return ctx.format_output(
            f"Swarm `{task_id}` started on `{focus_node}` in `{target_rel}` "
            f"(mode={swarm_config.swarm_mode()}, budget=${budget:.2f}). "
            f"Poll with get_swarm_status('{task_id}')."
        )


async def get_swarm_status(task_id: str) -> str:
    """Report a swarm's status, the oracle-honesty facts (focus-span coverage,
    bench stability), the current Pareto front with relative deltas, and
    spend."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        task = await store.get_swarm_task(task_id)
        if not task:
            return ctx.format_output(f"no swarm task `{task_id}`.")
        status = effective_status(task)
        oracle = _load_json(task.get("oracle_json"))
        baseline = _load_json(task.get("baseline_json"))
        candidates = await store.list_swarm_candidates(task_id, feasible_only=True)
        front = _current_front(candidates)

        lines = [
            f"# Swarm `{task_id}`",
            f"- **Status:** `{status}`  **Mode:** `{swarm_config.swarm_mode()}`",
            f"- **Target:** `{task['focus_node']}` in `{task['target_path']}`",
            f"- **Spend:** ${float(task.get('spent_usd') or 0):.4f} / ${float(task.get('budget_usd') or 0):.2f}",
            f"- **Generations run:** {task.get('generation') or 0}",
        ]
        if oracle:
            cov = oracle.get("focus_coverage_pct")
            bench = oracle.get("bench") or {}
            lines.append(
                "- **Oracle honesty:** "
                f"focus-span coverage {cov if cov is not None else 'n/a'}%, "
                f"bench {bench.get('stability', 'n/a')} "
                f"(provenance {oracle.get('import_provenance', 'n/a')})"
            )
            if oracle.get("error"):
                lines.append(f"- **Error:** {redact_secrets(str(oracle['error']))[:300]}")
        base_latency = float((baseline or {}).get("latency_ms") or 0.0)
        if front:
            lines.append(f"\n## Pareto front ({len(front)} candidate(s))")
            lines.append("| candidate | mutator | latency_ms | Δlatency | peak_mem | diff |")
            lines.append("| :-- | :-- | --: | --: | --: | --: |")
            for c in sorted(front, key=lambda x: float(x.get("latency_ms") or 0)):
                lat = float(c.get("latency_ms") or 0.0)
                delta = f"{(base_latency - lat) / base_latency * 100:.1f}%" if base_latency > 0 else "—"
                lines.append(
                    f"| `{c['id']}` | {c['mutator']} | {lat:.2f} | {delta} | "
                    f"{c.get('peak_mem_bytes')} | {c.get('diff_bytes')} |"
                )
            if swarm_config.swarm_mode() == "active":
                lines.append(
                    "\nApply the winner with "
                    f"`apply_swarm_winner('{front[0]['id']}')` (re-verified before it lands)."
                )
            else:
                lines.append("\n(dry_run: apply is disabled — set UNIGROK_SWARM=active to apply.)")
        elif status in ("completed", "cancelled"):
            lines.append("\nNo feasible improvement found.")
        return ctx.format_output("\n".join(lines))


async def apply_swarm_winner(candidate_id: str) -> str:
    """Splice a winning candidate into the live workspace file (contributor +
    active mode only). Guarded by a base_file_hash staleness check and
    post-apply re-verification: if the file changed since the swarm ran, or the
    candidate breaks the tests, the original bytes are restored and nothing
    lands. Never commits — that stays with you."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        refusal = _gate()
        if refusal:
            return ctx.format_output(refusal)
        if swarm_config.swarm_mode() != "active":
            return ctx.format_output(
                "apply is disabled in dry_run; set UNIGROK_SWARM=active to apply."
            )
        task_id = candidate_id.rsplit("-g", 1)[0] if "-g" in candidate_id else None
        candidate = await _find_candidate(task_id, candidate_id)
        if candidate is None:
            return ctx.format_output(f"no candidate `{candidate_id}`.")
        task = await store.get_swarm_task(candidate["task_id"])
        if not task:
            return ctx.format_output("owning swarm task not found.")
        feasible = await store.list_swarm_candidates(task["id"], feasible_only=True)
        front_ids = {c["id"] for c in _current_front(feasible)}
        if candidate_id not in front_ids:
            return ctx.format_output(
                "refusing to apply: the candidate is not on the current verified Pareto front."
            )
        try:
            target = _resolve_target(task["target_path"])
        except (ValueError, FileNotFoundError) as exc:
            return ctx.format_output(f"cannot apply: {exc}")

        live = target.read_bytes()
        if hashlib.sha256(live).hexdigest() != task["base_file_hash"]:
            return ctx.format_output(
                "the target file changed since the swarm ran — its byte spans no "
                "longer apply. Re-run the swarm on the current file."
            )
        replacement = candidate["code"].encode("utf-8")
        patched = apply_byte_replacement(
            live, int(candidate["byte_start"]), int(candidate["byte_end"]), replacement
        )
        if not parse_ok(patched):
            return ctx.format_output("refusing to apply: the result would not parse.")
        if signature_fingerprint(patched, task["focus_node"]) != signature_fingerprint(
            live, task["focus_node"]
        ):
            return ctx.format_output(
                "refusing to apply: the candidate changes the callable signature."
            )
        target.write_bytes(patched)

        passed, output = await _reverify(task, target, live)
        if not passed:
            return ctx.format_output(
                "applied change FAILED post-apply verification and was reverted:\n"
                + redact_secrets(output)[-800:]
            )
        return ctx.format_output(
            f"Applied `{candidate_id}` to `{task['target_path']}` and re-verified "
            f"({task['test_target']} passes). Review and commit yourself — the swarm "
            "never commits. Note: this invalidates every other candidate for this "
            "file (its hash changed); re-run the swarm for further optimization."
        )


async def cancel_swarm(task_id: str) -> str:
    """Cooperatively cancel a running swarm; the partial Pareto front is kept."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        task = await store.get_swarm_task(task_id)
        if not task:
            return ctx.format_output(f"no swarm task `{task_id}`.")
        _get_runner().cancel(task_id)
        return ctx.format_output(
            f"Cancel requested for `{task_id}`; it will stop after the current "
            "candidate and retain results so far."
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _find_candidate(task_id: Optional[str], candidate_id: str) -> Optional[Dict[str, Any]]:
    if task_id:
        for c in await store.list_swarm_candidates(task_id):
            if c["id"] == candidate_id:
                return c
    # Fallback: scan recent tasks (candidate id encodes the task, but be robust).
    for task in await store.list_swarm_tasks(limit=50):
        for c in await store.list_swarm_candidates(task["id"]):
            if c["id"] == candidate_id:
                return c
    return None


def _current_front(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = rank_candidates([dict(candidate) for candidate in candidates])
    return [candidate for candidate in ranked if candidate.get("pareto_rank") == 0]


async def _reverify(task: Dict[str, Any], target: Path, original: bytes) -> tuple:
    """Run the task's test_target against the LIVE workspace; restore original
    bytes on failure."""
    workspace = PathResolver.get_workspace_root()
    python = str((workspace / ".venv" / "bin" / "python")) if (workspace / ".venv").exists() else "python3"
    proc = None
    runtime_dir = tempfile.TemporaryDirectory(prefix="unigrok-reverify-")
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {
            "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "USER", "SHELL",
            "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
        }
    }
    env["HOME"] = runtime_dir.name
    env["TMPDIR"] = runtime_dir.name
    env["PYTHONPATH"] = str(workspace)
    env["PYTHONHASHSEED"] = "0"
    try:
        proc = await asyncio.create_subprocess_exec(
            python, "-m", "pytest", "-q", "-p", "no:cacheprovider", task["test_target"],
            cwd=str(workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=swarm_config.swarm_eval_timeout()
        )
        passed = proc.returncode == 0
        output = (out + err).decode("utf-8", errors="replace")
    except asyncio.TimeoutError as exc:
        if proc is not None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
        passed, output = False, f"re-verification error: {exc}"
    except OSError as exc:
        passed, output = False, f"re-verification error: {exc}"
    finally:
        runtime_dir.cleanup()
    if not passed:
        target.write_bytes(original)
    return passed, output


def register_swarm_tools(mcp: FastMCP) -> None:
    mcp.add_tool(start_code_swarm)
    mcp.add_tool(get_swarm_status, annotations=READONLY_TOOL)
    mcp.add_tool(apply_swarm_winner, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(cancel_swarm)


register_internal_tool("start_code_swarm", start_code_swarm)
register_internal_tool("get_swarm_status", get_swarm_status)
register_internal_tool("apply_swarm_winner", apply_swarm_winner)
register_internal_tool("cancel_swarm", cancel_swarm)
