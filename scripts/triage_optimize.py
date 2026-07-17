"""Hive scout pass: cheap yes/no triage over our whole codebase.

Extracts every top-level function from src/unigrok_public, batches them to Grok
on the flat-rate plane, and asks for one terse verdict each: worth optimizing?
Verdicts are advisory — anything marked yes goes to dogfood_optimize.py where
only measured wins count.

Usage:
    python scripts/triage_optimize.py
    python scripts/triage_optimize.py --max-lines 80
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SRC = Path(__file__).resolve().parent.parent / "src" / "unigrok_public"
MODULES = ["harness.py", "state.py", "xai_api.py", "grok_build.py", "server.py"]
ALREADY_DONE = {
    "number_draft_lines",
    "majority",
    "parse_hive_vote",
    "format_session_prompt",
    "is_nonanswer_completion",
    "redact_secrets",
    "_classify_fallback_reason",
}

TRIAGE_PROMPT = (
    "You are a performance triage scout for a Python MCP gateway. For each "
    "numbered function below, judge: is it WORTH sending to an expensive "
    "optimization pass? Say yes only when the function does real CPU work "
    "(string/regex/loops/allocation) AND likely runs often AND has visible "
    "headroom. Say no for I/O-bound, trivial, cold-path, or already-tight code.\n"
    "Reply with EXACTLY one JSON array, no other text:\n"
    '[{"n":1,"name":"...","worth":"yes|no","why":"<=8 words"}, ...]\n\n'
)


def extract_functions(max_lines: int) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for module_name in MODULES:
        path = SRC / module_name
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in ALREADY_DONE:
                continue
            size = node.end_lineno - node.lineno + 1
            if size > max_lines or size < 4:
                continue
            functions.append(
                {
                    "module": module_name,
                    "name": node.name,
                    "lines": size,
                    "source": "\n".join(lines[node.lineno - 1 : node.end_lineno]),
                }
            )
    return functions


def _payload(result: Any) -> dict[str, Any] | list[Any]:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def triage(functions: list[dict[str, Any]], endpoint: str) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    budget = 0
    for function in functions:
        batch.append(function)
        budget += function["lines"]
        if budget > 260:
            batches.append(batch)
            batch, budget = [], 0
    if batch:
        batches.append(batch)

    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for index, group in enumerate(batches, 1):
                listing = "\n\n".join(
                    f"### {i}. {f['module']}::{f['name']} ({f['lines']} lines)\n"
                    f"```python\n{f['source']}\n```"
                    for i, f in enumerate(group, 1)
                )
                print(f"batch {index}/{len(batches)} ({len(group)} functions) ...", flush=True)
                reply = _payload(
                    await session.call_tool("chat", {"prompt": TRIAGE_PROMPT + listing})
                )
                text = str(reply.get("text") or "") if isinstance(reply, dict) else ""
                match = re.search(r"\[.*\]", text, re.S)
                if not match:
                    print(f"  batch {index}: unparseable, skipping", flush=True)
                    continue
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    print(f"  batch {index}: bad JSON, skipping", flush=True)
                    continue
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    position = int(item.get("n") or 0) - 1
                    if 0 <= position < len(group):
                        verdicts.append(
                            {
                                "module": group[position]["module"],
                                "name": group[position]["name"],
                                "lines": group[position]["lines"],
                                "worth": str(item.get("worth") or "no").lower(),
                                "why": str(item.get("why") or "")[:60],
                            }
                        )
    return verdicts


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4775/mcp")
    parser.add_argument("--max-lines", type=int, default=60)
    args = parser.parse_args()
    functions = extract_functions(args.max_lines)
    print(f"scouting {len(functions)} functions across {len(MODULES)} modules\n")
    verdicts = await triage(functions, args.endpoint)
    hits = [v for v in verdicts if v["worth"] == "yes"]
    print(f"\n== HIT LIST ({len(hits)}/{len(verdicts)} worth optimizing) ==")
    for verdict in hits:
        print(f"  {verdict['module']:<14} {verdict['name']:<32} {verdict['why']}")
    print("\n== skipped ==")
    for verdict in verdicts:
        if verdict["worth"] != "yes":
            print(f"  {verdict['module']:<14} {verdict['name']:<32} {verdict['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
