"""Brand-root files must put plain human chat first for new users."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Files new agents load first — each must surface the human-radio law.
ROOT_MARKERS = (
    ROOT / ".agents" / "AGENTS.md",
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",  # Claude Code root (same path as Claude.md on case-insensitive FS)
    ROOT / ".gemini" / "GEMINI.md",
    ROOT / ".github" / "copilot-instructions.md",
    ROOT / ".grok" / "README.md",
)


def test_talk_to_humans_first_is_present_in_brand_roots() -> None:
    for path in ROOT_MARKERS:
        text = path.read_text(encoding="utf-8")
        assert "Talk to humans first" in text, f"missing banner in {path.relative_to(ROOT)}"
        # Early in the file so models see it before deep product detail.
        assert text.index("Talk to humans first") < 800, (
            f"banner too deep in {path.relative_to(ROOT)}"
        )


def test_shared_agents_forbids_chat_pollution() -> None:
    text = (ROOT / ".agents" / "AGENTS.md").read_text(encoding="utf-8")
    assert "product bug" in text.lower() or "Chat pollution" in text
    assert "No** paste diffs" in text or "Do not** paste diffs" in text or "diffs" in text
    assert "Human language" in text
    assert "Ready for supervisor" in text
