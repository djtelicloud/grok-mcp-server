"""Stable 4765 vs slim 4775: low/high effort × harness prepend on/off.

Tests whether the j-space harness prepend ("physics demand") moves verified
accuracy relative to bare effort. Uses the same checkable tasks as benchmark_deep.

Usage:
    uv run python scripts/bench_stable_vs_slim.py
    uv run python scripts/bench_stable_vs_slim.py --task grid_60
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_deep import MAX_POLLS, POLL_SECONDS, TASKS, _total_tokens

from unigrok_public.harness import DEEP_HARNESS_PROMPT

ENDPOINTS = {
    "stable-4765": "http://127.0.0.1:4765/mcp",
    "slim-4775": "http://127.0.0.1:4775/mcp",
}
EFFORTS = ("low", "high")
PREPENDS = ("off", "on")


def _payload(result: Any) -> dict[str, Any]:
    """Prefer structuredContent; fall back to JSON/text content blocks."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if getattr(result, "isError", False):
                return {"error": text, "text": ""}
            return {"text": text}
        if isinstance(parsed, dict):
            return parsed
        return {"text": text}
    if getattr(result, "isError", False):
        return {"error": "tool error with empty payload", "text": ""}
    return {}


def _agent_args(
    *,
    endpoint_key: str,
    task_prompt: str,
    effort: str,
    prepend: str,
) -> dict[str, Any]:
    harness = DEEP_HARNESS_PROMPT if prepend == "on" else None
    if endpoint_key.startswith("slim"):
        args: dict[str, Any] = {
            "task": task_prompt,
            "level": effort,
            "disable_tools": ["web", "x_search", "remote_code_execution"],
            "use_memory": False,
        }
        if harness:
            # Portable physics demand: same bytes as server deep prefix, attached as
            # caller instructions so low/high effort stays under caller control.
            args["system_prompt"] = harness
        return args
    # Stable: map low→fast, high→thinking; CLI same-plane; no depth/level knobs.
    mode = "fast" if effort == "low" else "thinking"
    args = {
        "prompt": task_prompt,
        "mode": mode,
        "plane": "cli",
        "fallback_policy": "same_plane",
    }
    if harness:
        args["system_prompt"] = harness
    return args


