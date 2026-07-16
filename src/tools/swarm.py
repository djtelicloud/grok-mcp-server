"""MCP tools for the swarm code optimizer (contributor-mode only).

Public surface: analyze_code_for_swarm, start_code_swarm, start_paste_swarm,
get_swarm_status, list_swarm_tasks, apply_swarm_winner, cancel_swarm, export_swarm_narrow_pr. Mutating tools are triple-gated (contributor mode + attached
workspace + not Cloud Run) — the stable public MCP is workspace-neutral and
must never mutate a caller's files. apply_swarm_winner is additionally gated on
UNIGROK_SWARM=active and guarded by the base_file_hash staleness check plus
post-apply re-verification, so a candidate can never land over a changed file
or leave the tests broken.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import math
import os
import shlex
import signal
import tempfile
import textwrap

from ..subprocess_security import create_scrubbed_subprocess_exec
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..swarm import config as swarm_config
from ..swarm.analytics import analyze_python_source, analyze_python_source_full
from ..swarm.ast_utils import (
    apply_byte_replacement,
    extract_node_span,
    parse_ok,
    signature_fingerprint,
)
from ..swarm.pareto import rank_candidates, select_champion
from ..swarm.runner import SwarmRunner, effective_status
from ..utils import (
    GrokInvocationContext,
    PathResolver,
    is_cloudrun_runtime,
    redact_secrets,
    register_internal_tool,
    store,
)

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE_TOOL = ToolAnnotations(destructiveHint=True)

logger = logging.getLogger("GrokMCP")

_RUNNER: Optional[SwarmRunner] = None


def _get_runner() -> SwarmRunner:
    global _RUNNER
    if _RUNNER is None:
        state_base = PathResolver.get_state_base_dir() or PathResolver.get_service_root()
        _RUNNER = SwarmRunner(store, Path(state_base))
    return _RUNNER


async def _shutdown_runner() -> None:
    """Drain the process-local runner for one-shot contributor scripts."""
    if _RUNNER is not None:
        await _RUNNER.shutdown()


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


def _paste_gate() -> Optional[str]:
    """Paste execution needs local contributor authority, not a workspace."""
    if swarm_config.swarm_mode() == "off":
        return (
            "The swarm optimizer is off. Set UNIGROK_SWARM=dry_run to search "
            "without Apply or active to enable guarded workspace Apply."
        )
    if is_cloudrun_runtime():
        return "Pasted code execution is unavailable in the Cloud Run runtime."
    if not PathResolver.contributor_mode():
        return "Verified paste search is available only in the local contributor Forge."
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


async def analyze_code_for_swarm(code: str, language: str = "python") -> str:
    """Analyze pasted Python without a model call, import, or user-code execution.

    The source is capped at 256 KiB, read only from this request, and never
    persisted. Cloud Run refuses this server-side tool because the public
    page performs its preview entirely in the browser.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:  # noqa: F841
        if is_cloudrun_runtime():
            return json.dumps(
                {"error": "server-side paste analysis is unavailable in Cloud Run; use the client-side preview"},
                separators=(",", ":"),
            )
        if str(language or "").strip().lower() != "python":
            return json.dumps(
                {"error": "Swarm analysis currently supports language='python' only"},
                separators=(",", ":"),
            )
        try:
            payload = await analyze_python_source_full(code)
        except (TypeError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":"))
        return json.dumps(payload, separators=(",", ":"))


