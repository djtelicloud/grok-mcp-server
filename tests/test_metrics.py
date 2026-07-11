from datetime import datetime, timedelta

import pytest

from src.metrics import build_metrics_snapshot, fetch_provider_api_usage, telemetry_metadata
from src.utils import GrokSessionStore


def _row(*, created_at, plane="API", success=1, latency=1.0, cost=0.0, metadata=None):
    return {
        "created_at": created_at.isoformat(),
        "chosen_plane": plane,
        "success": success,
        "latency": latency,
        "cost": cost,
        "metadata": metadata,
    }


def test_structured_metrics_separate_api_billing_from_cli_subscription():
    now = datetime(2026, 7, 10, 12, 0, 0)
    rows = [
        _row(
            created_at=now,
            plane="API",
            latency=2.0,
            cost=0.012,
            metadata='{"model":"grok-4.5","tokens":120,"token_kind":"provider_exact","caller":"codex","routing":{"v":1,"route_class":"planning","resolved_model":"grok-4.5","why":"auto","why_detail":"reasoning_score","features":{"feature_hash":"abc"},"candidates":[]}}',
        ),
        _row(
            created_at=now,
            plane="CLI",
            latency=1.0,
            cost=0.0,
            metadata='{"model":"grok-composer-2.5-fast","tokens":80,"token_kind":"local_estimate","caller":"claude"}',
        ),
        _row(created_at=now - timedelta(days=1), cost=0.5),
    ]

    snapshot = build_metrics_snapshot(rows, now=now)
    today = snapshot["usage"]["today"]

    assert today["summary"]["requests"] == 2
    assert today["summary"]["api_cost_usd"] == pytest.approx(0.012)
    assert today["summary"]["tracked_tokens"] == 200
    assert today["summary"]["caller_attributed_requests"] == 2
    assert today["planes"]["API"]["exact_token_requests"] == 1
    assert today["planes"]["CLI"]["estimated_token_requests"] == 1
    assert snapshot["usage"]["cli_subscription"]["cost_per_request_usd"] is None
    assert snapshot["usage"]["cli_subscription"]["provider_usage_available"] is False
    assert snapshot["planes"]["CLI"]["total_cost_usd"] == 0.0
    assert today["summary"]["route_classes"] == {"planning": 1}
    assert today["summary"]["selection_reasons"] == {"reasoning_score": 1}
    assert today["callers"] == {
        "claude": {"requests": 1, "success_rate": 1.0, "total_cost_usd": 0.0},
        "codex": {"requests": 1, "success_rate": 1.0, "total_cost_usd": pytest.approx(0.012)},
    }
    assert snapshot["usage"]["lifetime"]["callers"]["codex"]["requests"] == 1
    receipt = today["recent_routes"][0]
    assert receipt["routing"]["resolved_model"] == "grok-4.5"
    assert "intent" not in receipt
    assert snapshot["usage"]["data_quality"]["routing_receipt_rows"] == 1


def test_structured_metrics_empty_period_is_null_not_fake_zero():
    now = datetime(2026, 7, 10, 12, 0, 0)
    snapshot = build_metrics_snapshot([], now=now)
    summary = snapshot["usage"]["today"]["summary"]

    assert summary["requests"] == 0
    assert summary["success_rate"] is None
    assert summary["avg_latency_sec"] is None
    assert summary["p95_latency_sec"] is None
    assert summary["caller_attributed_requests"] == 0
    assert snapshot["usage"]["today"]["planes"] == {}
    assert snapshot["usage"]["today"]["callers"] == {}


def test_telemetry_metadata_tolerates_old_and_malformed_rows():
    assert telemetry_metadata({"metadata": None}) == {}
    assert telemetry_metadata({"metadata": "not-json"}) == {}
    assert telemetry_metadata({"metadata": {"model": "grok"}}) == {"model": "grok"}


def test_semantic_eval_scores_aggregate_and_null_at_zero_rows():
    now = datetime(2026, 7, 10, 12, 0, 0)
    rows = [
        _row(
            created_at=now,
            metadata='{"semantic":{"v":1,"scores":{"correctness":4,"tool_efficiency":5,"safety":5},"overall":4.67,"judge_cost_usd":0.0012}}',
        ),
        _row(
            created_at=now,
            metadata='{"semantic":{"v":1,"scores":{"correctness":2,"tool_efficiency":3,"safety":5},"overall":3.33,"judge_cost_usd":0.001}}',
        ),
        _row(created_at=now),  # ungraded row does not dilute the averages
        _row(created_at=now, metadata='{"semantic":"malformed-not-a-dict"}'),
        _row(created_at=now, metadata='{"semantic":{"scores":"also-malformed"}}'),
    ]

    snapshot = build_metrics_snapshot(rows, now=now, semantic_evals={"mode": "shadow"})
    semantic = snapshot["usage"]["today"]["summary"]["semantic"]

    # The scores-malformed row still counts as scored but contributes zeros.
    assert semantic["scored_requests"] == 3
    assert semantic["avg_correctness"] == pytest.approx((4 + 2) / 3)
    assert semantic["avg_overall"] == pytest.approx((4.67 + 3.33) / 3)
    assert semantic["judge_cost_usd"] == pytest.approx(0.0022)
    assert snapshot["usage"]["data_quality"]["semantic_scored_rows"] == 3
    assert snapshot["semantic_evals"] == {"mode": "shadow"}

    empty = build_metrics_snapshot([], now=now)
    empty_semantic = empty["usage"]["today"]["summary"]["semantic"]
    assert empty_semantic["scored_requests"] == 0
    assert empty_semantic["avg_overall"] is None
    assert empty["semantic_evals"] is None


@pytest.mark.asyncio
async def test_provider_usage_is_explicitly_not_configured(monkeypatch):
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.delenv("UNIGROK_XAI_TEAM_ID", raising=False)
    monkeypatch.setenv("UNIGROK_PROVIDER_USAGE", "auto")

    result = await fetch_provider_api_usage()

    assert result["state"] == "not_configured"
    assert result["usage_usd"] is None
    assert result["scope"] == "xai_api_team"


@pytest.mark.asyncio
async def test_save_telemetry_persists_usage_provenance(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "metrics.db")
    try:
        await store.save_telemetry(
            "intent",
            "CLI",
            1,
            1.2,
            0.0,
            model="grok-composer-2.5-fast",
            tokens=321,
            token_kind="local_estimate",
            billing_source="subscription_unmetered",
            routing={
                "v": 1,
                "route_class": "coding",
                "resolved_model": "grok-composer-2.5-fast",
                "why": "cost",
                "why_detail": "keyless_cli",
            },
        )
        row = (await store.get_telemetry_stats())[0]
        metadata = telemetry_metadata(row)
        assert metadata["model"] == "grok-composer-2.5-fast"
        assert metadata["tokens"] == 321
        assert metadata["token_kind"] == "local_estimate"
        assert metadata["billing_source"] == "subscription_unmetered"
        assert metadata["routing"]["why_detail"] == "keyless_cli"
    finally:
        await store.close()
