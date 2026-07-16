import json
from datetime import datetime, timedelta

import pytest

from src.metrics import build_metrics_snapshot, fetch_provider_api_usage, telemetry_metadata
from src.utils import GrokSessionStore


def _row(
    *, created_at, plane="API", success=1, latency=1.0, cost=0.0,
    metadata=None, intent=None,
):
    return {
        "created_at": created_at.isoformat(),
        "intent": intent,
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

    assert snapshot["schema_version"] == 3
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
        "claude": {
            "requests": 1, "verified_outcomes": 1, "unverified_requests": 0,
            "success_rate": 1.0, "total_cost_usd": 0.0,
        },
        "codex": {
            "requests": 1, "verified_outcomes": 1, "unverified_requests": 0,
            "success_rate": 1.0, "total_cost_usd": pytest.approx(0.012),
        },
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


def test_unverified_rows_preserve_usage_without_fabricating_failure():
    now = datetime(2026, 7, 10, 12, 0, 0)
    rows = [
        _row(created_at=now, success=None, cost=0.02, metadata='{"caller":"codex"}'),
        _row(created_at=now, success=0, cost=0.01, metadata='{"caller":"codex"}'),
        _row(
            created_at=now,
            success=1,
            cost=0.50,
            metadata='{"caller":"codex"}',
            intent="history-compaction",
        ),
    ]

    snapshot = build_metrics_snapshot(rows, now=now)
    summary = snapshot["usage"]["today"]["summary"]
    caller = snapshot["usage"]["today"]["callers"]["codex"]

    assert summary["requests"] == 2
    assert summary["verified_outcomes"] == 1
    assert summary["unverified_requests"] == 1
    assert summary["success_rate"] == 0.0
    assert summary["api_cost_usd"] == pytest.approx(0.53)
    assert caller["requests"] == 2
    assert caller["verified_outcomes"] == 1
    assert caller["unverified_requests"] == 1
    assert caller["success_rate"] == 0.0
    assert caller["total_cost_usd"] == pytest.approx(0.53)
    assert snapshot["usage"]["today"]["recent_routes"] == []


def test_all_unverified_rows_omit_success_rate():
    now = datetime(2026, 7, 10, 12, 0, 0)
    rows = [
        _row(
            created_at=now,
            success=None,
            metadata='{"caller":"codex","routing":{"resolved_model":"grok-4.5"}}',
        )
    ]

    snapshot = build_metrics_snapshot(rows, now=now)

    assert snapshot["planes"]["API"]["success_rate"] is None
    assert snapshot["callers"]["codex"]["success_rate"] is None
    assert snapshot["usage"]["today"]["recent_routes"][0]["success"] is None


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
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("UNIGROK_XAI_TEAM_ID", raising=False)
    monkeypatch.setenv("UNIGROK_PROVIDER_USAGE", "auto")

    result = await fetch_provider_api_usage()

    assert result["state"] == "not_configured"
    assert result["usage_usd"] is None
    assert result["scope"] == "xai_api_team"


@pytest.mark.asyncio
async def test_provider_usage_accepts_sdk_management_alias(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"timeSeries": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    def capture_client(**kwargs):
        captured["client_kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr("src.metrics.httpx.AsyncClient", capture_client)
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "sdk-management-test-key")
    monkeypatch.setenv("UNIGROK_XAI_TEAM_ID", "team-test")
    monkeypatch.setenv("UNIGROK_PROVIDER_USAGE", "auto")

    result = await fetch_provider_api_usage()

    assert result["state"] == "ready"
    assert captured["headers"] == {
        "Authorization": "Bearer sdk-management-test-key"
    }
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_provider_usage_rejects_conflicting_management_aliases(monkeypatch):
    def forbidden_client(**kwargs):
        raise AssertionError("conflicting management authority must not make a call")

    monkeypatch.setattr("src.metrics.httpx.AsyncClient", forbidden_client)
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "canonical-test-key")
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "different-sdk-test-key")
    monkeypatch.setenv("UNIGROK_XAI_TEAM_ID", "team-test")
    monkeypatch.setenv("UNIGROK_PROVIDER_USAGE", "auto")

    result = await fetch_provider_api_usage()

    assert result["state"] == "error"
    assert result["usage_usd"] is None
    assert "conflict" in result["detail"].lower()


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


