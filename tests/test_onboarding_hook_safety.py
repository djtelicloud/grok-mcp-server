from __future__ import annotations

import json
import subprocess
import sys

import pytest

from unigrok_public import server


def _run_cursor_hook(payload: dict[str, str]) -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, "-c", server.CURSOR_AGENT_HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ({}, "ask"),
        ({"tool_name": ""}, "ask"),
        ({"tool_name": "read_file"}, "ask"),
        ({"toolName": "delete_agent_credentials"}, "ask"),
        ({"tool_name": "agent"}, "allow"),
        ({"tool_name": "agent_result"}, "allow"),
        ({"tool_name": "mcp__grok__agent"}, "allow"),
        ({"tool_name": "mcp__grok__agent_result"}, "allow"),
    ),
)
def test_cursor_hook_requires_an_exact_known_agent_tool(
    payload: dict[str, str], expected: str
) -> None:
    assert _run_cursor_hook(payload) == {"permission": expected}
