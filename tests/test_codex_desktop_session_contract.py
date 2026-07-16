from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / ".codex" / "desktop-session-contract.md"

REQUIRED_HEADINGS = (
    "## Purpose",
    "## Codex Desktop superpowers",
    "## Codex collaboration modes",
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
)


def test_codex_desktop_session_contract_is_complete() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    assert len(text) >= 4_000
    for heading in REQUIRED_HEADINGS:
        assert heading in text
    for phrase in REQUIRED_PHRASES:
        assert phrase in text