async def _complete(
    session: ClientSession, endpoint_key: str, args: dict[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    result = _payload(await session.call_tool("agent", args))
    # Slim may return pending jobs; stable usually completes inline.
    while result.get("status") == "pending":
        job_id = result.get("job_id")
        if not job_id or (time.monotonic() - started) > POLL_SECONDS * MAX_POLLS:
            raise RuntimeError("job polling exhausted")
        result = _payload(
            await session.call_tool(
                "agent_result", {"job_id": job_id, "wait_seconds": POLL_SECONDS}
            )
        )
    return result


async def _run_case(
    session: ClientSession,
    *,
    endpoint_key: str,
    task_key: str,
    effort: str,
    prepend: str,
) -> dict[str, Any]:
    task = next(t for t in TASKS if t.key == task_key)
    args = _agent_args(
        endpoint_key=endpoint_key,
        task_prompt=task.prompt,
        effort=effort,
        prepend=prepend,
    )
    wall0 = time.perf_counter()
    result = await _complete(session, endpoint_key, args)
    wall_ms = round((time.perf_counter() - wall0) * 1000)
    text = str(result.get("text") or result.get("response") or "")
    passed, reason = task.check(text)
    telemetry_id = result.get("telemetry_id")
    if isinstance(telemetry_id, int):
        await session.call_tool(
            "record_benchmark_result",
            {
                "telemetry_id": telemetry_id,
                "success": passed,
                "note": (
                    f"stable_vs_slim {endpoint_key} {task_key} "
                    f"effort={effort} prepend={prepend}: {reason}"
                ),
            },
        )
    return {
        "endpoint": endpoint_key,
        "task": task_key,
        "effort": effort,
        "prepend": prepend,
        "passed": passed,
        "reason": reason,
        "plane": result.get("resolved_plane") or result.get("plane"),
        "tokens": _total_tokens(result),
        "elapsed_ms": result.get("elapsed_ms"),
        "wall_ms": wall_ms,
        "cost_usd": result.get("cost_usd"),
        "fallback": result.get("fallback_reason") or result.get("fallback_occurred"),
        "telemetry_id": telemetry_id,
        "leak_hint": any(
            marker in text
            for marker in ("PROVER", "RED TEAM", "j-space", "Candidate fleet", "vote")
        ),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task",
        action="append",
        choices=[t.key for t in TASKS],
        help="repeatable; default grid_60 + digit_sum_2_64",
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=list(ENDPOINTS),
        help="repeatable; default both",
    )
    args = parser.parse_args()
    task_keys = args.task or ["grid_60", "digit_sum_2_64"]
    endpoint_keys = args.endpoint or list(ENDPOINTS)

    rows: list[dict[str, Any]] = []
    for endpoint_key in endpoint_keys:
        url = ENDPOINTS[endpoint_key]
        print(f"\n=== {endpoint_key} ({url}) ===", flush=True)
        async with streamablehttp_client(
            url, headers={"X-Client-ID": "bench-stable-vs-slim"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                # Warm / refresh credential catalogs before the timed matrix.
                with contextlib.suppress(Exception):
                    if endpoint_key.startswith("slim"):
                        await session.call_tool("list_models", {})
                        await session.call_tool("grok_mcp_status", {"refresh": True})
                    else:
                        await session.call_tool("grok_mcp_status", {"view": "json"})
                for task_key in task_keys:
                    for effort in EFFORTS:
                        for prepend in PREPENDS:
                            label = f"{task_key} effort={effort} prepend={prepend}"
                            print(f"running {label} ...", flush=True)
                            try:
                                row = await _run_case(
                                    session,
                                    endpoint_key=endpoint_key,
                                    task_key=task_key,
                                    effort=effort,
                                    prepend=prepend,
                                )
                            except Exception as exc:
                                row = {
                                    "endpoint": endpoint_key,
                                    "task": task_key,
                                    "effort": effort,
                                    "prepend": prepend,
                                    "passed": False,
                                    "reason": f"runner error: {exc}"[:200],
                                }
                            rows.append(row)
                            print(f"  -> {json.dumps(row, default=str)}", flush=True)

    print("\n== Results ==")
    header = (
        f"{'endpoint':<14}{'task':<16}{'eff':<6}{'pre':<5}{'pass':<6}"
        f"{'plane':<8}{'tokens':<8}{'wall_s':<8}{'cost':<8}{'leak':<5}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        wall_s = (
            f"{(row.get('wall_ms') or 0) / 1000:.1f}" if row.get("wall_ms") is not None else "-"
        )
        print(
            f"{str(row.get('endpoint', '')):<14}"
            f"{str(row.get('task', '')):<16}"
            f"{str(row.get('effort', '')):<6}"
            f"{str(row.get('prepend', '')):<5}"
            f"{str(row.get('passed', '')):<6}"
            f"{str(row.get('plane') or '-'):<8}"
            f"{str(row.get('tokens') or '-'):<8}"
            f"{wall_s:<8}"
            f"{str(row.get('cost_usd') if row.get('cost_usd') is not None else '-'):<8}"
            f"{str(row.get('leak_hint', '')):<5}"
        )

    print("\n== Aggregate: pass rate by endpoint × prepend × effort ==")
    buckets: dict[tuple[str, str, str], list[bool]] = {}
    for row in rows:
        key = (row["endpoint"], row["prepend"], row["effort"])
        buckets.setdefault(key, []).append(bool(row.get("passed")))
    for key in sorted(buckets):
        vals = buckets[key]
        endpoint, prepend, effort = key
        print(
            f"{endpoint:<14} prepend={prepend:<3} effort={effort:<4} "
            f"{sum(vals)}/{len(vals)} passed"
        )

    print("\n== Prepend lift (on − off) by endpoint × effort ==")
    for endpoint in endpoint_keys:
        for effort in EFFORTS:
            off = buckets.get((endpoint, "off", effort), [])
            on = buckets.get((endpoint, "on", effort), [])
            if not off or not on:
                continue
            off_rate = sum(off) / len(off)
            on_rate = sum(on) / len(on)
            print(
                f"{endpoint:<14} effort={effort:<4} "
                f"off={off_rate:.0%} on={on_rate:.0%} "
                f"delta={on_rate - off_rate:+.0%}"
            )

    out = Path(__file__).resolve().parent.parent / "bench_stable_vs_slim_results.json"  # noqa: ASYNC240
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")  # noqa: ASYNC240
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
