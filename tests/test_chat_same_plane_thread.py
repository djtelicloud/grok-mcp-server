"""plane/fallback_policy must reach orchestrate and /v1 agent turns."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.http_server import _agent_turn_kwargs
from src.tools import chats as chats_mod
from src.utils import MetaLayer


def test_agent_turn_kwargs_forwards_plane_and_fallback_policy():
    kwargs = _agent_turn_kwargs(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "mode": "fast",
            "plane": "cli",
            "fallback_policy": "same_plane",
        }
    )
    assert kwargs["plane"] == "cli"
    assert kwargs["fallback_policy"] == "same_plane"
    assert kwargs["enable_agentic"] is False


def test_agent_turn_kwargs_defaults_and_sanitizes():
    kwargs = _agent_turn_kwargs({"messages": []})
    assert kwargs["plane"] == "auto"
    assert kwargs["fallback_policy"] == "cross_plane"
    bad = _agent_turn_kwargs(
        {"messages": [], "plane": "nope", "fallback_policy": "whatever"}
    )
    assert bad["plane"] == "auto"
    assert bad["fallback_policy"] == "cross_plane"


@pytest.mark.asyncio
async def test_chat_forwards_plane_policy_to_orchestrate(monkeypatch):
    captured = {}

    async def fake_orchestrate(**kwargs):
        captured.update(kwargs)
        return MetaLayer(
            generation="ok",
            finish_reason="final_answer",
            route="fast",
            plane="CLI",
            model="grok-4.5",
            cost_usd=0.0,
            tokens=1,
        )

    monkeypatch.setattr("src.server.orchestrate", fake_orchestrate)
    monkeypatch.setattr(
        chats_mod,
        "get_dynamic_context",
        AsyncMock(return_value=("", False, None)),
    )
    result = await chats_mod.chat(
        "hi",
        enable_agentic=False,
        plane="cli",
        fallback_policy="same_plane",
    )
    assert result.finish_reason == "final_answer"
    assert captured["requested_plane"] == "cli"
    assert captured["fallback_policy"] == "same_plane"
