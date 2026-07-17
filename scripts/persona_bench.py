"""Persona-prepend tournament: which prepends force the best verified artifacts?

Each candidate prepend is sent as `system_prompt` (caller instructions) with the
same checkable tasks from benchmark_deep. Every run is machine-checked and
recorded via record_benchmark_result. The report ranks prepends by pass rate,
then latency.

Usage:
    python scripts/persona_bench.py
    python scripts/persona_bench.py --task lis_code --endpoint http://localhost:4775/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from benchmark_deep import MAX_POLLS, POLL_SECONDS, TASKS, _payload, _total_tokens
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PREPENDS: dict[str, str] = {
    "none": "",
    "prover": (
        "Derive the answer from first principles and check every stated constraint "
        "one by one before replying. Recompute every number twice."
    ),
    "red_team": (
        "Before replying, attack your own draft answer: edge cases, boundary values, "
        "arithmetic slips, off-by-one errors. Only emit the version that survives."
    ),
    "bounty": (
        "You are a bug-bounty hunter reviewing your own output before submission: "
        "if any input could break it or any number could be wrong, fix it first."
    ),
    "optimizer": (
        "Generate three distinct approaches internally, rank them on correctness "
        "then efficiency, and emit only the winner. Never show the ranking."
    ),
    "spec_auditor": (
        "You are a spec-compliance auditor: satisfy every stated requirement exactly, "
        "nothing more, and verify each one against your reply before sending."
    ),
}


async def _run_case(
    session: ClientSession, task_key: str, prepend_key: str
) -> dict[str, Any]:
    task = next(t for t in TASKS if t.key == task_key)
    args: dict[str, Any] = {"task": task.prompt}
    if PREPENDS[prepend_key]:
        args["system_prompt"] = PREPENDS[prepend_key]
    started = time.monotonic()
    result = _payload(await session.call_tool("agent", args))
    while result.get("status") == "pending":
        job_id = result.get("job_id")
        if not job_id or (time.monotonic() - started) > POLL_SECONDS * MAX_POLLS:
            raise RuntimeError(f"{task_key}/{prepend_key}: polling exhausted")
        result = _payload(
            await session.call_tool(
                "agent_result", {"job_id": job_id, "wait_seconds": POLL_SECONDS}
            )
        )
    passed, reason = task.check(str(result.get("text") or ""))
    telemetry_id = result.get("telemetry_id")
    if isinstance(telemetry_id, int):
        await session.call_tool(
            "record_benchmark_result",
            {
                "telemetry_id": telemetry_id,
                "success": passed,
                "note": f"persona_bench {task_key} prepend={prepend_key}: {reason}",
            },
        )
    return {
        "task": task_key,
        "prepend": prepend_key,
        "passed": passed,
        "plane": result.get("resolved_plane"),
        "tokens": _total_tokens(result),
        "latency_ms": result.get("elapsed_ms"),
        "cost_usd": result.get("cost_usd"),
        "telemetry_id": telemetry_id,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4775/mcp")
    parser.add_argument(
        "--task",
        choices=[t.key for t in TASKS],
        action="append",
        help="repeatable; default grid_60 + lis_code",
    )
    args = parser.parse_args()
    task_keys = args.task or ["grid_60", "lis_code"]

    rows: list[dict[str, Any]] = []
    async with streamablehttp_client(args.endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for task_key in task_keys:
                for prepend_key in PREPENDS:
                    print(f"running {task_key} prepend={prepend_key} ...", flush=True)
                    try:
                        row = await _run_case(session, task_key, prepend_key)
                    except Exception as exc:
                        row = {
                            "task": task_key,
                            "prepend": prepend_key,
                            "passed": False,
                            "error": str(exc)[:160],
                        }
                    rows.append(row)
                    print(f"  -> {json.dumps(row, default=str)}", flush=True)

    print("\n== Tournament standings (pass rate, then avg latency) ==")
    standings: dict[str, dict[str, float]] = {}
    for row in rows:
        entry = standings.setdefault(row["prepend"], {"runs": 0, "passes": 0, "latency": 0})
        entry["runs"] += 1
        entry["passes"] += 1 if row.get("passed") else 0
        entry["latency"] += float(row.get("latency_ms") or 0)
    ranked = sorted(
        standings.items(),
        key=lambda item: (-item[1]["passes"] / item[1]["runs"], item[1]["latency"]),
    )
    for name, entry in ranked:
        print(
            f"{name:<14} {int(entry['passes'])}/{int(entry['runs'])} passed   "
            f"avg latency {round(entry['latency'] / entry['runs'] / 1000, 1)}s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
