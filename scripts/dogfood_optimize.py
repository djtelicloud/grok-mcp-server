"""Dog-food the hive as a code optimizer on OUR OWN source — the courier bridge.

The gateway proposes; this script (the "caller" with the filesystem) disposes:
1. Pick a real function from this repo and courier its source to `agent` at ultra.
2. Extract the candidate rewrite from the reply.
3. Verify locally: exec the candidate, run correctness checks, time both versions.
4. Only a measured, test-passing winner counts. Record the verified outcome.

This is the forge loop without workspace attachment: Grok never touches files;
we run the oracle here and courier the verdict into benchmark receipts.

Untrusted local exec lives here (host ``exec``), not in the gateway agent.
Future wasm-in-Docker guest ABI / trigger conditions: docs/WASM_DOGFOOD.md.

Usage:
    python scripts/dogfood_optimize.py                 # default target
    python scripts/dogfood_optimize.py --target number_draft_lines
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import re
import statistics
import sys
import time
import timeit
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from unigrok_public import harness as harness_module
from unigrok_public import server as server_module
from unigrok_public import state as state_module

POLL_SECONDS = 16
MAX_POLLS = 30
# Same honesty bar Claude used: sub-noise wins are recorded but not applied.
APPLY_MIN_SPEEDUP_PCT = 8.0

OPTIMIZE_RUBRIC = (
    "Rewrite the Python function below to be faster and clearer.\n"
    "Hard rules (pareto rubric):\n"
    "- Correctness is a gate, not a tradeoff: behavior must be byte-for-byte "
    "identical for all inputs.\n"
    "- Keep the exact function name, signature, arity, and async-ness (drop-in "
    "replacement).\n"
    "- Prefer: lower time complexity, fewer allocations, smaller diff — in that "
    "order.\n"
    "- No new imports beyond what the original uses; no undefined names.\n"
    "- You may reference module-level names already used by the original "
    "(for example compiled regex tuples); do not redefine them unless required.\n"
    "- Never claim a speedup; the caller measures.\n"
    "Internally try four distinct approaches before choosing: (1) algorithmic "
    "restructure, (2) allocation reduction, (3) hot-loop micro-optimization, "
    "(4) simplification. Emit only the best.\n"
    "Output only one python code block containing the full rewritten function.\n\n"
    "## Function\n"
)


def _cases_number_draft_lines() -> list[tuple[Any, ...]]:
    return [
        ("",),
        ("one line",),
        ("a\nb\nc",),
        ("x\n" * 500,),
        ("line with unicode ✓\n" * 50,),
    ]


def _cases_majority() -> list[tuple[Any, ...]]:
    return [
        ([], "fallback"),
        (["a"], "d"),
        (["a", "b", "a"], "d"),
        (["x", "y", "y", "x", "y"], "d"),
        (["t"] * 100 + ["u"] * 99, "d"),
    ]


def _cases_parse_hive_vote() -> list[tuple[Any, ...]]:
    return [
        ("",),
        ("no json here",),
        ('{"v":"pass","c":2,"r":"none","f":"none","loc":"-"}',),
        ('noise {"v":"fail","c":1,"r":"bug","f":"fix","loc":"L2-L3"} tail',),
        ('{"v":"bogus"}',),
        ('{"v":"risk","c":"2"}',),
    ]


def _cases_redact_secrets() -> list[tuple[Any, ...]]:
    # Hot path: runs on every session turn, fact, courier, and telemetry note.
    long_clean = ("ordinary prose about routing and latency " * 40) + "\n"
    long_secret = (
        "XAI_API_KEY=xai-" + ("a" * 40) + "\n"
        "Authorization: Bearer " + ("b" * 32) + "\n"
        "sk-proj-" + ("c" * 24) + "\n"
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
    ) * 20
    return [
        (None,),
        ("",),
        ("hello world",),
        ("Authorization: Bearer secret-token-value",),
        ("XAI_API_KEY=xai-abcdefghijklmnopqrstuv",),
        ("openai_api_key: sk-abcdefghijklmnopqrstuv",),
        ("ghp_" + ("x" * 36),),
        ("-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----",),
        (long_clean,),
        (long_secret,),
        (12345,),
        ({"nested": "XAI_API_KEY=xai-nestedsecret12"},),
    ]


def _cases_classify_fallback_reason() -> list[tuple[Any, ...]]:
    # Hot path: every cross-plane recovery stamps a stable category.
    return [
        ("cli", Exception("capability unavailable for this model")),
        ("api", TimeoutError("request timed out")),
        ("cli", Exception("Build ACP timed-out after 30s")),
        ("api", Exception("operation cancelled by client")),
        ("cli", Exception("canceled")),
        ("api", Exception("HTTP 429 too many requests")),
        ("cli", Exception("rate-limit throttling")),
        ("api", Exception("provider busy / at capacity / 503")),
        ("cli", Exception("oauth authentication failed 401")),
        ("api", Exception("token expired credential 403")),
        ("cli", Exception("non-answer without a completed answer")),
        ("api", Exception("incomplete answer without a final answer")),
        ("cli", Exception("circuit breaker open")),
        ("api", Exception("runtime unavailable; stdout is unavailable")),
        ("cli", ConnectionError("connection reset by peer")),
        ("api", OSError("dns name resolution failed")),
        ("cli", Exception("network blip during stream")),
        ("api", Exception("mystery provider failure")),
        ("cli", Exception("")),
    ]


def _cases_format_session_prompt() -> list[tuple[Any, ...]]:
    # Hot path: builds the provider prompt on EVERY session-backed agent turn.
    def msg(role: str, content: Any) -> dict[str, Any]:
        return {"role": role, "content": content}

    short_history = [msg("user", "hi"), msg("assistant", "hello there")]
    mixed_history = [
        msg("user", "first question"),
        msg("assistant", ""),
        msg("assistant", None),
        msg("", "role defaults to user"),
        msg("system", "  padded content  "),
        msg("user", "second question with a secret XAI_API_KEY=xai-" + "k" * 30),
    ]
    long_history = [
        msg("user" if i % 2 == 0 else "assistant", f"turn {i}: " + ("word " * 400))
        for i in range(60)
    ]
    huge_single = [msg("user", "H" * 90_000)]
    return [
        ([], "current task"),
        (short_history, "what next?"),
        (mixed_history, "summarize the thread"),
        (long_history, "now answer briefly"),
        (huge_single, "truncate me correctly"),
        (short_history, ""),
    ]


def _cases_is_nonanswer_completion() -> list[tuple[Any, ...]]:
    # Hot path: judges EVERY agentic completion (twice when recovery retries).
    def kw(prompt: str) -> dict[str, Any]:
        return {"__kw__": {"prompt": prompt}}

    deep_prompt = harness_module.apply_deep_harness("Solve the puzzle.")
    long_answer = (
        "Findings: the maximum sum is 60 via the verified path.\n"
        + ("Supporting detail sentence about constraints and moves. " * 120)
    )
    plan_text = "Plan:\n1. Inspect the config\n2. Run the tests\n3. Report results"
    return [
        ("",),
        (None,),
        (12345,),
        ("The answer is 42.",),
        ("I'll review the code and get back to you.",),
        ("Sure thing, I'll run the tests now.",),
        ("Let me explain: the bug is a missing null check on line 3.",),
        ("I'll summarize: pending",),
        ("I found the issue: the cache key never includes the locale.",),
        (plan_text,),
        (plan_text, kw("give me a step-by-step plan for the migration")),
        ("We will carefully audit the dependencies.",),
        ("Okay, I'm going to investigate the flaky test.",),
        ("Verdict: safe to merge.",),
        ("PROVER — candidate 2 survived the vote; answer is 60.", kw(deep_prompt)),
        ("The maximum sum is 60.", kw(deep_prompt)),
        ("PROVER — this looks fine.", kw("summarize our red team exercise")),
        (long_answer,),
        (long_answer, kw("what did you find?")),
    ]


def _cases_workspace_courier() -> list[tuple[Any, ...]]:
    # Hot path: wraps every deliberate workspace_context courier on agent turns.
    def kw(max_chars: int) -> dict[str, Any]:
        return {"__kw__": {"max_chars": max_chars}}

    blob = ("def helper():\n    return 1\n" * 400) + "XAI_API_KEY=xai-" + ("z" * 40)
    return [
        ("", "proj", kw(1000)),
        ("   ", "label", kw(1000)),
        ("short snippet", "my project", kw(10_000)),
        (blob, "unigrok staging", kw(100_000)),
        ("Authorization: Bearer tokensecret", "secrets-demo", kw(5_000)),
        ("plain evidence text", "", kw(2_000)),
        ("x" * 500, "L" * 300, kw(10_000)),
        # Oversize must raise identically.
        ("y" * 200, "too-big", kw(50)),
    ]


def _cases_looks_like_plan() -> list[tuple[Any, ...]]:
    # Scout hit (triage_optimize.py): per-line regex loop in the non-answer path.
    big_plan = "Plan:\n" + "\n".join(f"{i}. Run the migration step {i}" for i in range(1, 60))
    big_prose = "\n".join(
        f"This paragraph {i} describes results without action verbs leading." for i in range(60)
    )
    return [
        ("",),
        ("Just a normal sentence.",),
        ("Plan:",),
        ("Plan:\n1. Inspect config\n2. Run tests",),
        ("- Inspect the config\n- Run the tests",),
        ("- one bullet only",),
        ("Notes first\n- Inspect config\n- Run tests\n- Report results",),
        ("1) Review the diff\n2) Deploy the fix",),
        (big_plan,),
        (big_prose,),
    ]


TARGETS: dict[str, dict[str, Any]] = {
    "_looks_like_plan": {
        "func": harness_module._looks_like_plan,
        "module": harness_module,
        "cases": _cases_looks_like_plan,
    },
    "is_nonanswer_completion": {
        "func": harness_module.is_nonanswer_completion,
        "module": harness_module,
        "cases": _cases_is_nonanswer_completion,
    },
    "workspace_courier": {
        "func": harness_module.workspace_courier,
        "module": harness_module,
        "cases": _cases_workspace_courier,
    },
    "format_session_prompt": {
        "func": harness_module.format_session_prompt,
        "module": harness_module,
        "cases": _cases_format_session_prompt,
    },
    "number_draft_lines": {
        "func": harness_module.number_draft_lines,
        "module": harness_module,
        "cases": _cases_number_draft_lines,
    },
    "majority": {
        "func": harness_module.majority,
        "module": harness_module,
        "cases": _cases_majority,
    },
    "parse_hive_vote": {
        "func": harness_module.parse_hive_vote,
        "module": harness_module,
        "cases": _cases_parse_hive_vote,
    },
    "redact_secrets": {
        "func": state_module.redact_secrets,
        "module": state_module,
        "cases": _cases_redact_secrets,
    },
    "_classify_fallback_reason": {
        "func": server_module._classify_fallback_reason,
        "module": server_module,
        "cases": _cases_classify_fallback_reason,
    },
}


def _payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def _extract_function(reply: str, name: str) -> str | None:
    match = re.search(r"```(?:python)?\n(.*?)```", reply, re.S)
    code = match.group(1) if match else reply
    return code if f"def {name}(" in code else None


def _compile_candidate(
    code: str, name: str, module: Any
) -> Callable[..., Any] | None:
    namespace: dict[str, Any] = dict(vars(module))
    try:
        exec(compile(code, "<candidate>", "exec"), namespace)  # noqa: S102
    except Exception:
        return None
    candidate = namespace.get(name)
    return candidate if callable(candidate) else None


def _imports(code: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def _counter_metric_gate(original_src: str, candidate_src: str) -> str | None:
    """Anti-Goodhart: winning on speed must not regress the other axes.

    Mechanical (never a vote): reject a faster candidate that smuggles in new
    imports or balloons the diff. This is the unfakeable half of the gate; the
    subjective 'is it more readable' half is left to an optional hive vote.
    """
    new_imports = _imports(candidate_src) - _imports(original_src)
    if new_imports:
        return f"adds imports {sorted(new_imports)} (blast radius)"
    base_lines = len(original_src.strip().splitlines())
    cand_lines = len(candidate_src.strip().splitlines())
    if base_lines and cand_lines > base_lines * 2 + 5:
        return f"diff too large ({base_lines}->{cand_lines} lines; smallest-diff pareto)"
    return None


def _split_case(case: tuple[Any, ...]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """A case is positional args, optionally ending in {"__kw__": {...}} for kwargs."""
    if case and isinstance(case[-1], dict) and set(case[-1]) == {"__kw__"}:
        return case[:-1], dict(case[-1]["__kw__"])
    return case, {}


def _verify(original: Callable[..., Any], candidate: Callable[..., Any], cases) -> str | None:
    for case in cases():
        args, kwargs = _split_case(case)
        try:
            expected = original(*args, **kwargs)
        except Exception as exc:
            expected = ("__raises__", type(exc).__name__)
        try:
            actual = candidate(*args, **kwargs)
        except Exception as exc:
            actual = ("__raises__", type(exc).__name__)
        if expected != actual:
            return f"behavior differs on case {case!r}"
    return None


def _bench(func: Callable[..., Any], cases) -> float:
    prepared = [_split_case(case) for case in cases()]

    def _run_all() -> None:
        for args, kwargs in prepared:
            try:
                func(*args, **kwargs)
            except Exception:  # noqa: S110
                # Cases may include expected raises; still exercise that path.
                pass

    # Heavier workload for redact_secrets-scale string work.
    runs = timeit.repeat(_run_all, number=80, repeat=7)
    return statistics.median(runs) * 1000


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4775/mcp")
    parser.add_argument(
        "--target",
        choices=sorted(TARGETS),
        default="workspace_courier",
    )
    parser.add_argument(
        "--apply-min-pct",
        type=float,
        default=APPLY_MIN_SPEEDUP_PCT,
        help="Measured speedup below this is verified but not applied",
    )
    args = parser.parse_args()
    target = TARGETS[args.target]
    original: Callable[..., Any] = target["func"]
    module = target["module"]
    source = inspect.getsource(original)

    print(f"optimizing {args.target} via ultra hive ...", flush=True)
    started = time.monotonic()
    async with streamablehttp_client(
        args.endpoint, headers={"X-Client-ID": "dogfood-optimize"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = _payload(
                await session.call_tool(
                    "agent",
                    {
                        "task": OPTIMIZE_RUBRIC + source,
                        "level": "ultra",
                        "workspace_label": "unigrok staging (dogfood)",
                        "disable_tools": [
                            "web",
                            "x_search",
                            "remote_code_execution",
                        ],
                    },
                )
            )
            while result.get("status") == "pending":
                job_id = result.get("job_id")
                if not job_id or (time.monotonic() - started) > POLL_SECONDS * MAX_POLLS:
                    raise RuntimeError("polling exhausted")
                result = _payload(
                    await session.call_tool(
                        "agent_result", {"job_id": job_id, "wait_seconds": POLL_SECONDS}
                    )
                )

            reply = str(result.get("text") or "")
            telemetry_id = result.get("telemetry_id")
            code = _extract_function(reply, args.target)
            verdict: dict[str, Any] = {
                "target": args.target,
                "telemetry_id": telemetry_id,
                "plane": result.get("resolved_plane"),
                "cost_usd": result.get("cost_usd"),
                "hive": (result.get("hive") or {}).get("votes_returned"),
            }
            passed = False
            apply = False
            note = ""
            if code is None:
                note = "no drop-in function in reply"
            else:
                candidate = _compile_candidate(code, args.target, module)
                if candidate is None:
                    note = "candidate failed to compile/exec"
                else:
                    mismatch = _verify(original, candidate, target["cases"])
                    regression = _counter_metric_gate(source, code)
                    if mismatch:
                        note = f"REJECTED: {mismatch}"
                    elif regression:
                        note = f"REJECTED (counter-metric): {regression}"
                    else:
                        base_ms = _bench(original, target["cases"])
                        cand_ms = _bench(candidate, target["cases"])
                        speedup = (base_ms - cand_ms) / base_ms * 100 if base_ms else 0.0
                        passed = True
                        apply = speedup >= args.apply_min_pct
                        note = (
                            f"verified drop-in; baseline {base_ms:.2f}ms vs candidate "
                            f"{cand_ms:.2f}ms ({speedup:+.1f}% measured)"
                            + (
                                "; APPLY"
                                if apply
                                else f"; SKIP below {args.apply_min_pct:g}% floor"
                            )
                        )
                        verdict.update(
                            {
                                "baseline_ms": round(base_ms, 3),
                                "candidate_ms": round(cand_ms, 3),
                                "measured_speedup_pct": round(speedup, 1),
                                "apply": apply,
                            }
                        )
            verdict.update({"passed": passed, "note": note})
            if isinstance(telemetry_id, int):
                await session.call_tool(
                    "record_benchmark_result",
                    {
                        "telemetry_id": telemetry_id,
                        "success": passed and apply,
                        "note": f"dogfood_optimize {args.target}: {note}",
                    },
                )
            print(json.dumps(verdict, indent=2))
            if passed and code:
                print("\n--- verified candidate ---\n" + code)
                if apply:
                    out = Path("dogfood_candidate_redact_secrets.py")
                    if args.target != "redact_secrets":
                        out = Path(f"dogfood_candidate_{args.target}.py")
                    out.write_text(code.rstrip() + "\n", encoding="utf-8")
                    print(f"\nwrote {out} for manual apply review")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
