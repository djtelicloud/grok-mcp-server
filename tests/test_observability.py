# tests/test_observability.py
# Observability + storage interface: request-id correlation (contextvar,
# W3C traceparent at the gateway, X-Request-Id echo, MetaLayer/telemetry/job
# threading, log injection), UNIGROK_LOG_FORMAT=json structured logging,
# /metrics?format=prometheus text exposition, and the SessionStoreProtocol /
# get_store storage seam.

import json
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from src.http_server import (
    RequestIdMiddleware,
    _render_prometheus_metrics,
    _request_id_from_traceparent,
    create_app,
)
from src.jobs import JobManager
from src.storage import (
    SUPPORTED_STORAGE_BACKENDS,
    SessionStoreProtocol,
    get_store,
)
from src.utils import (
    GrokSessionStore,
    JsonLogFormatter,
    MetaLayer,
    RequestContextLogFilter,
    _PLAIN_LOG_FORMAT,
    _log_format_mode,
    get_request_id,
    new_request_id,
    normalize_request_id,
    request_id_scope,
    reset_request_id,
    run_agent_turn,
    set_request_id,
)


@pytest.fixture
async def ostore(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "observability.db")
    yield s
    await s.close()


def _log_record(msg="hello world", name="GrokMCP", level=logging.INFO, exc_info=None):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=exc_info,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Item 1 — request ids: contextvar plumbing
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestIdPrimitives:
    def test_new_request_id_is_short_uuid4_hex(self):
        rid = new_request_id()
        assert re.fullmatch(r"[0-9a-f]{12}", rid)
        assert rid != new_request_id()

    def test_normalize_request_id_strips_and_bounds(self):
        assert normalize_request_id("abc-DEF_1.2") == "abc-DEF_1.2"
        assert normalize_request_id("a\nb c\x00d\"e") == "abcde"
        assert normalize_request_id("x" * 200) == "x" * 64
        assert normalize_request_id(None) == ""
        assert normalize_request_id("") == ""

    def test_set_get_reset_roundtrip(self):
        token = set_request_id("trace-123")
        try:
            assert get_request_id() == "trace-123"
        finally:
            reset_request_id(token)
        assert get_request_id() == ""

    def test_scope_generates_and_resets(self):
        assert get_request_id() == ""
        with request_id_scope() as rid:
            assert rid
            assert get_request_id() == rid
        # Reset on exit: the next scope must not inherit the previous id.
        assert get_request_id() == ""
        with request_id_scope() as rid2:
            assert rid2 != rid

    def test_scope_respects_inherited_id(self):
        """An id bound by the transport (gateway traceparent) is respected —
        the scope neither replaces nor resets it."""
        token = set_request_id("inherited-id")
        try:
            with request_id_scope() as rid:
                assert rid == "inherited-id"
            assert get_request_id() == "inherited-id"
        finally:
            reset_request_id(token)


class TestAgentEntrypointRequestIds:
    @pytest.mark.asyncio
    async def test_run_agent_turn_stamps_metalayer_and_resets(self, monkeypatch):
        # side_effect (not return_value): each call must get a FRESH layer so
        # the second stamp is observable.
        mock_orchestrate = AsyncMock(side_effect=lambda **kw: MetaLayer(generation="ok"))
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        first = await run_agent_turn(prompt="hi")
        second = await run_agent_turn(prompt="hi again")

        assert re.fullmatch(r"[0-9a-f]{12}", first.request_id)
        # Per-call set/reset: sequential calls in one task get distinct ids
        # and nothing leaks into the ambient context afterwards.
        assert second.request_id != first.request_id
        assert get_request_id() == ""

    @pytest.mark.asyncio
    async def test_run_agent_turn_respects_inherited_id(self, monkeypatch):
        mock_orchestrate = AsyncMock(return_value=MetaLayer(generation="ok"))
        monkeypatch.setattr("src.utils.orchestrate", mock_orchestrate)

        token = set_request_id("4bf92f3577b34da6a3ce929d0e0e4736")
        try:
            layer = await run_agent_turn(prompt="hi")
        finally:
            reset_request_id(token)

        assert layer.request_id == "4bf92f3577b34da6a3ce929d0e0e4736"

    @pytest.mark.asyncio
    async def test_orchestrate_threads_id_into_telemetry_metadata(self, ostore, monkeypatch):
        """End-to-end through the REAL orchestrate/AgentLoop: the telemetry
        row's metadata envelope carries the same request id the MetaLayer
        reports."""
        monkeypatch.delenv("UNIGROK_FORCE_FAST", raising=False)
        from src.utils import orchestrate
        from evals.fakes import make_response

        resp = make_response(content="answer", cost_usd=0.001)
        mock_chat = MagicMock()
        mock_chat.sample.return_value = resp
        mock_client = MagicMock()
        mock_client.chat.create.return_value = mock_chat

        with patch("xai_sdk.Client", return_value=mock_client):
            layer = await orchestrate(
                prompt="fix the typo", mode="auto", store=ostore,
                dynamic_sys_prompt="sys", caller="claude-code",
            )

        assert re.fullmatch(r"[0-9a-f]{12}", layer.request_id)
        rows = await ostore.get_telemetry_stats()
        meta = json.loads(rows[0]["metadata"])
        assert meta["request_id"] == layer.request_id
        assert meta["caller"] == "claude-code"
        assert get_request_id() == ""


