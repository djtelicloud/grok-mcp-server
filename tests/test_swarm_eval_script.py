import json
from pathlib import Path

import pytest

import scripts.run_swarm_evals as sweep


ROOT = Path(__file__).parent.parent


def test_discovers_versioned_golden_target_manifests():
    specs = sweep.discover_targets(ROOT)
    assert [spec.name for spec in specs] == ["nsquared_dedup", "slow_loop_optimize"]
    assert specs[0].focus_node == "function:dedup"
    assert specs[1].bench_command.endswith("bench_loop_opt.py")


def test_manifest_refuses_traversal(tmp_path):
    root = tmp_path / "evals" / "tasks" / "swarm_targets" / "bad"
    root.mkdir(parents=True)
    (root / "target.json").write_text(
        json.dumps(
            {
                "version": 1,
                "target": "../escape.py",
                "focus_node": "function:f",
                "test_target": "test_f.py",
                "bench_script": "bench_f.py",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="one file name"):
        sweep.discover_targets(tmp_path)


def test_live_opt_in_gate_short_circuits_before_cli_probe(monkeypatch):
    monkeypatch.delenv("UNIGROK_SWARM_EVALS_LIVE", raising=False)
    monkeypatch.setattr(
        sweep,
        "grok_cli_plane_status",
        lambda **kwargs: pytest.fail("CLI should not be probed without opt-in"),
    )
    assert "UNIGROK_SWARM_EVALS_LIVE=1" in sweep.gate_errors()[0]


def test_zero_feasible_is_valid_but_vacuous_cost_or_failed_status_is_not():
    payload = {
        "format": "unigrok-swarm-status-v2",
        "status": "completed",
        "aggregates": {
            "candidates_total": 2,
            "feasibility_rate": 0.0,
            "cost_to_optimize_usd": 0.0,
        },
        "budget": {"spent_usd": 0.0},
    }
    assert sweep.payload_errors(payload) == []
    payload["aggregates"]["candidates_total"] = 0
    assert any("no persisted candidates" in error for error in sweep.payload_errors(payload))
    payload["aggregates"]["candidates_total"] = 2
    payload["aggregates"]["cost_to_optimize_usd"] = 0.01
    assert any("nonzero metered cost" in error for error in sweep.payload_errors(payload))
    payload["status"] = "failed"
    assert any("terminal status" in error for error in sweep.payload_errors(payload))


@pytest.mark.parametrize("bad_cost", [float("nan"), float("inf"), -0.01])
def test_nonfinite_or_negative_cost_never_counts_as_exact_zero(bad_cost):
    payload = {
        "format": "unigrok-swarm-status-v2",
        "status": "completed",
        "aggregates": {
            "candidates_total": 1,
            "cost_to_optimize_usd": bad_cost,
        },
        "budget": {"spent_usd": bad_cost},
    }
    assert any("exact-zero" in error for error in sweep.payload_errors(payload))


@pytest.mark.asyncio
async def test_timeout_requests_cancel_and_returns_partial_payload(monkeypatch):
    waits = []

    class Runner:
        async def wait(self, task_id, timeout):
            waits.append((task_id, timeout))
            return len(waits) > 1

    cancelled = []

    async def launch(*args, **kwargs):
        return "task-1", "started"

    async def cancel(task_id):
        cancelled.append(task_id)

    async def status(task_id, view):
        return json.dumps(
            {
                "format": "unigrok-swarm-status-v2",
                "status": "cancelled",
                "aggregates": {"cost_to_optimize_usd": 0.0},
                "budget": {"spent_usd": 0.0},
            }
        )

    monkeypatch.setattr(sweep.swarm_tools, "_launch_code_swarm", launch)
    monkeypatch.setattr(sweep.swarm_tools, "_get_runner", lambda: Runner())
    monkeypatch.setattr(sweep.swarm_tools, "cancel_swarm", cancel)
    monkeypatch.setattr(sweep.swarm_tools, "get_swarm_status", status)
    spec = sweep.TargetSpec("one", "a.py", "function:f", "test_a.py", "python bench.py")
    payload, timed_out = await sweep.run_target(spec, timeout=2.0, cancel_grace=3.0)
    assert timed_out is True
    assert payload["status"] == "cancelled"
    assert cancelled == ["task-1"]
    assert waits == [("task-1", 2.0), ("task-1", 3.0)]


def test_report_renders_missing_measurements_honestly():
    report = sweep.render_report(
        [
            sweep.SweepResult(
                "empty",
                {
                    "status": "completed",
                    "aggregates": {
                        "candidates_total": 0,
                        "feasibility_rate": None,
                        "best_latency_improvement_pct": None,
                        "best_memory_improvement_pct": None,
                        "cost_to_optimize_usd": 0.0,
                    },
                },
                [],
            )
        ]
    )
    assert "persisted candidates" in report
    assert "| 0 | — | — | — | $0.0000 | OK |" in report


def test_report_output_must_stay_in_workspace(tmp_path):
    assert sweep.resolve_output(tmp_path, Path("reports/sweep.md")) == (
        tmp_path / "reports" / "sweep.md"
    )
    with pytest.raises(ValueError, match="inside the attached workspace"):
        sweep.resolve_output(tmp_path, tmp_path.parent / "escape.md")