async def _launch_code_swarm(
    target_path: str,
    focus_node: str,
    test_target: str,
    bench_command: str,
    budget_usd: Optional[float] = None,
    allow_unstable_bench: bool = False,
    search_strategy: str = "baseline_batch",
    primary_goal: str = "balanced",
) -> tuple[Optional[str], str]:
    """Internal typed launch seam shared by the MCP tool and live sweeps."""
    refusal = _gate()
    if refusal:
        return None, refusal
    try:
        target = _resolve_target(target_path)
        source = target.read_bytes()
        if not parse_ok(source):
            return None, f"target {target_path!r} does not parse."
        extract_node_span(source, focus_node)  # validate focus now, not mid-run
        _validate_test_target(test_target)
        bench_args = _parse_bench_command(bench_command)
        strategy = swarm_config.validate_search_strategy(search_strategy)
        goal = swarm_config.validate_primary_goal(primary_goal)
        analytics = await analyze_python_source_full(source.decode("utf-8"))
        analytics["source"] = "workspace"
    except (ValueError, FileNotFoundError) as exc:
        return None, f"cannot start swarm: {exc}"

    budget = swarm_config.swarm_default_budget_usd() if budget_usd is None else float(budget_usd)
    budget = max(0.0, min(budget, swarm_config.swarm_max_budget_usd()))
    # Deterministic per-task seed (Date/random are unavailable to workflow
    # scripts, but here a stable hash of the task identity suffices and keeps
    # the run reproducible from its receipt).
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
        search_strategy=strategy,
        primary_goal=goal,
        input_kind="workspace",
        analytics_json=json.dumps(analytics, separators=(",", ":")),
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
        "goal": f"optimize {focus_node} in {target_rel} for {goal}",
        "search_strategy": strategy,
        "primary_goal": goal,
    })
    return task_id, (
        f"Swarm `{task_id}` started on `{focus_node}` in `{target_rel}` "
        f"(mode={swarm_config.swarm_mode()}, strategy={strategy}, goal={goal}, "
        f"budget=${budget:.2f}). "
        f"Poll with get_swarm_status('{task_id}')."
    )


