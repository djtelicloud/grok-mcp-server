"""Hermetic contract for UniGrok multi-agent / subagent surfaces.

UniGrok does **not** expose Grok-Build-style local ``spawn_subagent``. Public
multi-agent work is:

1. ``agent(mode=\"research\")`` → server-side xAI fan-out via ``agent_count`` in
   {4, 16} only (env ``UNIGROK_RESEARCH_AGENT_COUNT``).
2. Deferred ``submit_research_job(..., agent_count=4|16)``.
3. Headless CLI ``cli_isolated=True`` → always ``--no-subagents`` (and no
   memory / web / interactive prompts) so contributor isolation cannot inherit
   ambient subagent context.

These tests lock that product boundary for the Grok IDE lane.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.tools.chats import _research_agent_count, agent
from src.tools.research import submit_research_job
from src.utils import MetaLayer, _build_grok_cli_args

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_FANOUT = frozenset({4, 16})


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("4", 4),
        ("16", 16),
        ("7", 4),
        ("junk", 4),
        ("", 4),
        ("0", 4),
        ("-1", 4),
    ],
)
def test_research_agent_count_only_allows_sdk_fanout_sizes(
    monkeypatch, env_value, expected
) -> None:
    if env_value == "":
        monkeypatch.delenv("UNIGROK_RESEARCH_AGENT_COUNT", raising=False)
        monkeypatch.setenv("UNIGROK_RESEARCH_AGENT_COUNT", "")
    else:
        monkeypatch.setenv("UNIGROK_RESEARCH_AGENT_COUNT", env_value)
    assert _research_agent_count() == expected
    assert _research_agent_count() in ALLOWED_FANOUT or expected == 4


@pytest.mark.asyncio
async def test_research_mode_is_only_mode_that_requests_agent_count(
    monkeypatch,
) -> None:
    mock_run = AsyncMock(return_value=MetaLayer(generation="ok"))
    monkeypatch.setattr("src.tools.chats.run_agent_turn", mock_run)
    monkeypatch.setenv("UNIGROK_RESEARCH_AGENT_COUNT", "16")

    await agent(task="survey", mode="research")
    assert mock_run.call_args.kwargs["agent_count"] == 16
    assert mock_run.call_args.kwargs["include"] == ["inline_citations"]

    mock_run.reset_mock()
    for mode in ("auto", "fast", "reasoning", "thinking"):
        await agent(task="q", mode=mode)
        assert mock_run.call_args.kwargs.get("agent_count") is None, mode


@pytest.mark.asyncio
async def test_submit_research_job_rejects_illegal_agent_count() -> None:
    bad = await submit_research_job("valid prompt", agent_count=8)
    assert "error" in bad
    assert "4 or 16" in bad["error"]


def test_isolated_cli_args_always_disable_subagents() -> None:
    isolated = _build_grok_cli_args(
        cli_prompt="p",
        model_name="grok-4.5",
        dynamic_sys_prompt="sys",
        output_format="json",
        isolated=True,
    )
    assert "--no-subagents" in isolated
    assert "--no-memory" in isolated
    assert "--disable-web-search" in isolated
    assert "--permission-mode" in isolated
    assert isolated[isolated.index("--permission-mode") + 1] == "dontAsk"

    normal = _build_grok_cli_args(
        cli_prompt="p",
        model_name="grok-4.5",
        dynamic_sys_prompt="sys",
        output_format="json",
        isolated=False,
    )
    assert "--no-subagents" not in normal


def test_using_unigrok_documents_research_fanout_not_local_spawn() -> None:
    skill = (
        ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md"
    ).read_text(encoding="utf-8")
    public = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill == public
    assert "research" in skill
    # Product law section we add for subagent surface
    assert "Multi-agent / research fan-out" in skill
    assert "server-side" in skill or "xAI" in skill
    assert "4" in skill and "16" in skill
    assert "spawn_subagent" in skill or "local subagent" in skill.lower()
    assert "--no-subagents" in skill or "no-subagents" in skill
