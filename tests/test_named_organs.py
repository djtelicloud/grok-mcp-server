# ruff: noqa
"""Named UniGrok organs are agent doors. chat stays the smoke one-shot."""

from __future__ import annotations

import asyncio
import inspect

from unigrok_public import server

_ORGANS = ("counsel", "swarm", "hive", "cascade", "ask")


def test_public_tool_names_include_organs_and_chat() -> None:
    names = list(server.PUBLIC_TOOL_NAMES)
    assert names.count("chat") == 1
    for organ in _ORGANS:
        assert names.count(organ) == 1
    assert len(names) == 34
    chat_at = names.index("chat")
    assert names[chat_at + 1 : chat_at + 6] == list(_ORGANS)


def test_list_tools_matches_public_names() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert [tool.name for tool in tools] == list(server.PUBLIC_TOOL_NAMES)


def test_organs_call_agent_with_native_depth(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_agent(**kwargs):
        captured.append(kwargs)
        return {"status": "complete", "text": "ok", "organ_echo": kwargs.get("depth")}

    monkeypatch.setattr(server, "agent", fake_agent)
    assert asyncio.run(server.counsel("c"))["status"] == "complete"
    assert asyncio.run(server.swarm("s"))["status"] == "complete"
    assert asyncio.run(server.hive("h"))["status"] == "complete"
    assert asyncio.run(server.cascade("k"))["status"] == "complete"
    assert asyncio.run(server.ask("a"))["status"] == "complete"
    depths = [row.get("depth") for row in captured]
    levels = [row.get("level") for row in captured]
    tasks = [row.get("task") for row in captured]
    assert tasks == ["c", "s", "h", "k", "a"]
    assert depths[0] == "deep"
    assert depths[1] == "auto"
    assert depths[2] == "hive"
    assert levels[3] == "ultra"
    assert captured[4].get("task") == "a"


def test_named_organs_source_does_not_phone_terminalgrok() -> None:
    src = inspect.getsource(server._organ_via_agent)
    src += inspect.getsource(server.counsel)
    src += inspect.getsource(server.swarm)
    src += inspect.getsource(server.hive)
    src += inspect.getsource(server.cascade)
    src += inspect.getsource(server.ask)
    assert "4767" not in src
    assert "TerminalGrok" in inspect.getsource(server._organ_via_agent)
    assert "agent(" in src
