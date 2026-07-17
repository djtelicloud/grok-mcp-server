"""Probe: are N concurrent CLI-plane turns safe on the public gateway?

Opens N independent MCP client connections and fires one small `chat` call on each
at the same moment. Reports plane, latency, and any fallback/cancellation per turn.
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


def _payload(result: Any) -> dict[str, Any]:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def _one(endpoint: str, index: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with streamablehttp_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = _payload(
                    await session.call_tool(
                        "chat",
                        {"prompt": f"Reply with exactly: PARALLEL_OK_{index}"},
                    )
                )
        return {
            "index": index,
            "ok": f"PARALLEL_OK_{index}" in str(result.get("text") or ""),
            "plane": result.get("resolved_plane") or result.get("plane"),
            "fallback": result.get("fallback_reason"),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {"index": index, "ok": False, "error": str(exc)[:200]}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4775/mcp")
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    rows = await asyncio.gather(*(_one(args.endpoint, i) for i in range(args.count)))
    for row in rows:
        print(json.dumps(row))
    good = sum(1 for row in rows if row.get("ok"))
    print(f"{good}/{len(rows)} parallel turns succeeded")
    return 0 if good == len(rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
