"""Audience split: public stable clients must not inherit insider agent law."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BRAND_ROOTS = (
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / ".agents" / "AGENTS.md",
    ROOT / ".gemini" / "GEMINI.md",
    ROOT / ".github" / "copilot-instructions.md",
)


def test_brand_roots_lead_with_audience_first() -> None:
    for path in BRAND_ROOTS:
        text = path.read_text(encoding="utf-8")
        assert "Audience first" in text, path.relative_to(ROOT)
        assert text.index("Audience first") < 400, path.relative_to(ROOT)
        assert "4765" in text
        assert "using-unigrok" in text or "using-unigrok" in text.lower()
        # Public path forbids inventing Forge/land for ordinary installs.
        lowered = " ".join(text.lower().split())
        assert "forge" in lowered or "4766" in text
        assert "ready for supervisor" in lowered


def test_session_rehydrate_has_product_cwd_gate() -> None:
    for rel in (
        ".agents/skills/session-rehydrate/SKILL.md",
        ".claude/skills/session-rehydrate/SKILL.md",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "product-cwd gate" in text.lower() or "Product-cwd gate" in text
        assert "foreign app" in text.lower()
        assert "product rehydrate" in text.lower()
        assert "do not run full product rehydrate" in text.lower()
        # Description frontmatter must mention the gate.
        assert "product checkout" in text
        assert "stable MCP" in text or "stable MCP" in text.replace("`", "")


def test_using_unigrok_status_language_for_vibe_apps() -> None:
    public = (ROOT / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    agent = (ROOT / ".agents" / "skills" / "using-unigrok" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert public == agent
    assert "Status language (vibe apps vs UniGrok product)" in public
    assert "Done" in public and "Blocked" in public
    assert "Ready for supervisor" in public
    assert "can_mutate_workspace" in public
    for rel in (
        ".claude/skills/using-unigrok/SKILL.md",
        ".github/skills/using-unigrok/SKILL.md",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "Status language (vibe apps vs UniGrok product)" in text
        assert "Ready for supervisor" in text


def test_public_pack_public_vs_insider_exists() -> None:
    body = (
        ROOT
        / "docs"
        / "public-intelligence"
        / "packs"
        / "v0-public-vs-insider-agent-law.md"
    )
    text = body.read_text(encoding="utf-8")
    assert "Ready for supervisor" in text
    assert "Task titles, not numbers" in text
    assert "can_mutate_workspace" in text
    assert "session-rehydrate" in text


def test_rehydrate_pack_manifest_matches_contributor_only_header() -> None:
    manifest = json.loads(
        (
            ROOT
            / "docs"
            / "public-intelligence"
            / "packs"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    pack = next(
        item
        for item in manifest["packs"]
        if item["pack_id"] == "rehydrate-brand-next-steps"
    )
    body = (ROOT / pack["body_path"]).read_text(encoding="utf-8")
    assert pack["audience"] == "contributor"
    assert "**Audience:** contributor agents developing UniGrok" in body


@pytest.mark.asyncio
async def test_stable_discover_self_states_contributor_workflows_disabled(
    monkeypatch,
) -> None:
    from src.tools.system import grok_mcp_discover_self

    monkeypatch.setenv("UNIGROK_SERVICE_MODE", "stable")
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(
        "src.tools.system.grok_cli_plane_status",
        lambda timeout_sec=5.0: {
            "state": "ready",
            "ready": True,
            "binary": True,
            "auth": "oauth",
            "setup_command": "unused",
        },
    )

    result = await grok_mcp_discover_self()
    prose = (result.response or "") + "\n" + (result.text or "")
    assert result.data["bootstrap"]["can_mutate_workspace"] is False
    assert result.data["bootstrap"]["can_use_swarm"] is False
    assert "Contributor workflows disabled" in prose
    assert "Ready for supervisor" in prose or "Ready-for-supervisor" in prose
    assert "using-unigrok" in prose.lower()
    action_ids = {
        item.get("id")
        for item in result.data["bootstrap"].get("next_actions") or []
        if isinstance(item, dict)
    }
    assert "stable_client_use_agent" in action_ids
    assert "stable_client_status_language" in action_ids
    for item in result.data["bootstrap"].get("next_actions") or []:
        if item.get("id") in {
            "stable_client_use_agent",
            "stable_client_status_language",
        }:
            assert item["action"]["instructions"]
