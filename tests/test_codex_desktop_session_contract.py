from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / ".codex" / "desktop-session-contract.md"
CONVERSATION_CANVAS = ROOT / ".codex" / "chat-fragments" / "conversation-canvas.html"

REQUIRED_HEADINGS = (
    "## Purpose",
    "## Codex Desktop superpowers",
    "## Codex collaboration modes",
    "## In-chat visual artifacts",
    "## UniGrok agent modes vs credential planes",
    "## Exact-head integration laws",
    "## Session start checklist",
    "## Non-goals",
)

REQUIRED_PHRASES = (
    "apply-patch",
    "commit-anchored memory",
    "credential planes",
    "exact-head",
    "do not auto-land",
    "orthogonal",
    "Default mode",
    "Plan mode",
    "conversation-canvas.html",
)


def test_codex_desktop_session_contract_is_complete() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    assert len(text) >= 4_000
    for heading in REQUIRED_HEADINGS:
        assert heading in text
    for phrase in REQUIRED_PHRASES:
        assert phrase in text


def test_conversation_canvas_uses_host_theme_tokens_and_bounded_follow_up() -> None:
    text = CONVERSATION_CANVAS.read_text(encoding="utf-8")

    assert '<section id="conversation-canvas"' in text
    assert "color: var(--foreground);" in text
    assert "var(--cyan, var(--primary))" in text
    assert "var(--teal, var(--viz-series-2))" in text
    assert "window.openai?.sendFollowUpMessage" in text
    assert "fetch(" not in text
    assert "https://" not in text
