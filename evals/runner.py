# evals/runner.py
# Golden-task eval runner with a closed calibration loop.
#
# Offline (default): every task replays through the REAL run_agent_turn /
# orchestrate stack against a cassette-scripted FakeClient (evals/fakes.py) —
# routing, AgentLoop, self-escalation, thinking-mode reflection, and session
# persistence all execute for real; only the SDK boundary is scripted. No
# network, deterministic, safe under pytest.
#
# Live (--live, NEVER in CI): tasks run against the real xAI API. When >= 4
# queued tasks are all fast-mode single completions and the installed SDK's
# batch service fits (client.batch.add accepts chat objects and chat.create
# accepts batch_request_id for result correlation), they are submitted as one
# batch; anything else runs sequentially.
#
# The closed loop: aggregated results are upserted into the store's
# routing_calibration table (user_version 6), which the RoutingAdvisor
# consults AHEAD of raw telemetry for borderline routing decisions when the
# rows are fresh (UNIGROK_CALIBRATION_TTL_HOURS) and have n >= 5.

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from evals.fakes import client_from_cassette
from evals.graders import run_graders

logger = logging.getLogger("GrokMCP.Evals")

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_TASKS_DIR = EVALS_DIR / "tasks"
DEFAULT_OUT_DIR = EVALS_DIR / "out"
DEFAULT_BASELINE_PATH = EVALS_DIR / "baseline.json"

VALID_CATEGORIES = {"coding", "reasoning", "research", "memory"}
VALID_MODES = {"auto", "fast", "reasoning", "thinking", "research"}
VALID_PLANES = {"auto", "api", "cli"}


# ─── Golden tasks ─────────────────────────────────────────────────────────────
@dataclass
class EvalTask:
    """Declarative golden task (evals/tasks/*.json)."""

    id: str
    category: str
    prompt: str
    mode: str = "auto"
    model: Optional[str] = None
    plane: str = "auto"
    session_setup: Optional[List[Dict[str, str]]] = None
    graders: List[Dict[str, Any]] = field(default_factory=list)
    max_cost_usd: Optional[float] = None
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: str = "?") -> "EvalTask":
        task_id = str(data.get("id") or "").strip()
        category = str(data.get("category") or "").strip().lower()
        prompt = str(data.get("prompt") or "")
        mode = str(data.get("mode") or "auto").strip().lower()
        plane = str(data.get("plane") or "auto").strip().lower()
        if not task_id:
            raise ValueError(f"{source}: task is missing 'id'")
        if category not in VALID_CATEGORIES:
            raise ValueError(f"{source}: task '{task_id}' category {category!r} not in {sorted(VALID_CATEGORIES)}")
        if not prompt:
            raise ValueError(f"{source}: task '{task_id}' is missing 'prompt'")
        if mode not in VALID_MODES:
            raise ValueError(f"{source}: task '{task_id}' mode {mode!r} not in {sorted(VALID_MODES)}")
        if plane not in VALID_PLANES:
            raise ValueError(
                f"{source}: task '{task_id}' plane {plane!r} not in {sorted(VALID_PLANES)}"
            )
        graders = data.get("graders") or []
        if not isinstance(graders, list) or not graders:
            raise ValueError(f"{source}: task '{task_id}' needs at least one grader")
        session_setup = data.get("session_setup")
        if session_setup is not None and not isinstance(session_setup, list):
            raise ValueError(f"{source}: task '{task_id}' session_setup must be a message list")
        max_cost = data.get("max_cost_usd")
        return cls(
            id=task_id,
            category=category,
            prompt=prompt,
            mode=mode,
            model=(str(data["model"]) if data.get("model") else None),
            plane=plane,
            session_setup=session_setup,
            graders=graders,
            max_cost_usd=(float(max_cost) if max_cost is not None else None),
            tags=[str(t) for t in (data.get("tags") or [])],
        )


