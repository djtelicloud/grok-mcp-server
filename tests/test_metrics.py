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
            metadata='{"model":"grok-4.5","tokens":120,"token_kind":"provider_exact","caller":"codex"}',
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
    assert today["planes"]["API"]["exact_token_requests"] == 1
    assert today["planes"]["CLI"]["estimated_token_requests"] == 1
    assert snapshot["usage"]["cli_subscription"]["cost_per_request_usd"] is None
    assert snapshot["usage"]["cli_subscription"]["provider_usage_available"] is False
    assert snapshot["planes"]["CLI"]["total_cost_usd"] == 0.0


def test_structured_metrics_empty_period_is_null_not_fake_zero():
    now = datetime(2026, 7, 10, 12, 0, 0)
    snapshot = build_metrics_snapshot([], now=now)
    summary = snapshot["usage"]["today"]["summary"]

    assert summary["requests"] == 0
    assert summary["success_rate"] is None
    assert summary["avg_latency_sec"] is None
    assert summary["p95_latency_sec"] is None
    assert snapshot["usage"]["today"]["planes"] == {}


def test_telemetry_metadata_tolerates_old_and_malformed_rows():
    assert telemetry_metadata({"metadata": None}) == {}
    assert telemetry_metadata({"metadata": "not-json"}) == {}
    assert telemetry_metadata({"metadata": {"model": "grok"}}) == {"model": "grok"}


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
        )
        row = (await store.get_telemetry_stats())[0]
        metadata = telemetry_metadata(row)
        assert metadata["model"] == "grok-composer-2.5-fast"
        assert metadata["tokens"] == 321
        assert metadata["token_kind"] == "local_estimate"
        assert metadata["billing_source"] == "subscription_unmetered"
    finally:
        await store.close()