@pytest.mark.asyncio
async def test_save_telemetry_redacts_secrets_in_intent(tmp_path):
    """Prompt prefixes must not land at-rest with live credentials."""
    store = GrokSessionStore(db_path=tmp_path / "intent-redact.db")
    try:
        secret = "xai-1234567890abcdef"
        await store.save_telemetry(
            f"deploy with {secret} please",
            "API",
            1,
            0.5,
            0.001,
        )
        await store.save_telemetry("history-compaction", "API", 1, 0.1, 0.0)
        rows = {row["intent"]: row for row in await store.get_telemetry_stats()}
        leaked = next(
            intent for intent in rows if "deploy with" in intent or "REDACTED" in intent
        )
        assert secret not in leaked
        assert "[REDACTED_KEY]" in leaked
        assert "history-compaction" in rows
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_save_telemetry_persists_partial_cross_plane_usage(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "partial-usage.db")
    try:
        await store.save_telemetry(
            "intent",
            "CLI-Fallback",
            0,
            1.2,
            0.001,
            model="grok-composer-2.5-fast",
            tokens=7,
            token_kind="partial",
            billing_source="partial",
        )
        row = (await store.get_telemetry_stats())[0]
        metadata = telemetry_metadata(row)
        assert metadata["tokens"] == 7
        assert metadata["token_kind"] == "partial"
        assert metadata["billing_source"] == "partial"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_save_telemetry_bounds_routing_by_persisted_compact_json(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "routing-bound.db")
    try:
        empty_size = len(json.dumps({"payload": ""}, separators=(",", ":")))
        bounded = {"payload": "x" * (6000 - empty_size)}
        oversized = {"payload": bounded["payload"] + "x"}

        assert len(json.dumps(bounded, separators=(",", ":"))) == 6000
        assert len(json.dumps(bounded)) > 6000

        await store.save_telemetry("bounded", "API", 1, 0.1, 0.0, routing=bounded)
        with pytest.raises(ValueError, match="core exceeds"):
            await store.save_telemetry(
                "oversized", "API", 1, 0.1, 0.0, routing=oversized
            )

        rows = {row["intent"]: telemetry_metadata(row) for row in await store.get_telemetry_stats()}
        assert rows["bounded"]["routing"] == bounded
        assert "oversized" not in rows
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_telemetry_attempt_split_round_trips_exact_receipt_and_detects_tamper(
    tmp_path,
):
    store = GrokSessionStore(db_path=tmp_path / "routing-attempts.db")
    attempts = [
        {
            "provider": "xai",
            "phase": "execution",
            "attempt": index,
            "plane": "API" if index % 2 else "CLI",
            "model": "shared-live-slug",
            "purpose": "reflection",
            "outcome": "error" if index < 90 else "completed",
            "error": "bounded failure evidence " + ("x" * 80),
        }
        for index in range(1, 101)
    ]
    receipt = {
        "v": 1,
        "provider": "xai",
        "authority": "grok",
        "requested_plane": "auto",
        "resolved_plane": "API",
        "attempts": attempts,
    }
    try:
        await store.save_telemetry(
            "many-attempts", "API", 0, 1.0, 0.1, routing=receipt
        )
        row = (await store.get_telemetry_stats())[0]
        assert telemetry_metadata(row)["routing"] == receipt

        async with store._conn.execute(
            "SELECT metadata FROM telemetry WHERE id = ?", (row["id"],)
        ) as cursor:
            raw_parent = (await cursor.fetchone())[0]
        assert len(raw_parent) < 7500
        async with store._conn.execute(
            "SELECT COUNT(*), MAX(length(attempt_json)) FROM telemetry_attempts"
        ) as cursor:
            count, max_chars = await cursor.fetchone()
        assert count == len(attempts)
        assert max_chars <= 4096

        await store._conn.execute(
            "UPDATE telemetry_attempts SET attempt_json = ? "
            "WHERE telemetry_id = ? AND attempt_ordinal = 1",
            (json.dumps({"tampered": True}), row["id"]),
        )
        await store._conn.commit()
        with pytest.raises(RuntimeError, match="digest mismatch"):
            await store.get_telemetry_stats()
    finally:
        await store.close()
