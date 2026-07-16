from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_copilot_html_instruction_prefers_artifacts_and_integrated_browser() -> None:
    text = (
        ROOT
        / ".github"
        / "instructions"
        / "copilot-html-artifacts.instructions.md"
    ).read_text(encoding="utf-8")

    assert "Use when the user asks GitHub Copilot in VS Code to show HTML" in text
    assert "raw HTML will render correctly in the chat surface" in text
    assert "create an `.html` artifact" in text
    assert "Open in Integrated Browser" in text
    assert "outside the repository" in text
    assert "VS Code webview or extension" in text
