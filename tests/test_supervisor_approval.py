from pathlib import Path

from scripts.supervisor_approval import (
    GateDecision,
    augment_cursor_evidence,
    collect_check_states,
    has_exact_cursor_approval,
    decide_gate,
    declared_risk,
    inferred_risk,
    waiting_for_required_ci,
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
    assert "github.event.check_run.name != 'evaluate'" in workflow
    assert "CHECK_RUN_HEAD_SHA" in workflow


def test_collect_check_states_prefers_newer_finished_success_over_older_in_progress():
    checks = collect_check_states(
        [
            {
                "name": "build (3.11)",
                "id": 1,
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-07-16T01:00:00Z",
                "completed_at": None,
            },
            {
                "name": "build (3.11)",
                "id": 2,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-07-16T01:05:00Z",
                "completed_at": "2026-07-16T01:06:00Z",
            },
        ]
    )
    assert checks["build (3.11)"] == "success"


def test_collect_check_states_prefers_newer_success_over_stale_failure():
    checks = collect_check_states(
        [
            {
                "name": "build (3.11)",
                "id": 10,
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-07-16T01:00:00Z",
                "completed_at": "2026-07-16T01:01:00Z",
            },
            {
                "name": "build (3.11)",
                "id": 11,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-07-16T01:10:00Z",
                "completed_at": "2026-07-16T01:11:00Z",
            },
        ]
    )
    assert checks["build (3.11)"] == "success"


def test_collect_check_states_prefers_newer_in_progress_over_stale_success():
    checks = collect_check_states(
        [
            {
                "name": "build (3.11)",
                "id": 20,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-07-16T01:00:00Z",
                "completed_at": "2026-07-16T01:01:00Z",
            },
            {
                "name": "build (3.11)",
                "id": 21,
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-07-16T01:20:00Z",
                "completed_at": None,
            },
        ]
    )
    assert checks["build (3.11)"] == "in_progress"


def test_waiting_for_required_ci_detects_build_gap():
    assert waiting_for_required_ci(
        GateDecision("pending", "waiting for build (3.11), build (3.12)")
    )
    assert not waiting_for_required_ci(
        GateDecision("pending", "waiting for Cursor approval")
    )
    assert not waiting_for_required_ci(
        GateDecision("success", "Cursor failover approved for risk: low")
    )


def test_cursor_approval_must_match_the_current_head():
    reviews = [{"user": {"login": "cursor[bot]"}, "state": "APPROVED", "commit_id": "old"}]
    assert not has_exact_cursor_approval(reviews, "new")
    reviews[0]["commit_id"] = "new"
    assert has_exact_cursor_approval(reviews, "new")


def test_bugbot_neutral_and_missing_security_check_can_pass():
    raw = {
        "build (3.11)": "success",
        "build (3.12)": "success",
        "Project Site": "success",
        "Control Cloud Run Image": "success",
        "evals-offline": "success",
        "docker": "success",
        "Cursor Bugbot": "neutral",
    }
    checks = augment_cursor_evidence(raw, reviews=[])
    decision = decide_gate(
        declared="low",
        inferred="low",
        checks=checks,
        statuses={"Cursor Approval": "success"},
    )
    assert checks["Cursor Bugbot"] == "success"
    assert checks["Cursor Security Agent: Security Reviewer"] == "success"
    assert decision.state == "success"


def test_security_reviewer_changes_requested_blocks_gate():
    raw = {
        "build (3.11)": "success",
        "build (3.12)": "success",
        "Project Site": "success",
        "Control Cloud Run Image": "success",
        "evals-offline": "success",
        "docker": "success",
        "Cursor Bugbot": "success",
    }
    reviews = [
        {
            "state": "CHANGES_REQUESTED",
            "body": "<!-- CURSOR_AUTOMATION_ID: f12530a3-7ff4-11f1-ba66-0e7d0216e441 | RUN_ID: x -->\nblock",
        }
    ]
    checks = augment_cursor_evidence(raw, reviews=reviews)
    decision = decide_gate(
        declared="low",
        inferred="low",
        checks=checks,
        statuses={"Cursor Approval": "success"},
    )
    assert decision.state == "failure"
    assert "Security Reviewer" in decision.description