class TestTelemetryRequestId:
    @pytest.mark.asyncio
    async def test_explicit_param_wins(self, ostore):
        await ostore.save_telemetry("i", "API", 1, 0.1, 0.01, request_id="rid-explicit")
        rows = await ostore.get_telemetry_stats()
        assert json.loads(rows[0]["metadata"]) == {"request_id": "rid-explicit"}

    @pytest.mark.asyncio
    async def test_context_fallback(self, ostore):
        token = set_request_id("rid-ambient")
        try:
            await ostore.save_telemetry("i", "API", 1, 0.1, 0.01, caller="codex")
        finally:
            reset_request_id(token)
        rows = await ostore.get_telemetry_stats()
        assert json.loads(rows[0]["metadata"]) == {"caller": "codex", "request_id": "rid-ambient"}

    @pytest.mark.asyncio
    async def test_nothing_bound_keeps_metadata_null(self, ostore):
        await ostore.save_telemetry("i", "API", 1, 0.1, 0.01)
        rows = await ostore.get_telemetry_stats()
        assert rows[0]["metadata"] is None


class TestJobRequestId:
    @pytest.mark.asyncio
    async def test_v9_migration_adds_jobs_request_id(self, ostore):
        await ostore._ensure_initialized()
        async with ostore._conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row[0] >= 9
        async with ostore._conn.execute("PRAGMA table_info(jobs);") as cursor:
            jobs_cols = {r[1] for r in await cursor.fetchall()}
        assert "request_id" in jobs_cols

    @pytest.mark.asyncio
    async def test_job_row_records_bound_request_id(self, ostore, monkeypatch):
        monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=ostore)

        token = set_request_id("rid-job-1")
        try:
            view = await manager.submit("dig in", caller="codex-cli")
        finally:
            reset_request_id(token)
        await manager.wait(view["job_id"])

        row = await ostore.get_job(view["job_id"])
        assert row["request_id"] == "rid-job-1"
        assert JobManager.describe(row)["request_id"] == "rid-job-1"

    @pytest.mark.asyncio
    async def test_job_without_request_id_stays_none(self, ostore, monkeypatch):
        monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
        manager = JobManager(job_store=ostore)

        view = await manager.submit("dig in")
        await manager.wait(view["job_id"])

        row = await ostore.get_job(view["job_id"])
        assert row["request_id"] is None
        assert "request_id" not in JobManager.describe(row)


# ─────────────────────────────────────────────────────────────────────────────
# Item 1 — gateway: traceparent parsing and X-Request-Id echo
# ─────────────────────────────────────────────────────────────────────────────

class TestTraceparentParsing:
    def test_valid_traceparent_yields_trace_id(self):
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert _request_id_from_traceparent(header) == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_case_and_whitespace_tolerated(self):
        header = "  00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01  "
        assert _request_id_from_traceparent(header) == "4bf92f3577b34da6a3ce929d0e0e4736"

    @pytest.mark.parametrize("header", [
        None,
        "",
        "garbage",
        "00-shorttrace-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",  # missing flags
        "00-zzzz2f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # non-hex
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",  # all-zero invalid sentinel
    ])
    def test_malformed_traceparent_rejected(self, header):
        assert _request_id_from_traceparent(header) is None


