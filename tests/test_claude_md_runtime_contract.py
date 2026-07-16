from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_md_documents_cli_readiness_and_session_continuity() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "`grok models` probe" in text
    assert "`GET /readyz`" in text
    assert "`--session-id` names a new" in text
    assert "`--resume` continues an existing one" in text
    assert "`--fork-session --session-id ...`" in text
    assert "CLI session continuity" in text