async def start_code_swarm(
    target_path: str,
    focus_node: str,
    test_target: str,
    bench_command: str,
    budget_usd: Optional[float] = None,
    allow_unstable_bench: bool = False,
    search_strategy: str = "baseline_batch",
    primary_goal: str = "balanced",
) -> str:
    """Launch a swarm that searches rewrites of ONE focus function for
    latency/memory wins verified by your tests. Returns a task id to poll with
    get_swarm_status. focus_node is 'function:<name>' or 'method:<Class>.<name>';
    test_target and bench_command define the correctness oracle and the
    benchmark (the command must print a single SWARM_BENCH JSON line —
    scripts/swarm_bench.py is the easy path)."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        _task_id, message = await _launch_code_swarm(
            target_path,
            focus_node,
            test_target,
            bench_command,
            budget_usd=budget_usd,
            allow_unstable_bench=allow_unstable_bench,
            search_strategy=search_strategy,
            primary_goal=primary_goal,
        )
        return ctx.format_output(message)


async def start_paste_swarm(
    code: str,
    test_code: str,
    bench_code: str,
    focus_node: str,
    budget_usd: Optional[float] = None,
    allow_unstable_bench: bool = False,
    search_strategy: str = "elite_offspring",
    primary_goal: str = "balanced",
) -> str:
    """Run a verified local swarm over pasted Python, tests, and benchmark.

    Source material is written only to a task-scoped local scratch directory.
    Tests and a benchmark are mandatory; examples never count as proof. Paste
    tasks return copyable champions but cannot use workspace Apply.
    """
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        refusal = _paste_gate()
        if refusal:
            return ctx.format_output(refusal)
        try:
            if not isinstance(code, str) or not code.strip():
                raise ValueError("code is required")
            if not isinstance(test_code, str) or not test_code.strip():
                raise ValueError("test_code is required for verification")
            if not isinstance(bench_code, str) or not bench_code.strip():
                raise ValueError("bench_code is required for measurement")
            if len(code.encode("utf-8")) > 256 * 1024:
                raise ValueError("code exceeds the 256 KiB cap")
            if len(test_code.encode("utf-8")) > 128 * 1024:
                raise ValueError("test_code exceeds the 128 KiB cap")
            if len(bench_code.encode("utf-8")) > 64 * 1024:
                raise ValueError("bench_code exceeds the 64 KiB cap")
            if any(redact_secrets(value) != value for value in (code, test_code, bench_code)):
                raise ValueError("remove secret-like content before local search")
            source = code.encode("utf-8")
            if not parse_ok(source):
                raise ValueError("pasted code does not parse")
            extract_node_span(source, focus_node)
            compile(test_code, "test_focus.py", "exec")
            compile(bench_code, "bench_focus.py", "exec")
            strategy = swarm_config.validate_search_strategy(search_strategy)
            goal = swarm_config.validate_primary_goal(primary_goal)
            analytics = await analyze_python_source_full(code)
        except (SyntaxError, TypeError, UnicodeError, ValueError) as exc:
            return ctx.format_output(f"cannot start paste swarm: {exc}")

        task_id = uuid.uuid4().hex
        seed = int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16)
        state_base = PathResolver.get_state_base_dir() or PathResolver.get_service_root()
        scratch = Path(state_base) / "swarm-paste" / task_id
        scratch.mkdir(parents=True, exist_ok=False)
        (scratch / "module_under_test.py").write_text(code, encoding="utf-8")
        (scratch / "test_focus.py").write_text(test_code, encoding="utf-8")
        (scratch / "bench_focus.py").write_text(bench_code, encoding="utf-8")
        analytics["source"] = "paste"
        # Paste swarm supplies its own test + benchmark, so the scored-search
        # requirements are satisfied and there are no blockers.
        analytics["searchability"] = {
            "ready": True,
            "blockers": [],
            "scored_search_requirements": [],
        }
        budget = swarm_config.swarm_default_budget_usd() if budget_usd is None else float(budget_usd)
        budget = max(0.0, min(budget, swarm_config.swarm_max_budget_usd()))

        await store.create_swarm_task(
            task_id,
            target_path=f"paste://{task_id}/module_under_test.py",
            focus_node=focus_node,
            base_file_hash=hashlib.sha256(source).hexdigest(),
            test_target="test_focus.py",
            bench_command="python bench_focus.py",
            budget_usd=budget,
            seed=seed,
            search_strategy=strategy,
            primary_goal=goal,
            input_kind="paste",
            analytics_json=json.dumps(analytics, separators=(",", ":")),
        )
        _get_runner().launch(
            task_id,
            {
                "workspace_root": scratch,
                "target_rel": "module_under_test.py",
                "focus_node": focus_node,
                "test_target": "test_focus.py",
                "bench_args": ["bench_focus.py"],
                "budget_usd": budget,
                "seed": seed,
                "allow_unstable_bench": bool(allow_unstable_bench),
                "goal": f"optimize pasted {focus_node} for {goal}",
                "search_strategy": strategy,
                "primary_goal": goal,
            },
        )
        return ctx.format_output(
            f"Paste swarm `{task_id}` started for `{focus_node}` "
            f"(strategy={strategy}, goal={goal}, mode={swarm_config.swarm_mode()}). "
            f"Poll with get_swarm_status('{task_id}')."
        )


async def get_swarm_status(task_id: str, view: Literal["text", "json"] = "text") -> str:
    """Report a swarm's status, the oracle-honesty facts (focus-span coverage,
    bench stability), the current Pareto front with relative deltas, and
    spend. ``view="json"`` returns the stable machine-readable payload
    (format ``unigrok-swarm-status-v2``) that the local workbench and any
    static export consume — one call renders the whole run.

    The JSON schema is deliberately honest: it carries ONLY measured values.
    No ``instructions_retired``/``allocated_blocks`` (hardware counters are
    the OptiBench harness's domain, not measurable on this stack), no
    ``semantic_*`` scores (no judge exists in the v1 funnel by contract), and
    no invented cost comparisons — ``aggregates`` are computed from the same
    SQLite rows the text view reads."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:
        task = await store.get_swarm_task(task_id)
        if not task:
            if view == "json":
                return json.dumps({"error": f"no swarm task {task_id}"}, separators=(",", ":"))
            return ctx.format_output(f"no swarm task `{task_id}`.")
        status = effective_status(task)
        oracle = _load_json(task.get("oracle_json"))
        baseline = _load_json(task.get("baseline_json"))
        candidates = await store.list_swarm_candidates(task_id, feasible_only=True)
        front = _current_front(candidates)

        if view == "json":
            return json.dumps(
                await _status_payload(task, status, oracle, baseline, front),
                separators=(",", ":"),
                allow_nan=False,
            )

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
                champion = select_champion(front, str(task.get("primary_goal") or "balanced"))
                champion_id = champion["id"] if champion else front[0]["id"]
                lines.append(
                    "\nApply the winner with "
                    f"`apply_swarm_winner('{champion_id}')` (re-verified before it lands)."
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
        if (task.get("input_kind") or "workspace") == "paste":
            return ctx.format_output(
                "paste swarms are copy-only: copy the Best verified candidate or "
                "start a workspace swarm to use guarded Apply."
            )
        task_status = effective_status(task)
        if task_status not in ("completed", "cancelled"):
            return ctx.format_output(
                "refusing to apply: the swarm is still running; wait for completion "
                "or cancel it before applying a verified Pareto candidate."
            )
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


async def list_swarm_tasks(limit: int = 10) -> str:
    """List recent swarm tasks newest-first as a JSON array (id, effective
    status incl. staleness override, target, focus node, generations run,
    spend). The Playground's task picker consumes this — read-only, no gate:
    on a service that never ran a swarm it simply returns []."""
    async with GrokInvocationContext("utility", logger, append_signature=False) as ctx:  # noqa: F841
        rows = await store.list_swarm_tasks(limit=max(1, min(int(limit or 10), 50)))
        items = [
            {
                "task_id": row["id"],
                "status": effective_status(row),
                "target": row.get("target_path"),
                "focus_node": row.get("focus_node"),
                "generations": int(row.get("generation") or 0),
                "spent_usd": float(row.get("spent_usd") or 0.0),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]
        return json.dumps(items, separators=(",", ":"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _json_safe(value: Any) -> Any:
    """Normalize legacy non-finite receipt numbers for strict browser JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


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


_STATIC_WALL_STAGES = ("generated", "parse", "signature", "compile", "lint")


def _candidate_outcome(candidate: Dict[str, Any], front_ids: set) -> str:
    """Selection outcome the UI colors by: pareto_elite (green) >
    dominated (gray) > test_wall (orange, ran but broke the oracle) >
    static_wall (red, never survived the free filters)."""
    if candidate["id"] in front_ids:
        return "pareto_elite"
    if candidate.get("feasible"):
        return "dominated"
    if candidate.get("stage_reached") in _STATIC_WALL_STAGES:
        return "static_wall"
    return "test_wall"


async def _status_payload(
    task: Dict[str, Any],
    status: str,
    oracle: Dict[str, Any],
    baseline: Dict[str, Any],
    front: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """The unigrok-swarm-status-v2 payload: every measured fact of one run,
    grouped generation-by-generation so a frontend can replay the swarm
    without further backend calls. Candidate ``code`` rides ONLY on Pareto
    elites (bounded payload; the detail view exists for winners), and the
    live original span is included only while base_file_hash still matches —
    never a stale slice."""
    all_candidates = await store.list_swarm_candidates(str(task["id"]))
    front_ids = {c["id"] for c in front}
    front_code = {c["id"]: c.get("code") for c in front}
    champion = select_champion(front, str(task.get("primary_goal") or "balanced"))
    champion_id = str(task.get("champion_id") or "") or (
        str(champion["id"]) if champion else None
    )

    generations: Dict[int, List[Dict[str, Any]]] = {}
    feasible_count = 0
    for row in all_candidates:
        entry = {
            "candidate_id": row["id"],
            "arm": row.get("mutator"),
            "stage": row.get("stage_reached"),
            "outcome": _candidate_outcome(row, front_ids),
            "feasible": bool(row.get("feasible")),
            "latency_ms": row.get("latency_ms"),
            "peak_mem_bytes": row.get("peak_mem_bytes"),
            "diff_bytes": row.get("diff_bytes"),
            "reward": row.get("reward"),
            "token_cost_usd": float(row.get("gen_cost_usd") or 0.0),
            "arm_receipt": _json_safe(_load_json(row.get("arm_receipt"))) or None,
            "parent_id": row.get("parent_id"),
            "parent_code_hash": row.get("parent_code_hash"),
            "origin": row.get("origin") or "llm",
            "transform": row.get("transform"),
        }
        if row["id"] in front_ids:
            entry["code"] = front_code.get(row["id"])
        if entry["feasible"]:
            feasible_count += 1
        generations.setdefault(int(row.get("generation") or 0), []).append(entry)

    base_latency = float((baseline or {}).get("latency_ms") or 0.0)
    base_mem = float((baseline or {}).get("peak_mem_bytes") or 0.0)
    best_latency_pct = None
    best_mem_pct = None
    if front and base_latency > 0:
        best = min(float(c.get("latency_ms") or base_latency) for c in front)
        best_latency_pct = round((base_latency - best) / base_latency * 100.0, 2)
    if front and base_mem > 0:
        best = min(float(c.get("peak_mem_bytes") or base_mem) for c in front)
        best_mem_pct = round((base_mem - best) / base_mem * 100.0, 2)

    generations_run = int(task.get("generation") or 0)
    last_generation = max([generations_run, *generations.keys()], default=0)
    original_span, span_stale = _live_original_span(task)
    safe_oracle = dict(oracle) if oracle else None
    if safe_oracle and safe_oracle.get("error") is not None:
        safe_oracle["error"] = redact_secrets(str(safe_oracle["error"]))[:400]
    return {
        "format": "unigrok-swarm-status-v2",
        "task_id": task["id"],
        "status": status,
        "mode": swarm_config.swarm_mode(),
        "input_kind": task.get("input_kind") or "workspace",
        "search_strategy": task.get("search_strategy") or "baseline_batch",
        "primary_goal": task.get("primary_goal") or "balanced",
        "target": {
            "path": task.get("target_path"),
            "focus_node": task.get("focus_node"),
            "test_target": task.get("test_target"),
            "bench_command": task.get("bench_command"),
        },
        "oracle": safe_oracle,
        "analytics": _load_json(task.get("analytics_json")) or None,
        "baseline": baseline or None,
        "budget": {
            "budget_usd": float(task.get("budget_usd") or 0.0),
            "spent_usd": float(task.get("spent_usd") or 0.0),
            "generations_run": generations_run,
        },
        "original_span": original_span,
        "original_span_stale": span_stale,
        "generations": [
            {"generation": gen, "candidates": generations.get(gen, [])}
            for gen in range(1, last_generation + 1)
        ],
        "pareto_front": [
            c["id"] for c in sorted(front, key=lambda x: float(x.get("latency_ms") or 0.0))
        ],
        "champion_id": champion_id,
        "comparison": _comparison_payload(
            task, all_candidates, champion, original_span
        ),
        "aggregates": {
            "candidates_total": len(all_candidates),
            "feasibility_rate": (
                round(feasible_count / len(all_candidates), 4) if all_candidates else None
            ),
            "best_latency_improvement_pct": best_latency_pct,
            "best_memory_improvement_pct": best_mem_pct,
            "cost_to_optimize_usd": float(task.get("spent_usd") or 0.0),
        },
        "folded_state": task.get("folded_state") or None,
    }


def _comparison_payload(
    task: Dict[str, Any],
    all_candidates: List[Dict[str, Any]],
    champion: Optional[Dict[str, Any]],
    original_span: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not champion:
        return None
    by_id = {str(candidate["id"]): candidate for candidate in all_candidates}
    parent = by_id.get(str(champion.get("parent_id") or ""))

    def analytics_metrics(code: Optional[str]) -> Optional[Dict[str, Any]]:
        if not code:
            return None
        measured = analyze_python_source(textwrap.dedent(code))
        functions = measured.get("functions") or []
        if not functions:
            return None
        item = functions[0]
        return {
            key: item.get(key)
            for key in ("loc", "cyclomatic_complexity", "branch_points", "max_nesting")
        }

    def objective(candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidate:
            return None
        return {
            key: candidate.get(key)
            for key in ("latency_ms", "peak_mem_bytes", "diff_bytes")
        }

    champion_code = str(champion.get("code") or "")
    parent_code = str(parent.get("code") or "") if parent else None

    def unified(before: Optional[str], after: str, before_name: str) -> Optional[str]:
        if before is None:
            return None
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=before_name,
                tofile="champion",
                n=3,
            )
        )

    return {
        "original": {
            "code": original_span,
            "analytics": analytics_metrics(original_span),
            "objectives": {
                key: _load_json(task.get("baseline_json")).get(key)
                for key in ("latency_ms", "peak_mem_bytes")
            },
        },
        "parent": (
            {
                "candidate_id": parent.get("id"),
                "code": parent_code,
                "analytics": analytics_metrics(parent_code),
                "objectives": objective(parent),
            }
            if parent
            else None
        ),
        "champion": {
            "candidate_id": champion.get("id"),
            "code": champion_code,
            "analytics": analytics_metrics(champion_code),
            "objectives": objective(champion),
        },
        "diff_from_original": unified(original_span, champion_code, "original"),
        "diff_from_parent": unified(parent_code, champion_code, "parent") if parent else None,
    }


def _live_original_span(task: Dict[str, Any]) -> tuple:
    """(redacted original focus-span text, stale flag). None/True when the
    workspace is unavailable or the file changed since the swarm ran — the
    UI must show a staleness notice instead of a wrong diff."""
    try:
        target = _task_target(task)
        live = target.read_bytes()
    except (ValueError, FileNotFoundError, PermissionError, OSError):
        return None, True
    if hashlib.sha256(live).hexdigest() != task.get("base_file_hash"):
        return None, True
    rows_span = _task_span(task)
    if rows_span is None:
        return None, True
    start, end = rows_span
    if not (0 <= start < end <= len(live)):
        return None, True
    return redact_secrets(live[start:end].decode("utf-8", errors="replace")), False


def _task_span(task: Dict[str, Any]) -> Optional[tuple]:
    """Recover the focus span from the (hash-verified) live file."""
    from ..swarm.ast_utils import extract_node_span

    try:
        target = _task_target(task)
        return extract_node_span(target.read_bytes(), str(task.get("focus_node") or ""))
    except (ValueError, FileNotFoundError, OSError):
        return None


def _task_target(task: Dict[str, Any]) -> Path:
    if (task.get("input_kind") or "workspace") == "paste":
        task_id = str(task.get("id") or "")
        if not task_id or any(character not in "0123456789abcdef" for character in task_id):
            raise ValueError("invalid paste task id")
        state_base = PathResolver.get_state_base_dir() or PathResolver.get_service_root()
        candidate = (Path(state_base) / "swarm-paste" / task_id / "module_under_test.py").resolve()
        root = (Path(state_base) / "swarm-paste" / task_id).resolve()
        candidate.relative_to(root)
        if not candidate.is_file():
            raise FileNotFoundError("paste task source is unavailable")
        return candidate
    return _resolve_target(str(task.get("target_path") or ""))


async def _reverify(task: Dict[str, Any], target: Path, original: bytes) -> tuple:
    """Run the task's test_target against the LIVE workspace; restore original
    bytes on failure."""
    workspace = PathResolver.get_workspace_root()
    import sys
    python = str((workspace / ".venv" / "bin" / "python")) if (workspace / ".venv").exists() else sys.executable
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
        proc = await create_scrubbed_subprocess_exec(
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


async def plan_swarm_campaign(
    target_paths: List[str],
    test_roots: List[str] = ["tests"],
    max_targets: int = 5,
) -> Dict[str, Any]:
    """Perform a wide-pass analysis to find swarmable hotspot functions.
    
    Deterministically maps files/functions to available tests and categorizes
    them into a campaign plan without executing any code.
    
    Args:
        target_paths: Workspace-relative paths to files or directories to scan.
        test_roots: Workspace-relative paths where tests live (default: ["tests"]).
        max_targets: Maximum number of swarm-ready targets to rank and return.
    """
    workspace = PathResolver.get_workspace_root()
    if workspace is None:
        return {"error": "A workspace must be attached to plan a campaign."}
        
    resolved_targets: List[Path] = []
    for tp in target_paths:
        try:
            rp = _resolve_workspace_input(tp, "target_paths element")
            if rp.is_file() and rp.suffix == ".py":
                resolved_targets.append(rp)
            elif rp.is_dir():
                resolved_targets.extend([p for p in rp.rglob("*.py") if p.is_file()])
        except Exception as e:
            logger.warning(f"Skipping {tp}: {e}")
            
    # Remove duplicates
    resolved_targets = list({p.resolve(): p for p in resolved_targets}.values())
    
    candidates = []
    non_swarm = []
    
    for rp in resolved_targets:
        try:
            source = rp.read_text(encoding="utf-8")
            analytics = await analyze_python_source_full(source)
            if not analytics.get("parse_ok"):
                continue
                
            rel_path = str(rp.relative_to(workspace))
            
            for func in analytics.get("functions", []):
                focus_node = func.get("focus_node")
                loc = func.get("loc", 0)
                complexity = func.get("cyclomatic_complexity", 0)
                
                # Filter trivial spans
                if loc < 15 or complexity <= 3:
                    non_swarm.append({
                        "path": rel_path,
                        "focus_node": focus_node,
                        "reason": f"trivial span (loc={loc}, complexity={complexity}) -> ide_edit_not_swarm"
                    })
                    continue
                    
                # Heuristic oracle binding
                base_name = rp.stem
                test_file_candidates = []
                for tr in test_roots:
                    try:
                        tr_path = _resolve_workspace_input(tr, "test_root")
                        if tr_path.is_dir():
                            # Check tests/test_{base_name}.py
                            guess = tr_path / f"test_{base_name}.py"
                            if guess.is_file():
                                test_file_candidates.append(str(guess.relative_to(workspace)))
                    except Exception:
                        pass
                        
                if not test_file_candidates:
                    candidates.append({
                        "target_path": rel_path,
                        "focus_node": focus_node,
                        "searchability": "blocked",
                        "blockers": ["missing_tests"],
                        "signals": {"loc": loc, "complexity": complexity}
                    })
                else:
                    # found tests
                    candidates.append({
                        "target_path": rel_path,
                        "focus_node": focus_node,
                        "searchability": "ready",
                        "blockers": [],
                        "suggested_launch": {
                            "test_target": test_file_candidates[0]
                        },
                        "signals": {"loc": loc, "complexity": complexity}
                    })
        except Exception as e:
            logger.warning(f"Failed to analyze {rp}: {e}")
            
    # Rank ready candidates by complexity * loc
    ready = [c for c in candidates if c["searchability"] == "ready"]
    ready.sort(key=lambda x: x["signals"]["complexity"] * x["signals"]["loc"], reverse=True)
    ready = ready[:max_targets]
    
    # Assign ranks
    for i, c in enumerate(ready):
        c["rank"] = i + 1
        
    blocked = [c for c in candidates if c["searchability"] == "blocked"]
    
    return {
        "format": "unigrok-swarm-campaign-plan-v1",
        "candidates": ready + blocked,
        "non_swarm": non_swarm
    }



async def export_swarm_narrow_pr(task_id: str) -> Dict[str, Any]:
    """Export a narrow PR-shaped payload for the best verified swarm candidate.

    Read-only: builds a unified diff against the live workspace bytes without
    writing files. Used by contributor tooling to hand a single candidate to a
    human/supervisor review packet.
    """
    task = await store.get_swarm_task(task_id)
    if not task:
        return {
            "format": "unigrok-swarm-narrow-pr-v1",
            "task_id": task_id,
            "error": f"no swarm task `{task_id}`",
            "hash_matches": False,
        }

    candidates = await store.list_swarm_candidates(task_id)
    if not candidates:
        return {
            "format": "unigrok-swarm-narrow-pr-v1",
            "task_id": task_id,
            "error": "no candidates",
            "hash_matches": False,
        }

    feasible = [c for c in candidates if c.get("feasible")]
    pool = feasible or candidates
    front = _current_front(feasible) if feasible else pool
    champion = select_champion(front, str(task.get("primary_goal") or "balanced")) if front else None
    champion_id = str(task.get("champion_id") or "") or (champion["id"] if champion else None)
    
    if champion_id:
        candidate = next((c for c in pool if c["id"] == champion_id), champion)
    else:
        candidate = sorted(
            pool,
            key=lambda c: (
                int(c.get("pareto_rank") if c.get("pareto_rank") is not None else 10**9),
                -float(c.get("crowding") or 0.0),
            ),
        )[0]

    target_path = str(task.get("target_path") or "")
    try:
        target = _task_target(task)
        live = target.read_bytes()
    except (ValueError, FileNotFoundError, PermissionError, OSError) as exc:
        return {
            "format": "unigrok-swarm-narrow-pr-v1",
            "task_id": task_id,
            "candidate_id": candidate.get("id"),
            "error": f"cannot read target: {exc}",
            "hash_matches": False,
        }

    hash_matches = hashlib.sha256(live).hexdigest() == task.get("base_file_hash")
    start = int(candidate.get("byte_start") or 0)
    end = int(candidate.get("byte_end") or 0)
    if not (0 <= start <= end <= len(live)):
        start, end = 0, len(live)
    original = live[start:end].decode("utf-8", errors="replace")
    proposed = str(candidate.get("code") or "")
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{target_path}",
            tofile=f"b/{target_path}",
        )
    )

    return {
        "format": "unigrok-swarm-narrow-pr-v1",
        "task_id": task_id,
        "primary_goal": str(task.get("primary_goal") or "balanced"),
        "candidate_id": candidate.get("id"),
        "target_path": target_path,
        "focus_node": task.get("focus_node"),
        "hash_matches": hash_matches,
        "diff": diff,
        "verification": {
            "latency_ms": candidate.get("latency_ms"),
            "peak_mem_bytes": candidate.get("peak_mem_bytes"),
            "diff_bytes": candidate.get("diff_bytes"),
            "feasible": bool(candidate.get("feasible")),
            "pareto_rank": candidate.get("pareto_rank"),
            "crowding": candidate.get("crowding"),
            "mutator": candidate.get("mutator"),
        },
    }


def register_swarm_tools(mcp: FastMCP) -> None:
    mcp.add_tool(analyze_code_for_swarm, annotations=READONLY_TOOL)
    mcp.add_tool(plan_swarm_campaign, annotations=READONLY_TOOL)
    mcp.add_tool(start_code_swarm)
    mcp.add_tool(start_paste_swarm)
    mcp.add_tool(get_swarm_status, annotations=READONLY_TOOL)
    mcp.add_tool(list_swarm_tasks, annotations=READONLY_TOOL)
    mcp.add_tool(apply_swarm_winner, annotations=DESTRUCTIVE_TOOL)
    mcp.add_tool(cancel_swarm)
    mcp.add_tool(export_swarm_narrow_pr, annotations=READONLY_TOOL)


register_internal_tool("analyze_code_for_swarm", analyze_code_for_swarm)
register_internal_tool("plan_swarm_campaign", plan_swarm_campaign)
register_internal_tool("start_code_swarm", start_code_swarm)
register_internal_tool("start_paste_swarm", start_paste_swarm)
register_internal_tool("get_swarm_status", get_swarm_status)
register_internal_tool("list_swarm_tasks", list_swarm_tasks)
register_internal_tool("apply_swarm_winner", apply_swarm_winner)
register_internal_tool("cancel_swarm", cancel_swarm)
register_internal_tool("export_swarm_narrow_pr", export_swarm_narrow_pr)
