from pathlib import Path

from scripts.supervisor_approval import (
    has_exact_cursor_approval,
    decide_gate,
    declared_risk,
    inferred_risk,
)


ROOT = Path(__file__).resolve().parents[1]


def _checks(*, cursor=True):
    checks = {
        "build (3.11)": "success",
        "build (3.12)": "success",
        "Project Site": "success",
        "Control Cloud Run Image": "success",
        "evals-offline": "success",
        "docker": "success",
        "Cursor Bugbot": "success",
        "Cursor Security Agent: Security Reviewer": "success",
    }
    if cursor:
        checks["Cursor Approval Agent: Pull Request Router and Approver"] = "success"
    return checks


def test_medium_runtime_packet_can_fail_over_to_cursor():
    decision = decide_gate(
        declared="medium",
        inferred="medium",
        checks=_checks(),
        statuses={},
    )
    assert decision.state == "success"


def test_high_control_plane_packet_stays_with_codex():
    decision = decide_gate(
        declared="high",
        inferred="high",
        checks={},
        statuses={},
    )
    assert decision.state == "pending"
    assert "Codex Approval" in decision.description


def test_high_path_cannot_be_declared_medium():
    decision = decide_gate(
        declared="medium",
        inferred="high",
        checks=_checks(),
        statuses={},
    )
    assert decision.state == "failure"


def test_low_declaration_cannot_hide_runtime_change():
    decision = decide_gate(
        declared="low",
        inferred="medium",
        checks=_checks(),
        statuses={},
    )
    assert decision.state == "failure"


def test_risk_declaration_is_unambiguous():
    assert declared_risk("risk: medium", []) == "medium"
    assert declared_risk("risk: low\nrisk: high", []) is None
    assert inferred_risk(["src/utils.py"]) == "medium"
    assert inferred_risk(["scripts/land"]) == "high"
    assert inferred_risk(["scripts/supervisor_approval.py"]) == "high"


def test_supervisor_status_event_does_not_retrigger_its_own_workflow():
    workflow = (ROOT / ".github" / "workflows" / "supervisor-approval.yml").read_text(
        encoding="utf-8"
    )
    assert "github.event.context != 'Supervisor Approval'" in workflow


def test_cursor_approval_must_match_the_current_head():
    reviews = [{"user": {"login": "cursor[bot]"}, "state": "APPROVED", "commit_id": "old"}]
    assert not has_exact_cursor_approval(reviews, "new")
    reviews[0]["commit_id"] = "new"
    assert has_exact_cursor_approval(reviews, "new")
