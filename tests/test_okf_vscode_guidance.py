from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_tool_documents_vscode_copilot_patterns() -> None:
    text = (ROOT / "docs" / "okf" / "agent-tool.md").read_text(encoding="utf-8")

    assert "VS Code + GitHub Copilot integration patterns" in text
    assert 'X-Client-ID: vscode' in text
    assert 'fallback_policy="same_plane"' in text
    assert "workspace_context" in text
    assert "credentials.notices" in text
    assert '"mode": "reasoning"' in text


def test_chat_modes_points_http_clients_to_agent_entrypoint() -> None:
    text = (ROOT / "docs" / "okf" / "chat-modes.md").read_text(encoding="utf-8")

    assert "VS Code / Copilot note" in text
    assert 'mode="fast"' in text
    assert "Agent Entrypoint" in text


def test_metrics_tool_documents_ide_session_checks() -> None:
    text = (ROOT / "docs" / "okf" / "metrics-tool.md").read_text(encoding="utf-8")

    assert "IDE session checks" in text
    assert "grok_mcp_status(view=\"json\")" in text
    assert "X-Client-ID" in text
    assert "cost_usd" in text
