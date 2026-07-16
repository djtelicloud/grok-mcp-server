from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_silent_team_check_topic_covers_low_cost_review_pattern() -> None:
    text = (ROOT / "docs" / "okf" / "silent-team-check.md").read_text(
        encoding="utf-8"
    )

    assert "Silent Team Check" in text
    assert "token-efficient" in text
    assert "one cheap reviewer" in text
    assert 'agent(mode="fast")' in text
    assert 'agent(mode="reasoning")' in text
    assert "Do not fan out by default" in text
    assert "team-check: passed" in text
    assert "workspace_context" in text


def test_okf_index_lists_silent_team_check() -> None:
    text = (ROOT / "docs" / "okf" / "index.md").read_text(encoding="utf-8")

    assert "silent-team-check" in text
    assert "Silent Team Check" in text