def load_tasks(tasks_dir: Optional[Path] = None,
               only_ids: Optional[List[str]] = None) -> List[EvalTask]:
    """Load every golden task under tasks_dir (each *.json file holds one task
    object or a list of them), sorted by id for stable run order."""
    directory = Path(tasks_dir or DEFAULT_TASKS_DIR)
    tasks: List[EvalTask] = []
    seen = set()
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            task = EvalTask.from_dict(entry, source=path.name)
            if task.id in seen:
                raise ValueError(f"duplicate task id '{task.id}' (in {path.name})")
            seen.add(task.id)
            tasks.append(task)
    if only_ids:
        wanted = set(only_ids)
        missing = wanted - {t.id for t in tasks}
        if missing:
            raise ValueError(f"unknown task id(s): {sorted(missing)}")
        tasks = [t for t in tasks if t.id in wanted]
    tasks.sort(key=lambda t: t.id)
    return tasks


def _turn_kwargs(task: EvalTask) -> Dict[str, Any]:
    """Map a task's mode onto run_agent_turn kwargs, mirroring the MCP agent
    tool's mode mapping (src/tools/chats.py::agent)."""
    kwargs: Dict[str, Any] = {
        "prompt": task.prompt,
        "model": task.model,
        "plane": task.plane,
    }
    if task.mode == "fast":
        kwargs["enable_agentic"] = False
    elif task.mode == "reasoning":
        kwargs["mode"] = "reasoning"
    elif task.mode == "thinking":
        kwargs["thinking_mode"] = True
    elif task.mode == "research":
        kwargs.update(mode="reasoning", agent_count=4, include=["inline_citations"])
    return kwargs


