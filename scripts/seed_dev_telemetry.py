"""Seed clearly-labeled sample telemetry receipts for local UI work.

Dev-only. Writes 48 deterministic receipts (callers prefixed ``dev-seed:``)
through the public state store into a throwaway local volume, then spreads
their timestamps over the past few hours so the Time column and latency
percentiles render realistically. Never run against real state.

Usage: UNIGROK_STATE_DIR=/state python scripts/seed_dev_telemetry.py
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from unigrok_public.state import PublicStateStore

KINDS = ["agent", "chat_with_vision", "remote_code_execution", "web_search", "x_search"]
ROUTES = {"agent": "agent", "chat_with_vision": "chat", "remote_code_execution": "code"}
MODELS = ["grok-4", "grok-4-fast", "grok-3-mini"]
CALLERS = ["dev-seed:cursor", "dev-seed:vscode", "dev-seed:smoke", "dev-seed:copilot"]
TOTAL = 48
API_SHARE = 25  # api 25 / cli 23


def _row(index: int) -> dict[str, object]:
    kind = KINDS[index % len(KINDS)]
    plane = "api" if index < API_SHARE else "cli"
    verified = index % 3 != 2
    return {
        "caller": CALLERS[index % len(CALLERS)],
        "request_kind": kind,
        "route": ROUTES.get(kind, "search"),
        "requested_plane": plane,
        "resolved_plane": plane,
        "model": MODELS[index % len(MODELS)],
        "success": (index % 8 != 7) if verified else None,
        "verified": verified,
        "latency_ms": 350 + (index * 197) % 4200,
        "cost_usd": 0.0 if plane == "cli" else round(0.0004 + (index % 9) * 0.0011, 6),
        "fallback_reason": "cross_plane_recovery" if index % 12 == 5 else None,
        "stop_reason": ["end_turn", "tool_use", "length"][index % 3],
        "metadata": {"dev_seed": True},
    }


async def main() -> None:
    store = PublicStateStore()
    ids = [await store.save_telemetry(_row(index)) for index in range(TOTAL)]
    now = datetime.now(UTC)
    with sqlite3.connect(store.path) as connection:
        for offset, telemetry_id in enumerate(reversed(ids)):
            stamp = (now - timedelta(minutes=4 + offset * 7)).isoformat()
            connection.execute(
                "UPDATE telemetry SET created_at=? WHERE id=?", (stamp, telemetry_id)
            )
        connection.commit()
    print(f"seeded {len(ids)} dev receipts into {store.path}")


if __name__ == "__main__":
    asyncio.run(main())
