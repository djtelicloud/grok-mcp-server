from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_copilot_playbook_exists_and_covers_contract() -> None:
    text = (ROOT / "docs" / "okf" / "copilot-agent-playbook.md").read_text(
        encoding="utf-8"
    )

    assert "VS Code Copilot Agent Playbook" in text
    assert "workspace_context" in text
    assert "workspace_label" in text
    assert "X-Client-ID: vscode" in text
    assert "credentials.notices" in text
    assert 'fallback_policy="same_plane"' in text
    assert "cli_first" in text
    assert "XAI_API_KEY" in text
    assert 'mode="fast"' in text
    assert 'mode="reasoning"' in text
    assert 'mode="thinking"' in text
    assert 'mode="research"' in text


def test_okf_index_lists_copilot_playbook() -> None:
    text = (ROOT / "docs" / "okf" / "index.md").read_text(encoding="utf-8")

    assert "copilot-agent-playbook" in text
    assert "VS Code Copilot Agent Playbook" in text
