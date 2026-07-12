from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_control_center_avoids_dynamic_selector_and_guard_html() -> None:
    source = (ROOT / "mcp_ui" / "app.js").read_text(encoding="utf-8")

    assert 'querySelector(`.nav-btn[data-tab="${tabId}"]`)' not in source
    assert '$("simExplanation").innerHTML' not in source
    assert 'viewer.innerHTML = parseMarkdown(cleanText)' not in source


def test_markdown_renderer_does_not_apply_incomplete_scheme_filter() -> None:
    source = (ROOT / "mcp_ui" / "app.js").read_text(encoding="utf-8")

    assert ".replace(/javascript:/gi" not in source


def test_land_status_does_not_log_runtime_exception_details() -> None:
    source = (ROOT / "scripts" / "land-status.py").read_text(encoding="utf-8")

    assert 'unavailable ({exc})' not in source
