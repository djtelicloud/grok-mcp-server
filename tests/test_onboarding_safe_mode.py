from __future__ import annotations

import json
import subprocess
import sys

from unigrok_public import server


def _hook_decision(payload: dict[str, str]) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", server.CURSOR_AGENT_HOOK],
        input=json.dumps(payload),
        capture_output=True,
        check=True,
        text=True,
    )
    return str(json.loads(completed.stdout)["permission"])


def test_safe_mode_omits_cursor_automatic_approval() -> None:
    global_plan = server._client_onboarding_plan(
        "cursor", "global", safe_mode=True
    )
    project_plan = server._client_onboarding_plan(
        "cursor", "project", safe_mode=True
    )

    for plan in (global_plan, project_plan):
        assert plan["safe_mode"] is True
        assert plan["automatic_tool_approval_offered"] is False
        assert "hooks" not in plan
        assert not any(
            item["path"].endswith("before-unigrok-agent.py")
            for item in plan["files"]
        )
        assert "safe_mode_note" in plan


def test_safe_mode_omits_other_client_automatic_approval() -> None:
    for client in ("antigravity", "codex", "claude_code", "github_copilot"):
        plan = server._client_onboarding_plan(client, "global", safe_mode=True)
        assert plan["safe_mode"] is True
        assert plan["automatic_tool_approval_offered"] is False
        assert "auto_approve" not in plan
        assert "safe_mode_note" in plan


def test_default_onboarding_behavior_remains_compatible() -> None:
    cursor = server._client_onboarding_plan("cursor", "global")
    antigravity = server._client_onboarding_plan("antigravity", "global")

    assert cursor["safe_mode"] is False
    assert cursor["automatic_tool_approval_offered"] is True
    assert "hooks" in cursor
    assert antigravity["automatic_tool_approval_offered"] is True
    assert "auto_approve" in antigravity


def test_onboarding_pack_is_ground_only_not_forge_or_sky() -> None:
    """Day-1 public pack must not imply Forge Docker or Sky/Space nodes."""
    plan = server._client_onboarding_plan("claude_code", "global")
    pack = plan["pack"]
    assert pack["name"] == "groundcommand_public"
    assert "using-unigrok" in pack["includes"]
    assert "mission-brief-harness" in pack["includes"]
    for denied in (
        "forge_docker",
        "skycommand_node",
        "spacecommand_node",
        "private_operator_skills",
        "full_agents_skills_tree",
    ):
        assert denied in pack["does_not_include"]
    assert "Sky-class" in pack["heavier_capacity"]


def test_cursor_hook_never_allows_empty_or_ambiguous_tool_names() -> None:
    assert _hook_decision({}) == "ask"
    assert _hook_decision({"tool_name": ""}) == "ask"
    assert _hook_decision({"tool_name": "dangerous_agent"}) == "ask"
    assert _hook_decision({"tool_name": "agent"}) == "allow"
    assert _hook_decision({"tool_name": "grok/agent"}) == "allow"
    assert _hook_decision({"tool_name": "mcp__grok__agent_result"}) == "allow"