class TestGatewayRequestIdEcho:
    def test_request_id_middleware_is_pure_asgi(self):
        from starlette.middleware.base import BaseHTTPMiddleware

        assert not issubclass(RequestIdMiddleware, BaseHTTPMiddleware)

    def test_all_endpoints_echo_x_request_id(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

        with TestClient(create_app()) as client:
            for path in ("/healthz", "/readyz", "/metrics", "/v1/models"):
                res = client.get(path)
                assert re.fullmatch(r"[0-9a-f]{12}", res.headers["x-request-id"]), path

    def test_auth_rejections_still_carry_request_id(self, monkeypatch):
        """The middleware is outermost: even a 401 is correlatable."""
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.setenv("UNIGROK_API_KEYS", "sekret-key")
        monkeypatch.delenv("UNIGROK_ALLOW_UNAUTHENTICATED", raising=False)

        with TestClient(create_app()) as client:
            res = client.get("/metrics")

        assert res.status_code == 401
        assert re.fullmatch(r"[0-9a-f]{12}", res.headers["x-request-id"])

    def test_traceparent_trace_id_becomes_request_id(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

        with TestClient(create_app()) as client:
            res = client.get(
                "/healthz",
                headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
            )

        assert res.headers["x-request-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_malformed_traceparent_generates_fresh_id(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)

        with TestClient(create_app()) as client:
            res = client.get("/healthz", headers={"traceparent": "not-a-traceparent"})

        assert re.fullmatch(r"[0-9a-f]{12}", res.headers["x-request-id"])

    def test_bound_id_visible_inside_agent_turn_and_response(self, monkeypatch):
        """The id the middleware binds is the one the agent turn sees (so
        telemetry/logs correlate) AND the one echoed on the response."""
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        import src.http_server as http_module

        seen = {}

        async def fake_run_agent_turn(**kwargs):
            seen["request_id"] = get_request_id()
            return MetaLayer(generation="ok", finish_reason="final_answer",
                             request_id=get_request_id())

        monkeypatch.setattr(http_module, "run_agent_turn", fake_run_agent_turn)
        with TestClient(create_app()) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"model": "unigrok-agent", "messages": [{"role": "user", "content": "hi"}]},
                headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
            )

        assert res.status_code == 200
        assert seen["request_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert res.headers["x-request-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        # The unigrok extension block reports the same correlation id.
        assert res.json()["unigrok"]["request_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


# ─────────────────────────────────────────────────────────────────────────────
# Item 2 — structured logs
# ─────────────────────────────────────────────────────────────────────────────

class TestLogFormatMode:
    def test_default_is_plain_locally(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_LOG_FORMAT", raising=False)
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        assert _log_format_mode() == "plain"

    def test_explicit_json(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_LOG_FORMAT", "JSON")
        assert _log_format_mode() == "json"

    def test_cloudrun_defaults_to_json(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_LOG_FORMAT", raising=False)
        monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
        assert _log_format_mode() == "json"

    def test_explicit_plain_wins_on_cloudrun(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_LOG_FORMAT", "plain")
        monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
        assert _log_format_mode() == "plain"

    def test_unknown_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_LOG_FORMAT", "yaml")
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        assert _log_format_mode() == "plain"


class TestJsonLogFormatter:
    def _format(self, record):
        RequestContextLogFilter().filter(record)
        return json.loads(JsonLogFormatter().format(record))

    def test_shape_with_request_id_and_caller(self):
        from src.utils import reset_active_caller, set_active_caller

        rid_token = set_request_id("rid-log-1")
        caller_token = set_active_caller("claude-code")
        try:
            payload = self._format(_log_record("agent turn done"))
        finally:
            reset_active_caller(caller_token)
            reset_request_id(rid_token)

        assert payload["level"] == "INFO"
        assert payload["logger"] == "GrokMCP"
        assert payload["msg"] == "agent turn done"
        assert payload["request_id"] == "rid-log-1"
        assert payload["caller"] == "claude-code"
        assert "T" in payload["ts"]  # ISO timestamp

    def test_unset_context_reads_empty_and_omits_caller(self):
        payload = self._format(_log_record("plain line"))
        assert payload["request_id"] == ""
        assert "caller" not in payload

    def test_secrets_are_redacted(self):
        payload = self._format(_log_record("key is xai-supersecret1234567890 ok"))
        assert "xai-supersecret1234567890" not in payload["msg"]
        assert "[REDACTED_KEY]" in payload["msg"]

    def test_exception_info_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _log_record("failed", level=logging.ERROR, exc_info=sys.exc_info())
        payload = self._format(record)
        assert payload["level"] == "ERROR"
        assert "boom" in payload["msg"]
        assert "Traceback" in payload["msg"]


class TestPlainFormatCompatibility:
    def test_line_unchanged_when_no_request_id(self):
        """rid_suffix renders as the empty string, so plain lines without a
        bound request id are byte-identical to the historical format."""
        formatter = logging.Formatter(_PLAIN_LOG_FORMAT)
        record = _log_record("hello world")
        RequestContextLogFilter().filter(record)
        line = formatter.format(record)
        assert line.endswith(" [INFO] GrokMCP: hello world")
        assert "[rid=" not in line

    def test_request_id_appended_when_bound(self):
        formatter = logging.Formatter(_PLAIN_LOG_FORMAT)
        record = _log_record("hello world")
        token = set_request_id("rid-plain-1")
        try:
            RequestContextLogFilter().filter(record)
        finally:
            reset_request_id(token)
        line = formatter.format(record)
        assert line.endswith(" [INFO] GrokMCP [rid=rid-plain-1]: hello world")


# ─────────────────────────────────────────────────────────────────────────────
# Item 3 — Prometheus exposition
# ─────────────────────────────────────────────────────────────────────────────

def _metrics_snapshot():
    return {
        "planes": {
            "API": {"requests": 42, "success_rate": 0.9762, "avg_latency_sec": 1.5,
                    "p95_latency_sec": 4.2, "total_cost_usd": 1.23},
            "CLI": {"requests": 3, "success_rate": 1.0, "avg_latency_sec": 0.4,
                    "p95_latency_sec": 0.5, "total_cost_usd": 0.0},
        },
        "callers": {
            'we"ird\\name\n': {"requests": 7, "success_rate": 1.0, "total_cost_usd": 0.5},
        },
        "runtime": {"timed_threads_in_flight": 2, "timed_threads_peak": 5},
        "circuit_breakers": {
            "grok-4.3": {"open": True, "consecutive_failures": 6,
                         "cooldown_remaining_sec": 12.0, "trips": 2},
        },
        "routing_advisor": {
            "planning_model": "grok-4.20-0309-reasoning",
            "coding_model": "grok-build-0.1",
            "planning": {"samples": 12, "success_rate": 0.75, "avg_cost": 0.01},
            "coding": {"samples": 30, "success_rate": 0.9, "avg_cost": 0.002},
            "borderline_choice": "planning",
        },
    }


class TestPrometheusRendering:
    def test_families_carry_help_and_type_lines(self):
        text = _render_prometheus_metrics(_metrics_snapshot())
        assert "# HELP unigrok_plane_requests_total " in text
        assert "# TYPE unigrok_plane_requests_total counter" in text
        assert "# TYPE unigrok_plane_success_rate gauge" in text
        assert 'unigrok_plane_requests_total{plane="API"} 42' in text
        assert 'unigrok_plane_success_rate{plane="API"} 0.9762' in text
        assert 'unigrok_plane_cost_usd_total{plane="CLI"} 0.0' in text
        assert text.endswith("\n")

    def test_caller_model_and_runtime_series(self):
        text = _render_prometheus_metrics(_metrics_snapshot())
        assert 'unigrok_caller_requests_total{caller="we\\"ird\\\\name\\n"} 7' in text
        assert "unigrok_timed_threads_in_flight 2" in text
        assert 'unigrok_circuit_breaker_open{model="grok-4.3"} 1' in text
        assert 'unigrok_circuit_breaker_trips_total{model="grok-4.3"} 2' in text
        assert 'unigrok_routing_model_success_rate{model="grok-build-0.1"} 0.9' in text
        assert "unigrok_routing_prefers_planning 1" in text

    def test_empty_snapshot_renders_without_error(self):
        text = _render_prometheus_metrics({})
        # No data families, no advisor — but valid exposition text.
        assert "unigrok_plane_requests_total{" not in text
        assert "unigrok_routing_prefers_planning" not in text

    def test_unverified_success_rate_series_is_omitted(self):
        snapshot = {
            "planes": {
                "API": {
                    "requests": 1,
                    "verified_outcomes": 0,
                    "unverified_requests": 1,
                    "success_rate": None,
                }
            },
            "callers": {
                "codex": {
                    "requests": 1,
                    "verified_outcomes": 0,
                    "unverified_requests": 1,
                    "success_rate": None,
                }
            },
        }

        text = _render_prometheus_metrics(snapshot)

        assert 'unigrok_plane_success_rate{plane="API"}' not in text
        assert 'unigrok_caller_success_rate{caller="codex"}' not in text
        assert 'unigrok_plane_unverified_requests_total{plane="API"} 1' in text
        assert 'unigrok_caller_unverified_requests_total{caller="codex"} 1' in text

    def test_metrics_endpoint_format_switch(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        import src.http_server as http_module

        rows = [
            {"chosen_plane": "API", "success": 1, "latency": 1.0, "cost": 0.01,
             "metadata": '{"caller":"claude-code"}'},
        ]
        monkeypatch.setattr(
            http_module.store, "get_telemetry_stats", AsyncMock(return_value=rows)
        )

        with TestClient(
            create_app(),
            base_url="http://localhost:8080",
            client=("127.0.0.1", 50000),
        ) as client:
            prom = client.get("/metrics?format=prometheus")
            default = client.get("/metrics")

        assert prom.status_code == 200
        assert prom.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in prom.headers["content-type"]
        assert 'unigrok_plane_requests_total{plane="API"} 1' in prom.text
        assert 'unigrok_caller_requests_total{caller="claude-code"} 1' in prom.text
        # JSON stays the default shape.
        assert default.json()["format"] == "unigrok-json-v1"



def _task_rag_view():
    return {
        "mode": "shadow",
        "collection": "unigrok-task-memories-v2",
        "ready": True,
        "unsynced": 4,
        "fused_score_bucket_bounds": [0.2, 0.4, 0.6, 0.8, 1.0],
        "queries": 11, "cache_hits": 5, "remote_calls": 6, "remote_failures": 1,
        "rate_limited": 0, "timeouts": 1, "uploads": 9, "upload_failures": 2,
        "shadow_flips": 3, "applied_flips": 0,
        "fused_score_buckets": [1, 2, 0, 1, 0, 1],
        "fused_score_sum": 2.5,
        "fused_score_count": 5,
    }


class TestTaskRagPrometheusRendering:
    def test_families_and_histogram_render(self):
        snapshot = _metrics_snapshot()
        snapshot["routing_advisor"]["task_rag"] = _task_rag_view()
        text = _render_prometheus_metrics(snapshot)
        assert "unigrok_task_rag_ready 1" in text
        assert "unigrok_task_rag_unsynced_rows 4" in text
        assert "unigrok_task_rag_remote_calls_total 6" in text
        assert "unigrok_task_rag_remote_failures_total 1" in text
        assert "unigrok_task_rag_shadow_flips_total 3" in text
        assert "unigrok_task_rag_applied_flips_total 0" in text
        # Histogram: cumulative le buckets + sum + count, fixed cardinality.
        assert "# TYPE unigrok_task_rag_fused_score histogram" in text
        assert 'unigrok_task_rag_fused_score_bucket{le="0.2"} 1' in text
        assert 'unigrok_task_rag_fused_score_bucket{le="0.4"} 3' in text
        assert 'unigrok_task_rag_fused_score_bucket{le="1.0"} 4' in text
        assert 'unigrok_task_rag_fused_score_bucket{le="+Inf"} 5' in text
        assert "unigrok_task_rag_fused_score_sum 2.5" in text
        assert "unigrok_task_rag_fused_score_count 5" in text

    def test_unknown_ready_and_unsynced_emit_no_series(self):
        """None means "not probed yet"/"store unavailable" — the family() empty-
        series skip keeps those out of the exposition instead of lying with 0."""
        view = _task_rag_view()
        view["ready"] = None
        view["unsynced"] = None
        snapshot = _metrics_snapshot()
        snapshot["routing_advisor"]["task_rag"] = view
        text = _render_prometheus_metrics(snapshot)
        assert "unigrok_task_rag_ready" not in text
        assert "unigrok_task_rag_unsynced_rows" not in text
        assert "unigrok_task_rag_remote_calls_total 6" in text

    def test_absent_task_rag_renders_no_families(self):
        text = _render_prometheus_metrics(_metrics_snapshot())
        assert "unigrok_task_rag_" not in text

    def test_metrics_json_exposes_task_rag_mode(self, monkeypatch):
        monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
        monkeypatch.delenv("UNIGROK_API_KEYS", raising=False)
        monkeypatch.delenv("UNIGROK_TASK_RAG", raising=False)
        import src.http_server as http_module

        monkeypatch.setattr(
            http_module.store, "get_telemetry_stats", AsyncMock(return_value=[])
        )
        with TestClient(
            create_app(),
            base_url="http://localhost:8080",
            client=("127.0.0.1", 50000),
        ) as client:
            payload = client.get("/metrics").json()
        task_rag = payload["routing_advisor"]["task_rag"]
        assert task_rag["mode"] == "off"
        assert task_rag["collection"] == "unigrok-task-memories-v2"


# ─────────────────────────────────────────────────────────────────────────────
# Item 4 — storage interface
# ─────────────────────────────────────────────────────────────────────────────

# Every member of the protocol, enumerated so the conformance check below
# fails loudly (naming the member) instead of relying on isinstance alone.
_PROTOCOL_MEMBERS = (
    "close", "vacuum_db",
    "save_telemetry", "get_telemetry_stats", "attach_semantic_scores",
    "get_semantic_judge_cost_today", "get_caller_cost_today",
    "get_caller_stats_today", "get_recent_model_stats",
    "upsert_routing_calibration", "get_routing_calibration",
    "save_task_memory", "get_similar_task_memories", "get_task_memory_count",
    "list_unsynced_task_memories", "mark_task_memory_synced",
    "mark_task_memory_sync_failed", "get_task_memories_by_remote_ids",
    "count_unsynced_task_memories", "reset_task_memory_sync",
    "begin_provider_attempt", "complete_provider_attempt",
    "complete_projected_provider_attempt",
    "revoke_provider_attempt_projection",
    "mark_stale_provider_attempts_indeterminate", "list_provider_attempts",
    "lease_provider_attempts_for_harvest",
    "provider_attempt_harvest_lease_is_fresh",
    "mark_provider_attempt_harvest_synced", "mark_provider_attempt_harvest_retry",
    "save_workspace_evidence", "get_workspace_evidence",
    "list_workspace_evidence", "list_unsynced_workspace_evidence",
    "mark_workspace_evidence_synced", "mark_workspace_evidence_sync_failed",
    "count_workspace_evidence", "count_unsynced_workspace_evidence",
    "save_fact", "search_facts", "touch_facts", "delete_fact", "count_facts",
    "list_facts",
    "get_session", "save_session", "delete_session", "list_sessions",
    "save_message", "replace_messages", "load_messages",
    "create_job", "update_job", "get_job", "list_jobs",
    "create_swarm_task", "update_swarm_task", "get_swarm_task",
    "list_swarm_tasks", "insert_swarm_candidate", "list_swarm_candidates",
)


class TestSessionStoreProtocol:
    def test_grok_session_store_conforms_structurally(self, tmp_path):
        store = GrokSessionStore(db_path=tmp_path / "conform.db")
        assert isinstance(store, SessionStoreProtocol)

    def test_every_protocol_member_is_a_coroutine_method(self, tmp_path):
        import inspect

        store = GrokSessionStore(db_path=tmp_path / "conform.db")
        for member in _PROTOCOL_MEMBERS:
            attr = getattr(store, member, None)
            assert callable(attr), f"GrokSessionStore missing {member}"
            assert inspect.iscoroutinefunction(attr), f"{member} must be async"

    def test_protocol_covers_the_full_public_async_surface(self):
        """The protocol must not silently drift behind GrokSessionStore: any
        NEW public async method on the store has to be added to
        SessionStoreProtocol (and _PROTOCOL_MEMBERS here)."""
        import inspect

        public_async = {
            name
            for name, member in inspect.getmembers(
                GrokSessionStore, predicate=inspect.iscoroutinefunction
            )
            if not name.startswith("_")
        }
        assert public_async == set(_PROTOCOL_MEMBERS)


class TestGetStoreFactory:
    def test_default_backend_is_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("UNIGROK_STORAGE_BACKEND", raising=False)
        store = get_store(tmp_path / "factory.db")
        assert isinstance(store, GrokSessionStore)
        assert store.db_path == tmp_path / "factory.db"

    def test_explicit_sqlite_accepted_case_insensitively(self, monkeypatch, tmp_path):
        monkeypatch.setenv("UNIGROK_STORAGE_BACKEND", " SQLite ")
        assert isinstance(get_store(tmp_path / "x.db"), GrokSessionStore)

    def test_unknown_backend_fails_fast_naming_supported_set(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_STORAGE_BACKEND", "postgres")
        with pytest.raises(NotImplementedError, match=r"postgres.*sqlite"):
            get_store()
        assert SUPPORTED_STORAGE_BACKENDS == ("sqlite",)

    def test_global_singleton_went_through_the_factory(self):
        """The utils store singleton is a protocol-conforming sqlite store —
        the factory is its only construction path."""
        import src.utils as utils_module

        assert isinstance(utils_module.store, GrokSessionStore)
        assert isinstance(utils_module.store, SessionStoreProtocol)
