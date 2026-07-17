"""Slim 4775 only: low/high effort × j-space preprompt on/off.

Measures whether attaching DEEP_HARNESS_PROMPT changes verified accuracy
at fixed effort levels. Tools disabled; memory off.

Usage:
    PYTHONPATH=src uv run python scripts/bench_slim_preprompt.py
    PYTHONPATH=src uv run python scripts/bench_slim_preprompt.py --task grid_60
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

ENDPOINT = "http://127.0.0.1:4775/mcp"
EFFORTS = ("low", "high")
PREPENDS = ("off", "on")


def _payload(result: Any) -> dict[str, Any]:
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


async def _complete(session: ClientSession, args: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    result = _payload(await session.call_tool("agent", args))
    if result.get("error") and not result.get("text"):
        return result
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
    session: ClientSession, *, task_key: str, effort: str, prepend: str
) -> dict[str, Any]:
    task = next(t for t in TASKS if t.key == task_key)
    args: dict[str, Any] = {
        "task": task.prompt,
        "level": effort,
        "disable_tools": ["web", "x_search", "remote_code_execution"],
        "use_memory": False,
    }
    if prepend == "on":
        # Same bytes as server deep prefix; attached as system_prompt so level=low/high
        # still controls CLI --effort (native depth=deep would force xhigh).
        args["system_prompt"] = DEEP_HARNESS_PROMPT
    wall0 = time.perf_counter()
    result = await _complete(session, args)
    wall_ms = round((time.perf_counter() - wall0) * 1000)
    if result.get("error") and not (result.get("text") or result.get("response")):
        return {
            "task": task_key,
            "effort": effort,
            "prepend": prepend,
            "passed": False,
            "reason": f"tool error: {result.get('error')}"[:220],
            "wall_ms": wall_ms,
        }
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
                    f"slim_preprompt {task_key} effort={effort} "
                    f"prepend={prepend}: {reason}"
                ),
            },
        )
    return {
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
        "fallback": result.get("fallback_reason"),
        "telemetry_id": telemetry_id,
        "leak_hint": any(
            marker in text
            for marker in ("PROVER", "RED TEAM", "j-space", "Candidate fleet")
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
    parser.add_argument("--endpoint", default=ENDPOINT)
    args = parser.parse_args()
    task_keys = args.task or ["grid_60", "digit_sum_2_64"]

    rows: list[dict[str, Any]] = []
    async with streamablehttp_client(
        args.endpoint, headers={"X-Client-ID": "bench-slim-preprompt"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            with contextlib.suppress(Exception):
                await session.call_tool("list_models", {})
            for task_key in task_keys:
                for effort in EFFORTS:
                    for prepend in PREPENDS:
                        label = f"{task_key} effort={effort} prepend={prepend}"
                        print(f"running {label} ...", flush=True)
                        try:
                            row = await _run_case(
                                session,
                                task_key=task_key,
                                effort=effort,
                                prepend=prepend,
                            )
                        except Exception as exc:
                            row = {
                                "task": task_key,
                                "effort": effort,
                                "prepend": prepend,
                                "passed": False,
                                "reason": f"runner error: {exc}"[:220],
                            }
                        rows.append(row)
                        print(f"  -> {json.dumps(row, default=str)}", flush=True)

    print("\n== Results ==")
    header = (
        f"{'task':<16}{'eff':<6}{'pre':<5}{'pass':<6}{'plane':<8}"
        f"{'tokens':<8}{'wall_s':<8}{'cost':<10}{'leak':<5}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        wall_s = (
            f"{(row.get('wall_ms') or 0) / 1000:.1f}"
            if row.get("wall_ms") is not None
            else "-"
        )
        print(
            f"{str(row.get('task', '')):<16}"
            f"{str(row.get('effort', '')):<6}"
            f"{str(row.get('prepend', '')):<5}"
            f"{str(row.get('passed', '')):<6}"
            f"{str(row.get('plane') or '-'):<8}"
            f"{str(row.get('tokens') or '-'):<8}"
            f"{wall_s:<8}"
            f"{str(row.get('cost_usd') if row.get('cost_usd') is not None else '-'):<10}"
            f"{str(row.get('leak_hint', '')):<5}"
        )
        if not row.get("passed"):
            print(f"         reason: {row.get('reason')}")

    print("\n== Pass rate: prepend × effort ==")
    buckets: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        key = (row["prepend"], row["effort"])
        buckets.setdefault(key, []).append(bool(row.get("passed")))
    for key in sorted(buckets):
        prepend, effort = key
        vals = buckets[key]
        print(f"prepend={prepend:<3} effort={effort:<4} {sum(vals)}/{len(vals)} passed")

    print("\n== Prepend lift (on − off) by effort ==")
    for effort in EFFORTS:
        off = buckets.get(("off", effort), [])
        on = buckets.get(("on", effort), [])
        if not off or not on:
            continue
        off_rate = sum(off) / len(off)
        on_rate = sum(on) / len(on)
        print(
            f"effort={effort:<4} off={off_rate:.0%} on={on_rate:.0%} "
            f"delta={on_rate - off_rate:+.0%}"
        )

    out = Path(__file__).resolve().parent.parent / "bench_slim_preprompt_results.json"  # noqa: ASYNC240
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")  # noqa: ASYNC240
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
