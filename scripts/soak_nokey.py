"""No-key soak: prove a keyless deployment never spends metered money.

Runs concurrent agent calls (including ultra/hive, which would split voters to
the API plane IF a key existed) against a KEYLESS container and asserts:
- every run resolves on the CLI plane with cost_usd == 0
- no run errors out or hangs past the deadline
- hive still completes (voters all fall to CLI)

Point --endpoint at a container started WITHOUT XAI_API_KEY.

Usage:
    python scripts/soak_nokey.py --endpoint http://localhost:4798/mcp --rounds 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

POLL_SECONDS = 16
MAX_POLLS = 30

TASKS: list[dict[str, Any]] = [
    {"task": "What is 19 * 21? Reply with just the number.", "level": "low"},
    {"task": "Name the capital of France in one word.", "level": "none"},
    {
        "task": "Write a python function `add3(a,b,c)` returning their sum. "
        "Output only a code block.",
        "level": "ultra",
    },
    {"task": "Compute the sum of integers 1..40 exactly. Just the number.", "level": "max"},
]


def _payload(result: Any) -> dict[str, Any]:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def _one(endpoint: str, spec: dict[str, Any], index: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with streamablehttp_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = _payload(await session.call_tool("agent", spec))
                while result.get("status") == "pending":
                    job_id = result.get("job_id")
                    if not job_id or (time.monotonic() - started) > POLL_SECONDS * MAX_POLLS:
                        return {"index": index, "ok": False, "why": "poll deadline"}
                    result = _payload(
                        await session.call_tool(
                            "agent_result",
                            {"job_id": job_id, "wait_seconds": POLL_SECONDS},
                        )
                    )
        cost = float(result.get("cost_usd") or 0.0)
        plane = str(result.get("resolved_plane") or result.get("plane") or "")
        hive = result.get("hive") or {}
        planes_used = hive.get("planes_used") or ([plane] if plane else [])
        spent = cost > 0.0 or any("api" in str(p) for p in planes_used)
        return {
            "index": index,
            "level": spec.get("level"),
            "ok": bool(str(result.get("text") or "").strip()) and not spent,
            "plane": plane,
            "planes_used": planes_used,
            "cost_usd": cost,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "why": "METERED SPEND ON KEYLESS DEPLOY" if spent else "",
        }
    except Exception as exc:
        return {"index": index, "ok": False, "why": str(exc)[:160]}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4798/mcp")
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    failures = 0
    for round_number in range(1, args.rounds + 1):
        print(f"round {round_number}: {len(TASKS)} concurrent agents ...", flush=True)
        rows = await asyncio.gather(
            *(_one(args.endpoint, spec, i) for i, spec in enumerate(TASKS))
        )
        for row in rows:
            print("  " + json.dumps(row))
            if not row.get("ok"):
                failures += 1
    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
