from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SESSION = "smoke:team-harness-v1"
FACT = "The live team-harness verification phrase is TEAM_MEMORY_OK."


def _structured(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        value = structured.get("result", structured)
        return value if isinstance(value, dict) else {}
    for item in getattr(result, "content", []):
        raw = getattr(item, "text", None)
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    for _ in range(20):
        result = await session.call_tool(name, arguments)
        if result.isError:
            raise RuntimeError(f"{name} returned an MCP error")
        payload = _structured(result)
        if payload.get("status") != "pending":
            return payload
        name = "agent_result"
        arguments = {"job_id": payload["job_id"], "wait_seconds": 16}
    raise RuntimeError("agent job remained pending for too long")


async def seed_and_run(url: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            await _call(
                client,
                "forget_session",
                {"session": SESSION, "confirm_delete": True},
            )
            remembered = await _call(client, "remember_fact", {"fact": FACT, "scope": SESSION})

            first = await _call(
                client,
                "agent",
                {
                    "task": (
                        "Use the live team-harness verification phrase and the courier marker. "
                        "Reply with TEAM_TURN_ONE_OK, TEAM_MEMORY_OK, and COURIER_OK."
                    ),
                    "session": SESSION,
                    "workspace_context": (
                        "Caller-selected evidence: marker COURIER_OK. "
                        "XAI_API_KEY=SHOULD_NEVER_PERSIST"
                    ),
                    "workspace_label": "team-harness-smoke",
                    "disable_tools": ["web", "x_search", "remote_code_execution"],
                },
            )
            first_text = str(first.get("text") or "")
            for marker in ("TEAM_TURN_ONE_OK", "TEAM_MEMORY_OK", "COURIER_OK"):
                if marker not in first_text:
                    raise RuntimeError(f"first team turn omitted {marker}: {first_text[:300]}")

            second = await _call(
                client,
                "agent",
                {
                    "task": (
                        "Continue this stored team session. Reply with TEAM_TURN_TWO_OK and "
                        "repeat TEAM_TURN_ONE_OK from the previous assistant turn."
                    ),
                    "session": SESSION,
                    "disable_tools": ["web", "x_search", "remote_code_execution"],
                },
            )
            second_text = str(second.get("text") or "")
            for marker in ("TEAM_TURN_TWO_OK", "TEAM_TURN_ONE_OK"):
                if marker not in second_text:
                    raise RuntimeError(f"second team turn omitted {marker}: {second_text[:300]}")

            history = await _call(client, "session_history", {"session": SESSION, "limit": 20})
            serialized = json.dumps(history)
            if history.get("count") != 4:
                raise RuntimeError(f"expected four stored messages: {history}")
            if "SHOULD_NEVER_PERSIST" in serialized:
                raise RuntimeError("workspace secret leaked into the durable transcript")
            if first.get("memory_fact_ids") != [remembered.get("fact_id")]:
                raise RuntimeError("agent did not retrieve the scoped durable fact")
            print(f"team_seed=ok session={SESSION} messages=4 fact_id={remembered.get('fact_id')}")


async def verify_existing(url: str, cleanup: bool) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            history = await _call(client, "session_history", {"session": SESSION, "limit": 20})
            if history.get("count") != 4:
                raise RuntimeError(f"session did not survive restart: {history}")
            facts = await _call(
                client,
                "search_knowledge",
                {"query": "live team harness verification phrase", "scope": SESSION},
            )
            matching = [item for item in facts.get("facts", []) if item.get("fact") == FACT]
            if not matching:
                raise RuntimeError("durable knowledge did not survive restart")
            print(f"team_persistence=ok session={SESSION} facts={len(matching)}")
            if cleanup:
                await _call(
                    client,
                    "forget_session",
                    {"session": SESSION, "confirm_delete": True},
                )
                await _call(
                    client,
                    "forget_fact",
                    {"fact_id": matching[0]["id"], "confirm_delete": True},
                )
                print("team_cleanup=ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:4775/mcp")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.verify_existing:
        asyncio.run(verify_existing(args.url, args.cleanup))
    else:
        asyncio.run(seed_and_run(args.url))


if __name__ == "__main__":
    main()