# ─── Offline replay ───────────────────────────────────────────────────────────
@contextlib.contextmanager
def _hermetic_env():
    """Force UNI_GROK_TESTING=1 for the duration of an offline replay so
    model discovery, the routing advisor's store reads, and every other
    network-adjacent path stay inert. Restores the prior value on exit (a
    pytest run already has it set — this is a no-op there)."""
    prev = os.environ.get("UNI_GROK_TESTING")
    os.environ["UNI_GROK_TESTING"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("UNI_GROK_TESTING", None)
        else:
            os.environ["UNI_GROK_TESTING"] = prev


def _base_result(task: EvalTask) -> Dict[str, Any]:
    return {
        "task_id": task.id,
        "category": task.category,
        "mode": task.mode,
        "tags": list(task.tags),
        "route": None,
        "plane": None,
        "model": None,
        "final_model": None,
        "escalated": False,
        "finish_reason": None,
        "cost_usd": 0.0,
        "tokens": 0,
        "latency_sec": 0.0,
        "citations_count": 0,
        "tool_calls_count": 0,
        "tool_failures_count": 0,
        "project_file_lists_count": 0,
        "project_file_list_succeeded": None,
        "local_reads_count": 0,
        "local_reads_succeeded": 0,
        "local_test_runs_count": 0,
        "local_tests_passed": None,
        "appends_before_first_sample": None,
        "planning_model": None,
        "coding_model": None,
        "answer_excerpt": "",
        "graders": [],
        "passed": False,
        "error": None,
    }


def _apply_tool_trace_result(result: Dict[str, Any], trace: List[Any]) -> None:
    """Project bounded tool outcomes into safe structural grader fields.

    Full observations can contain source text, so reports retain counts and
    booleans only. A successful dispatch is necessary but not sufficient:
    read/list tools may return explicit refusal markers, and run_local_tests
    reports pytest's exit status in its standardized text.
    """
    tool_trace = [entry for entry in (trace or []) if isinstance(entry, dict)]
    result["tool_calls_count"] = len(tool_trace)

    def observation_succeeded(entry: Dict[str, Any]) -> bool:
        content = str(entry.get("content") or "").lstrip()
        return bool(entry.get("success")) and not content.startswith(
            ("[BLOCKED]", "[UNAVAILABLE]")
        )

    result["tool_failures_count"] = sum(
        1 for entry in tool_trace if not observation_succeeded(entry)
    )

    file_lists = [
        entry for entry in tool_trace if entry.get("tool_name") == "list_project_files"
    ]
    result["project_file_lists_count"] = len(file_lists)
    if file_lists:
        result["project_file_list_succeeded"] = all(
            observation_succeeded(entry) for entry in file_lists
        )

    local_reads = [
        entry for entry in tool_trace if entry.get("tool_name") == "read_local_file"
    ]
    result["local_reads_count"] = len(local_reads)
    result["local_reads_succeeded"] = sum(
        1 for entry in local_reads if observation_succeeded(entry)
    )

    local_test_runs = [
        entry for entry in tool_trace if entry.get("tool_name") == "run_local_tests"
    ]
    result["local_test_runs_count"] = len(local_test_runs)
    if local_test_runs:
        def local_test_passed(entry: Dict[str, Any]) -> bool:
            content = str(entry.get("content") or "")
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            return (
                observation_succeeded(entry)
                and first_line.startswith("Local tests passed for `")
                and "` (exit code 0, timeout " in first_line
                and first_line.endswith("s).")
            )

        result["local_tests_passed"] = all(
            local_test_passed(entry) for entry in local_test_runs
        )


def _grade(task: EvalTask, answer: str, result: Dict[str, Any]) -> None:
    """Run graders + the max_cost_usd budget check; sets result['graders'] and
    result['passed'] in place."""
    outcomes = run_graders(task.graders, answer, result)
    if task.max_cost_usd is not None:
        within = float(result.get("cost_usd") or 0.0) <= task.max_cost_usd
        outcomes.append({
            "type": "max_cost_usd",
            "passed": within,
            "detail": f"cost ${float(result.get('cost_usd') or 0.0):.4f} vs cap ${task.max_cost_usd:.4f}",
        })
    result["graders"] = outcomes
    result["passed"] = bool(outcomes) and all(o["passed"] for o in outcomes) and result["error"] is None


async def _run_one_offline(task: EvalTask, script: Dict[str, Any]) -> Dict[str, Any]:
    import src.utils as utils

    result = _base_result(task)
    result["planning_model"] = await utils.resolve_model("planning")
    result["coding_model"] = await utils.resolve_model("coding")

    fake = client_from_cassette(script)
    session = None
    if task.session_setup is not None:
        session = f"eval-{task.id}-{uuid.uuid4().hex[:8]}"
        seed = [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
            for m in task.session_setup
        ]
        await utils.store.replace_messages(session, seed)

    start = time.time()
    try:
        with mock.patch("src.utils.get_xai_client", return_value=fake):
            layer = await utils.run_agent_turn(session=session, **_turn_kwargs(task))
    except Exception as exc:  # noqa: BLE001 — a crashed task is a failed task
        result["latency_sec"] = round(time.time() - start, 4)
        result["error"] = f"{type(exc).__name__}: {exc}"
        _grade(task, "", result)
        return result

    result["latency_sec"] = round(time.time() - start, 4)
    result["route"] = layer.route
    result["plane"] = layer.plane
    result["escalated"] = bool(layer.escalated)
    result["finish_reason"] = layer.finish_reason
    result["cost_usd"] = round(float(layer.cost_usd or 0.0), 6)
    result["tokens"] = int(layer.tokens or 0)
    result["citations_count"] = len(layer.citations or [])
    _apply_tool_trace_result(result, layer.tool_trace or [])
    # Routed model: MetaLayer.model (set by orchestrate) with the fake's
    # first chat.create kwargs as the fallback witness.
    first_model = fake.create_calls[0].get("model") if fake.create_calls else None
    last_model = fake.create_calls[-1].get("model") if fake.create_calls else None
    result["model"] = layer.model or first_model
    result["final_model"] = last_model
    if fake.chats:
        result["appends_before_first_sample"] = fake.chats[0].appends_before_first_sample
    if fake.responses_remaining:
        result["unconsumed_responses"] = fake.responses_remaining
    result["answer_excerpt"] = (layer.generation or "")[:400]

    _grade(task, layer.generation or "", result)
    return result


async def run_offline(tasks: List[EvalTask],
                      cassettes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replay every task against its cassette. Tasks without a cassette fail
    with an explicit error instead of silently skipping."""
    results = []
    with _hermetic_env():
        for task in tasks:
            script = cassettes.get(task.id)
            if script is None:
                result = _base_result(task)
                result["error"] = f"no cassette scripted for task '{task.id}'"
                _grade(task, "", result)
                results.append(result)
                continue
            results.append(await _run_one_offline(task, script))
    return results


# ─── Live tier (never in CI) ─────────────────────────────────────────────────
def batch_service_usable(client: Any) -> Tuple[bool, str]:
    """Introspect whether the installed SDK's batch service cleanly fits chat
    completions: the service surface must exist and chat.create must accept
    batch_request_id (the correlation key BatchResult echoes back)."""
    batch = getattr(client, "batch", None)
    if batch is None:
        return False, "client has no batch service"
    for name in ("create", "add", "get", "list_batch_results"):
        if not callable(getattr(batch, name, None)):
            return False, f"batch service lacks {name}()"
    try:
        from src.utils import _chat_create_supports
        if not _chat_create_supports("batch_request_id"):
            return False, "chat.create lacks batch_request_id (no result correlation)"
    except Exception as exc:  # noqa: BLE001
        return False, f"capability probe failed: {exc}"
    return True, "ok"


def batch_mode_decision(tasks: List[EvalTask], usable: bool, reason: str) -> Tuple[bool, str]:
    """Batch submission is wired ONLY for the shape that cleanly fits: >= 4
    queued live tasks that are ALL fast-mode single toolless completions (a
    batch request is one completion — it cannot host an AgentLoop's tool
    round-trips). Everything else runs sequentially with the reason noted."""
    if not usable:
        return False, f"batch unavailable: {reason}"
    if len(tasks) < 4:
        return False, f"batch available but only {len(tasks)} live task(s) queued (need >= 4)"
    non_fast = [t.id for t in tasks if t.mode != "fast"]
    if non_fast:
        return False, (
            "batch available but not used: non-fast tasks need the agent loop "
            f"({', '.join(non_fast[:4])}{'…' if len(non_fast) > 4 else ''})"
        )
    cli_tasks = [t.id for t in tasks if t.plane == "cli"]
    if cli_tasks:
        return False, (
            "batch available but not used: explicit CLI-plane tasks cannot be "
            "submitted through the API batch service "
            f"({', '.join(cli_tasks[:4])}{'…' if len(cli_tasks) > 4 else ''})"
        )
    auto_tasks = [t.id for t in tasks if t.plane != "api"]
    if auto_tasks:
        return False, (
            "batch available but not used: batch execution requires explicit "
            "plane='api' so queue size cannot change credential planes "
            f"({', '.join(auto_tasks[:4])}{'…' if len(auto_tasks) > 4 else ''})"
        )
    return True, "batch mode engaged (all-fast task set, batch_request_id correlation)"


async def _live_task_model(task: EvalTask) -> str:
    """Approximate auto-routing for batch submissions with the same static
    heuristic orchestrate uses (borderline advisor consultation excluded)."""
    import src.utils as utils

    if task.model:
        return task.model
    alias = "planning" if utils.routing_reason_score(task.prompt) >= 2 else "coding"
    return await utils.resolve_model(alias)


async def _run_live_batch(tasks: List[EvalTask]) -> List[Dict[str, Any]]:
    import src.utils as utils
    from xai_sdk.chat import user

    client = utils.get_xai_client()
    chats = []
    models: Dict[str, str] = {}
    for task in tasks:
        model = await _live_task_model(task)
        models[task.id] = model
        chat = client.chat.create(model=model, batch_request_id=task.id)
        chat.append(user(task.prompt))
        chats.append(chat)

    batch = await utils.run_blocking(
        client.batch.create, f"unigrok-evals-{int(time.time())}", timeout=60.0
    )
    batch_id = getattr(batch, "batch_id", None) or getattr(batch, "id", None)
    if not batch_id:
        raise RuntimeError("batch.create returned no batch id")
    await utils.run_blocking(client.batch.add, batch_id, chats, timeout=120.0)

    poll_sec = float(os.environ.get("UNIGROK_EVAL_BATCH_POLL_SEC", "15") or 15)
    deadline = time.time() + float(os.environ.get("UNIGROK_EVAL_BATCH_TIMEOUT_SEC", "1800") or 1800)
    while True:
        status = await utils.run_blocking(client.batch.get, batch_id, timeout=60.0)
        state = getattr(status, "state", None)
        pending = getattr(state, "num_pending", None)
        if pending == 0:
            break
        if time.time() >= deadline:
            with contextlib.suppress(Exception):
                await utils.run_blocking(client.batch.cancel, batch_id, timeout=60.0)
            raise RuntimeError(f"batch {batch_id} timed out with {pending} request(s) pending")
        await asyncio.sleep(poll_sec)

    by_request: Dict[str, Any] = {}
    token = None
    while True:
        page = await utils.run_blocking(
            client.batch.list_batch_results, batch_id,
            timeout=60.0, pagination_token=token,
        )
        for entry in getattr(page, "results", []) or []:
            by_request[str(getattr(entry, "batch_request_id", ""))] = entry
        token = getattr(page, "pagination_token", None)
        if not token:
            break

    results = []
    for task in tasks:
        result = _base_result(task)
        result["route"] = "live-batch"
        result["plane"] = "API"
        result["model"] = models.get(task.id)
        result["final_model"] = models.get(task.id)
        entry = by_request.get(task.id)
        if entry is None:
            result["error"] = "batch produced no result for this request id"
        elif getattr(entry, "has_error", False):
            result["error"] = f"batch request failed: {getattr(entry, 'error_message', 'unknown')}"
        else:
            response = getattr(entry, "response", None)
            content = str(getattr(response, "content", "") or "")
            usage = getattr(response, "usage", None)
            if usage is not None:
                result["tokens"] = int(
                    (getattr(usage, "prompt_tokens", 0) or 0)
                    + (getattr(usage, "completion_tokens", 0) or 0)
                )
            result["cost_usd"] = round(float(getattr(response, "cost_usd", 0.0) or 0.0), 6)
            result["finish_reason"] = "final_answer"
            result["answer_excerpt"] = content[:400]
            _grade(task, content, result)
            results.append(result)
            continue
        _grade(task, "", result)
        results.append(result)
    return results


async def _run_live_sequential(tasks: List[EvalTask]) -> List[Dict[str, Any]]:
    import src.utils as utils

    results = []
    for task in tasks:
        result = _base_result(task)
        result["planning_model"] = await utils.resolve_model("planning")
        result["coding_model"] = await utils.resolve_model("coding")
        session = None
        if task.session_setup is not None:
            session = f"eval-live-{task.id}-{uuid.uuid4().hex[:8]}"
            await utils.store.replace_messages(session, [
                {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
                for m in task.session_setup
            ])
        start = time.time()
        try:
            layer = await utils.run_agent_turn(session=session, **_turn_kwargs(task))
        except Exception as exc:  # noqa: BLE001
            result["latency_sec"] = round(time.time() - start, 4)
            result["error"] = f"{type(exc).__name__}: {exc}"
            _grade(task, "", result)
            results.append(result)
            continue
        result["latency_sec"] = round(time.time() - start, 4)
        result["route"] = layer.route
        result["plane"] = layer.plane
        result["model"] = layer.model or None
        result["final_model"] = layer.model or None
        result["escalated"] = bool(layer.escalated)
        result["finish_reason"] = layer.finish_reason
        result["cost_usd"] = round(float(layer.cost_usd or 0.0), 6)
        result["tokens"] = int(layer.tokens or 0)
        result["citations_count"] = len(layer.citations or [])
        _apply_tool_trace_result(result, layer.tool_trace or [])
        result["answer_excerpt"] = (layer.generation or "")[:400]
        _grade(task, layer.generation or "", result)
        results.append(result)
    return results


async def run_live(tasks: List[EvalTask]) -> Tuple[List[Dict[str, Any]], str]:
    """Run tasks against the real API. Returns (results, batch_note)."""
    import src.utils as utils

    try:
        client = utils.get_xai_client()
        usable, reason = batch_service_usable(client)
    except Exception as exc:  # noqa: BLE001
        usable, reason = False, f"client unavailable: {exc}"
    use_batch, note = batch_mode_decision(tasks, usable, reason)
    if use_batch:
        try:
            return await _run_live_batch(tasks), note
        except Exception as exc:  # noqa: BLE001
            note = f"batch submission failed ({exc}); fell back to sequential live calls"
            logger.warning(note)
    return await _run_live_sequential(tasks), note


# ─── Report + calibration + baseline ─────────────────────────────────────────
def build_report(results: List[Dict[str, Any]], run_mode: str = "offline",
                 notes: Optional[List[str]] = None) -> Dict[str, Any]:
    by_category: Dict[str, Dict[str, Any]] = {}
    for result in results:
        bucket = by_category.setdefault(result["category"], {"tasks": 0, "passed": 0, "cost_usd": 0.0})
        bucket["tasks"] += 1
        bucket["passed"] += 1 if result["passed"] else 0
        bucket["cost_usd"] = round(bucket["cost_usd"] + float(result.get("cost_usd") or 0.0), 6)
    passed = sum(1 for r in results if r["passed"])
    return {
        "generated_at": datetime.now().isoformat(),
        "run_mode": run_mode,
        "notes": list(notes or []),
        "totals": {
            "tasks": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
            "total_cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in results), 6),
        },
        "by_category": by_category,
        "results": results,
    }


def write_report(report: Dict[str, Any], out_dir: Optional[Path] = None) -> Path:
    directory = Path(out_dir or DEFAULT_OUT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def markdown_summary(report: Dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        f"## UniGrok evals — {report['run_mode']} run",
        "",
        f"**{totals['passed']}/{totals['tasks']} passed** "
        f"(pass rate {totals['pass_rate']:.0%}, total cost ${totals['total_cost_usd']:.4f})",
        "",
        "| task | category | route | model | passed | cost $ | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in report["results"]:
        failed = [g["detail"] for g in result.get("graders", []) if not g["passed"]]
        note = result.get("error") or ("; ".join(failed)[:80] if failed else "")
        lines.append(
            f"| {result['task_id']} | {result['category']} | {result.get('route') or '—'} "
            f"| {result.get('model') or '—'} | {'PASS' if result['passed'] else 'FAIL'} "
            f"| {float(result.get('cost_usd') or 0.0):.4f} | {note} |"
        )
    for note in report.get("notes", []):
        lines.append(f"\n> {note}")
    return "\n".join(lines)


def aggregate_calibration(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse task results into (category, route, model) calibration rows.
    Results without a route or model (harness errors before routing) carry no
    routing signal and are skipped."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    for result in results:
        route = result.get("route")
        model = result.get("model")
        if not route or not model:
            continue
        key = (result["category"], str(route), str(model))
        bucket = groups.setdefault(key, {"successes": 0, "cost": 0.0, "n": 0})
        bucket["n"] += 1
        bucket["successes"] += 1 if result.get("passed") else 0
        bucket["cost"] += float(result.get("cost_usd") or 0.0)
    rows = []
    for (category, route, model), bucket in sorted(groups.items()):
        rows.append({
            "category": category,
            "route": route,
            "model": model,
            "success_rate": round(bucket["successes"] / bucket["n"], 4),
            "avg_cost_usd": round(bucket["cost"] / bucket["n"], 6),
            "n": bucket["n"],
        })
    return rows


async def write_calibration(store: Any, results: List[Dict[str, Any]]) -> int:
    """Upsert aggregated results into the store's routing_calibration table
    (the RoutingAdvisor's preferred data source while fresh). Returns the
    number of rows written; failures log and return what succeeded."""
    rows = aggregate_calibration(results)
    written = 0
    for row in rows:
        try:
            await store.upsert_routing_calibration(**row)
            written += 1
        except Exception as exc:  # noqa: BLE001 — calibration is advisory
            logger.warning(f"calibration upsert failed for {row}: {exc}")
    return written


def check_baseline(results: List[Dict[str, Any]],
                   baseline_path: Optional[Path] = None) -> List[str]:
    """Compare results against the checked-in expected-pass list. Returns the
    task ids that regressed: expected to pass but failed OR missing from the
    run entirely."""
    path = Path(baseline_path or DEFAULT_BASELINE_PATH)
    baseline = json.loads(path.read_text(encoding="utf-8"))
    expected = [str(task_id) for task_id in baseline.get("expected_pass", [])]
    outcome = {r["task_id"]: r["passed"] for r in results}
    return [task_id for task_id in expected if not outcome.get(task_id, False)]
