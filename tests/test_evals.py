"""
tests/test_evals.py

The self-feeding eval harness (evals/) plus its closed calibration loop:
  - grader logic (contains / not_contains / regex / structural + $aliases)
  - offline cassette replay through the REAL run_agent_turn stack —
    deterministic across runs, and the shipped seed suite passes
  - export-session: a fixture store session becomes a replayable golden task
  - routing_calibration: v6 migration, upsert/read TTL filter, and
    RoutingAdvisor precedence over raw telemetry (with TTL expiry + n gates)
  - the UNIGROK_EVAL_RECORD tap: off by default, JSONL events when on
  - live-tier batch gating: introspection + the >=4 all-fast decision rule
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("XAI_API_KEY", "xai-test-dummy-key-for-unit-tests")

from evals.cassettes import export_session, load_cassettes, stable_substrings
from evals.fakes import FakeChat, FakeClient, make_response
from evals.graders import run_graders
from evals.runner import (
    EvalTask,
    aggregate_calibration,
    batch_mode_decision,
    batch_service_usable,
    build_report,
    check_baseline,
    load_tasks,
    markdown_summary,
    run_offline,
    write_calibration,
)
from src.utils import (
    DEFAULT_CODING_MODEL,
    DEFAULT_PLANNING_MODEL,
    GrokSessionStore,
    RoutingAdvisor,
    _EvalRecordingClient,
    get_xai_client,
)


def _task(**overrides):
    base = {
        "id": "t1",
        "category": "coding",
        "prompt": "do the thing",
        "graders": [{"type": "contains", "value": "ok"}],
    }
    base.update(overrides)
    return EvalTask.from_dict(base)


# ─────────────────────────────────────────────────────────────────────────────
# Graders
# ─────────────────────────────────────────────────────────────────────────────

class TestGraders:
    def test_contains_case_insensitive_by_default(self):
        out = run_graders([{"type": "contains", "value": "cloud run"}], "Deploys to Cloud Run.", {})
        assert out[0]["passed"] is True

    def test_contains_case_sensitive_opt_in(self):
        out = run_graders(
            [{"type": "contains", "value": "cloud run", "case_sensitive": True}],
            "Deploys to Cloud Run.", {},
        )
        assert out[0]["passed"] is False

    def test_not_contains(self):
        graders = [{"type": "not_contains", "value": "as an AI"}]
        assert run_graders(graders, "here is the answer", {})[0]["passed"] is True
        assert run_graders(graders, "As an AI I cannot", {})[0]["passed"] is False

    def test_regex(self):
        graders = [{"type": "regex", "pattern": r"\b\d+\.\d+\.\d+\b"}]
        assert run_graders(graders, "matches 1.2.3 fine", {})[0]["passed"] is True
        assert run_graders(graders, "matches 1.2 only", {})[0]["passed"] is False

    def test_regex_invalid_pattern_fails_gracefully(self):
        out = run_graders([{"type": "regex", "pattern": "("}], "anything", {})
        assert out[0]["passed"] is False
        assert "invalid pattern" in out[0]["detail"]

    def test_structural_equals_with_bool_normalization(self):
        result = {"escalated": True, "route": "agentic"}
        assert run_graders(
            [{"type": "structural", "field": "escalated", "equals": True}], "", result
        )[0]["passed"] is True
        # JSON-authored "true" strings compare as booleans too.
        assert run_graders(
            [{"type": "structural", "field": "escalated", "equals": "true"}], "", result
        )[0]["passed"] is True
        assert run_graders(
            [{"type": "structural", "field": "route", "equals": "fast"}], "", result
        )[0]["passed"] is False

    def test_structural_gte_lte(self):
        result = {"citations_count": 2}
        assert run_graders(
            [{"type": "structural", "field": "citations_count", "gte": 1}], "", result
        )[0]["passed"] is True
        assert run_graders(
            [{"type": "structural", "field": "citations_count", "lte": 1}], "", result
        )[0]["passed"] is False

    def test_structural_model_alias_resolution(self):
        """$planning/$coding resolve against the result's resolved slugs so
        task JSON never hardcodes model names."""
        result = {"model": "grok-4.3", "planning_model": "grok-4.3", "coding_model": "grok-build-0.1"}
        assert run_graders(
            [{"type": "structural", "field": "model", "equals": "$planning"}], "", result
        )[0]["passed"] is True
        assert run_graders(
            [{"type": "structural", "field": "model", "equals": "$coding"}], "", result
        )[0]["passed"] is False

    def test_unknown_grader_type_fails(self):
        out = run_graders([{"type": "llm_judge"}], "answer", {})
        assert out[0]["passed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Offline cassette replay — determinism + shipped seed suite
# ─────────────────────────────────────────────────────────────────────────────

class TestOfflineReplay:
    @pytest.mark.asyncio
    async def test_seed_suite_passes_and_is_deterministic(self):
        """The shipped 12-task seed suite replays green through the real
        orchestrate stack, twice, with identical outcomes — the determinism
        contract CI's baseline gate relies on."""
        tasks = load_tasks()
        cassettes = load_cassettes()
        assert len(tasks) >= 12

        first = await run_offline(tasks, cassettes)
        second = await run_offline(tasks, cassettes)

        def _fingerprint(results):
            return [
                (r["task_id"], r["passed"], r["route"], r["model"],
                 r["escalated"], r["finish_reason"], r["answer_excerpt"])
                for r in results
            ]

        assert _fingerprint(first) == _fingerprint(second)
        failed = [r["task_id"] for r in first if not r["passed"]]
        assert not failed, f"seed tasks failed offline: {failed}"

    @pytest.mark.asyncio
    async def test_seed_suite_covers_the_designed_scenarios(self):
        results = {r["task_id"]: r for r in await run_offline(load_tasks(), load_cassettes())}

        # Borderline prompt statically routes to the coding model under the
        # hermetic advisor bypass.
        assert results["borderline_product_summary"]["model"] == DEFAULT_CODING_MODEL
        # The should-escalate task really escalated while keeping the routed
        # coding slug (escalated flags the upgrade, matching task memory).
        esc = results["escalation_flaky_test"]
        assert esc["escalated"] is True
        assert esc["model"] == DEFAULT_CODING_MODEL
        assert esc["final_model"] == DEFAULT_PLANNING_MODEL
        # Thinking route ran reviewer-driven retry to the corrected answer.
        think = results["thinking_trap_average_speed"]
        assert think["route"] == "thinking"
        assert "40 km/h" in think["answer_excerpt"]
        # Memory tasks replayed session history into the chat before sampling.
        assert results["memory_recall_deploy_target"]["appends_before_first_sample"] >= 4
        # Research surfaces citations.
        assert results["research_webgpu_adoption"]["citations_count"] >= 1
        # Fast mode takes the toolless plane.
        assert results["fastpath_simple_math"]["route"] == "fast"
        # Multi-file replay dispatched real file/test tools and only passes
        # when the pytest observation itself reports a green exit status.
        multifile = results["agent_multifile"]
        assert multifile["tool_calls_count"] == 4
        assert multifile["tool_failures_count"] == 0
        assert multifile["project_file_lists_count"] == 1
        assert multifile["project_file_list_succeeded"] is True
        assert multifile["local_reads_count"] == 2
        assert multifile["local_reads_succeeded"] == 2
        assert multifile["local_test_runs_count"] == 1
        assert multifile["local_tests_passed"] is True
        assert "21 seconds" in multifile["answer_excerpt"]

    @pytest.mark.asyncio
    async def test_missing_cassette_is_an_explicit_failure(self):
        task = _task(id="uncassetted")
        results = await run_offline([task], {})
        assert results[0]["passed"] is False
        assert "no cassette" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_max_cost_usd_gate_fails_expensive_runs(self):
        task = _task(
            id="pricey",
            graders=[{"type": "contains", "value": "ok"}],
            max_cost_usd=0.001,
        )
        cassettes = {"pricey": {"responses": [{"content": "ok", "cost_usd": 0.5}]}}
        results = await run_offline([task], cassettes)
        assert results[0]["passed"] is False
        assert any(g["type"] == "max_cost_usd" and not g["passed"] for g in results[0]["graders"])

    def test_task_validation_rejects_bad_specs(self):
        with pytest.raises(ValueError, match="category"):
            EvalTask.from_dict({"id": "x", "category": "vibes", "prompt": "p",
                                "graders": [{"type": "contains", "value": "a"}]})
        with pytest.raises(ValueError, match="mode"):
            EvalTask.from_dict({"id": "x", "category": "coding", "prompt": "p", "mode": "warp",
                                "graders": [{"type": "contains", "value": "a"}]})
        with pytest.raises(ValueError, match="plane"):
            EvalTask.from_dict({"id": "x", "category": "coding", "prompt": "p", "plane": "api_first",
                                "graders": [{"type": "contains", "value": "a"}]})
        with pytest.raises(ValueError, match="grader"):
            EvalTask.from_dict({"id": "x", "category": "coding", "prompt": "p"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["tests/test_failure.py", "tests/missing.py"])
    async def test_scripted_claim_cannot_override_failed_local_tests(
        self, tmp_path, monkeypatch, target
    ):
        """A cassette saying 'passed' is not proof when real pytest failed."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_failure.py").write_text(
            "def test_failure():\n"
            "    print('Local tests passed for `fake` (exit code 0, timeout 10s).')\n"
            "    assert False\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr("src.tools.system.shutil.which", lambda _name: None)
        task = _task(
            id="false-positive",
            graders=[
                {"type": "contains", "value": "tests passed"},
                {
                    "type": "structural",
                    "field": "local_tests_passed",
                    "equals": True,
                },
            ],
        )
        cassettes = {
            "false-positive": {
                "responses": [
                    {
                        "content": "checking",
                        "tool_calls": [
                            {
                                "id": "call-negative-test",
                                "name": "run_local_tests",
                                "arguments": {"target": target, "max_seconds": 10},
                            }
                        ],
                    },
                    {"content": "The tests passed."},
                ]
            }
        }
        result = (await run_offline([task], cassettes))[0]
        assert result["local_test_runs_count"] == 1
        assert result["local_tests_passed"] is False
        assert result["passed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Report + baseline
# ─────────────────────────────────────────────────────────────────────────────

class TestReportAndBaseline:
    def _results(self):
        return [
            {"task_id": "a", "category": "coding", "passed": True, "cost_usd": 0.001,
             "route": "agentic", "model": "m1", "graders": []},
            {"task_id": "b", "category": "coding", "passed": False, "cost_usd": 0.002,
             "route": "agentic", "model": "m1", "graders": [], "error": None},
        ]

    def test_build_report_totals_and_markdown(self):
        report = build_report(self._results(), run_mode="offline")
        assert report["totals"] == {
            "tasks": 2, "passed": 1, "failed": 1, "pass_rate": 0.5,
            "total_cost_usd": 0.003,
        }
        summary = markdown_summary(report)
        assert "1/2 passed" in summary
        assert "| a | coding |" in summary

    def test_check_baseline_flags_failures_and_missing(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"expected_pass": ["a", "b", "ghost"]}))
        regressions = check_baseline(self._results(), baseline)
        # a passed; b failed; ghost never ran — both count as regressions.
        assert regressions == ["b", "ghost"]

    def test_check_baseline_clean(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"expected_pass": ["a"]}))
        assert check_baseline(self._results(), baseline) == []


# ─────────────────────────────────────────────────────────────────────────────
# export-session — a stored session becomes a golden task
# ─────────────────────────────────────────────────────────────────────────────

class TestExportSession:
    @pytest.mark.asyncio
    async def test_export_then_replay_roundtrip(self, tmp_path):
        store = GrokSessionStore(db_path=tmp_path / "fixture.db")
        try:
            await store.replace_messages("real-session", [
                {"role": "user", "content": "How do I enable WAL mode in SQLite?"},
                {"role": "assistant",
                 "content": "Run PRAGMA journal_mode=WAL; once per database. "
                            "WAL mode persists in the file, so later connections inherit it.",
                 "metadata": {"tokens": 40, "cost": 0.002}},
            ])
            exported = await export_session(
                store, "real-session",
                tasks_dir=tmp_path / "tasks", cassettes_dir=tmp_path / "cassettes",
                category="coding",
            )
        finally:
            await store.close()

        task_file = json.loads(Path(exported["task_path"]).read_text())
        assert task_file["prompt"] == "How do I enable WAL mode in SQLite?"
        assert any(g["type"] == "contains" for g in task_file["graders"])

        # The exported artifacts replay green in one command — a real session
        # became a regression test.
        tasks = load_tasks(tmp_path / "tasks")
        cassettes = load_cassettes(tmp_path / "cassettes")
        results = await run_offline(tasks, cassettes)
        assert results[0]["task_id"] == exported["task_id"]
        assert results[0]["passed"] is True
        # Derived expected-contains really are substrings of the stored answer.
        for grader in task_file["graders"]:
            if grader["type"] == "contains":
                assert grader["value"] in cassettes[exported["task_id"]]["responses"][0]["content"]

    @pytest.mark.asyncio
    async def test_export_rejects_empty_or_userless_sessions(self, tmp_path):
        store = GrokSessionStore(db_path=tmp_path / "fixture2.db")
        try:
            with pytest.raises(ValueError, match="no stored messages"):
                await export_session(store, "ghost", tmp_path / "t", tmp_path / "c")
            await store.replace_messages("assistant-only", [
                {"role": "assistant", "content": "hello there, unprompted"},
            ])
            with pytest.raises(ValueError, match="no user turn"):
                await export_session(store, "assistant-only", tmp_path / "t", tmp_path / "c")
        finally:
            await store.close()

    def test_stable_substrings_prefers_long_fragments(self):
        text = "Ok. The definitive fix is to reset the connection pool between tests. Done."
        picks = stable_substrings(text)
        assert picks
        assert all(pick in text for pick in picks)
        assert "Ok." not in picks  # too short to be a stable discriminator


# ─────────────────────────────────────────────────────────────────────────────
# routing_calibration: migration, store round-trip, TTL filter
# ─────────────────────────────────────────────────────────────────────────────

class TestCalibrationStore:
    @pytest.mark.asyncio
    async def test_v6_migration_and_upsert_roundtrip(self, tmp_path):
        s = GrokSessionStore(db_path=tmp_path / "cal.db")
        try:
            await s._ensure_initialized()
            async with s._conn.execute("PRAGMA user_version;") as cursor:
                row = await cursor.fetchone()
                assert row[0] >= 6

            await s.upsert_routing_calibration(
                category="coding", route="agentic", model="grok-build-0.1",
                success_rate=0.8, avg_cost_usd=0.002, n=10,
            )
            # Second upsert for the same key REPLACES, never duplicates.
            await s.upsert_routing_calibration(
                category="coding", route="agentic", model="grok-build-0.1",
                success_rate=0.9, avg_cost_usd=0.003, n=12,
            )
            rows = await s.get_routing_calibration()
            assert len(rows) == 1
            assert rows[0]["success_rate"] == pytest.approx(0.9)
            assert rows[0]["n"] == 12
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_max_age_filter_excludes_stale_rows(self, tmp_path):
        s = GrokSessionStore(db_path=tmp_path / "cal_ttl.db")
        try:
            await s.upsert_routing_calibration(
                category="coding", route="agentic", model="fresh-model",
                success_rate=1.0, avg_cost_usd=0.001, n=6,
            )
            await s.upsert_routing_calibration(
                category="coding", route="agentic", model="stale-model",
                success_rate=1.0, avg_cost_usd=0.001, n=6,
            )
            stale_ts = (datetime.now() - timedelta(hours=400)).isoformat()
            async with s._lock:
                await s._conn.execute(
                    "UPDATE routing_calibration SET updated_at = ? WHERE model = 'stale-model'",
                    (stale_ts,),
                )
                await s._conn.commit()

            fresh = await s.get_routing_calibration(max_age_hours=168)
            assert [r["model"] for r in fresh] == ["fresh-model"]
            everything = await s.get_routing_calibration()
            assert len(everything) == 2
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_write_calibration_aggregates_by_category_route_model(self, tmp_path):
        results = [
            {"task_id": "a", "category": "coding", "route": "agentic", "model": "m1",
             "passed": True, "cost_usd": 0.002},
            {"task_id": "b", "category": "coding", "route": "agentic", "model": "m1",
             "passed": False, "cost_usd": 0.004},
            {"task_id": "c", "category": "reasoning", "route": "thinking", "model": "m2",
             "passed": True, "cost_usd": 0.01},
            # No route/model (harness error before routing) → no routing signal.
            {"task_id": "d", "category": "coding", "route": None, "model": None,
             "passed": False, "cost_usd": 0.0},
        ]
        assert aggregate_calibration(results) == [
            {"category": "coding", "route": "agentic", "model": "m1",
             "success_rate": 0.5, "avg_cost_usd": 0.003, "n": 2},
            {"category": "reasoning", "route": "thinking", "model": "m2",
             "success_rate": 1.0, "avg_cost_usd": 0.01, "n": 1},
        ]

        s = GrokSessionStore(db_path=tmp_path / "cal_write.db")
        try:
            written = await write_calibration(s, results)
            assert written == 2
            rows = await s.get_routing_calibration()
            assert {(r["category"], r["route"], r["model"]) for r in rows} == {
                ("coding", "agentic", "m1"), ("reasoning", "thinking", "m2"),
            }
        finally:
            await s.close()


# ─────────────────────────────────────────────────────────────────────────────
# RoutingAdvisor: calibration precedence over raw telemetry
# ─────────────────────────────────────────────────────────────────────────────

def _calibration_rows(planning_model, coding_model, p_rate=0.95, c_rate=0.5,
                      p_n=6, c_n=6):
    return [
        {"category": "coding", "route": "agentic", "model": planning_model,
         "success_rate": p_rate, "avg_cost_usd": 0.01, "n": p_n},
        {"category": "coding", "route": "agentic", "model": coding_model,
         "success_rate": c_rate, "avg_cost_usd": 0.002, "n": c_n},
    ]


class TestRoutingAdvisorCalibration:
    @pytest.mark.asyncio
    async def test_calibration_takes_precedence_over_telemetry(self):
        """Fresh calibration favoring planning wins even when raw telemetry
        says the opposite."""
        advisor = RoutingAdvisor()
        # Telemetry: coding looks great, planning terrible → telemetry alone
        # would keep the static prior.
        advisor.inject_stats([
            {"plane": "API", "model": DEFAULT_PLANNING_MODEL, "samples": 40,
             "success_rate": 0.2, "avg_cost": 0.01},
            {"plane": "API", "model": DEFAULT_CODING_MODEL, "samples": 40,
             "success_rate": 0.9, "avg_cost": 0.002},
        ])
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL)
        )
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is True

    @pytest.mark.asyncio
    async def test_calibration_verdict_false_blocks_telemetry_flip(self):
        """Calibration that does NOT justify planning is final — the advisor
        must not fall through to a telemetry aggregate that would flip."""
        advisor = RoutingAdvisor()
        advisor.inject_stats([
            {"plane": "API", "model": DEFAULT_PLANNING_MODEL, "samples": 40,
             "success_rate": 0.9, "avg_cost": 0.01},
            {"plane": "API", "model": DEFAULT_CODING_MODEL, "samples": 40,
             "success_rate": 0.5, "avg_cost": 0.002},
        ])
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
                              p_rate=0.6, c_rate=0.6)
        )
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False

    @pytest.mark.asyncio
    async def test_small_n_calibration_rows_fall_back_to_telemetry(self):
        """Rows below n >= 5 are ineligible; with calibration undecidable the
        telemetry path decides as before."""
        advisor = RoutingAdvisor()
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
                              p_n=2, c_n=2)
        )
        advisor.inject_stats([
            {"plane": "API", "model": DEFAULT_PLANNING_MODEL, "samples": 40,
             "success_rate": 0.9, "avg_cost": 0.01},
            {"plane": "API", "model": DEFAULT_CODING_MODEL, "samples": 40,
             "success_rate": 0.5, "avg_cost": 0.002},
        ])
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is True  # decided by telemetry, not the tiny calibration rows

    @pytest.mark.asyncio
    async def test_one_sided_calibration_cannot_decide(self):
        """Eligible rows for only ONE model leave the verdict to telemetry."""
        advisor = RoutingAdvisor()
        advisor.inject_calibration([
            {"category": "coding", "route": "agentic", "model": DEFAULT_PLANNING_MODEL,
             "success_rate": 1.0, "avg_cost_usd": 0.01, "n": 10},
        ])
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False  # empty telemetry → static prior

    @pytest.mark.asyncio
    async def test_stale_calibration_rows_expire_via_store_ttl(self, tmp_path, monkeypatch):
        """Rows older than UNIGROK_CALIBRATION_TTL_HOURS never reach the
        advisor: the store-side freshness filter drops them, so the decision
        falls back to telemetry/static."""
        monkeypatch.setenv("UNI_GROK_TESTING", "0")
        s = GrokSessionStore(db_path=tmp_path / "cal_advisor.db")
        try:
            for row in _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL):
                await s.upsert_routing_calibration(**{
                    "category": row["category"], "route": row["route"],
                    "model": row["model"], "success_rate": row["success_rate"],
                    "avg_cost_usd": row["avg_cost_usd"], "n": row["n"],
                })

            advisor = RoutingAdvisor()
            mock_stats = AsyncMock(return_value=[])
            s.get_recent_model_stats = mock_stats
            assert await advisor.prefers_planning(
                s, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
            ) is True  # fresh calibration decides; telemetry untouched

            # Backdate beyond the TTL window → calibration snapshot empties →
            # empty telemetry keeps the static prior.
            stale_ts = (datetime.now() - timedelta(hours=400)).isoformat()
            async with s._lock:
                await s._conn.execute("UPDATE routing_calibration SET updated_at = ?", (stale_ts,))
                await s._conn.commit()
            advisor.invalidate()
            assert await advisor.prefers_planning(
                s, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
            ) is False
            mock_stats.assert_awaited()  # telemetry consulted only on fallback
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_testing_bypass_still_holds_without_injection(self):
        """UNI_GROK_TESTING=1 (conftest) keeps BOTH advisor sources inert:
        no calibration reads, no telemetry reads, static prior."""
        advisor = RoutingAdvisor()
        mock_store = MagicMock()
        mock_store.get_routing_calibration = AsyncMock()
        mock_store.get_recent_model_stats = AsyncMock()
        assert await advisor.prefers_planning(
            mock_store, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False
        mock_store.get_routing_calibration.assert_not_awaited()
        mock_store.get_recent_model_stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_view_reports_calibration_source(self):
        advisor = RoutingAdvisor()
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL)
        )
        view = await advisor.status_view(None)
        assert view["borderline_source"] == "calibration"
        assert view["calibration"]["source_active"] is True
        assert view["borderline_choice"] == "planning"


# ─────────────────────────────────────────────────────────────────────────────
# UNIGROK_EVAL_RECORD tap
# ─────────────────────────────────────────────────────────────────────────────

def _seed_inference_client(utils, monkeypatch, raw, *, api_key: str = "eval-test-key") -> None:
    """Put a fake client in the principal/owner cache for the active key path."""
    from src.principal_xai import resolve_inference_credential

    monkeypatch.setenv("XAI_API_KEY", api_key)
    monkeypatch.delenv("UNIGROK_PRINCIPAL_XAI_KEYS_JSON", raising=False)
    monkeypatch.setattr(utils, "XAI_API_KEY", api_key)
    utils._clients.clear()
    _key, _source, cache_id, generation = resolve_inference_credential()
    utils._clients[f"{cache_id}:{generation}"] = raw


class TestEvalRecordingTap:
    def test_off_by_default_returns_raw_client(self, monkeypatch):
        import src.utils as utils

        monkeypatch.delenv("UNIGROK_EVAL_RECORD", raising=False)
        raw = FakeClient(responses=[make_response(content="hi")])
        _seed_inference_client(utils, monkeypatch, raw)
        assert get_xai_client() is raw

    def test_on_records_sample_events(self, tmp_path, monkeypatch):
        import src.utils as utils

        record_file = tmp_path / "recorded.jsonl"
        monkeypatch.setenv("UNIGROK_EVAL_RECORD", "1")
        monkeypatch.setenv("UNIGROK_EVAL_RECORD_FILE", str(record_file))
        raw = FakeClient(responses=[make_response(content="recorded answer", cost_usd=0.007)])
        _seed_inference_client(utils, monkeypatch, raw)

        client = get_xai_client()
        assert isinstance(client, _EvalRecordingClient)
        chat = client.chat.create(model="grok-4.3")
        chat.append("system prompt")
        chat.append("user prompt")
        response = chat.sample()
        assert response.content == "recorded answer"  # passthrough untouched

        events = [json.loads(line) for line in record_file.read_text().splitlines()]
        assert len(events) == 1
        event = events[0]
        assert event["kind"] == "sample"
        assert event["model"] == "grok-4.3"
        assert event["content"] == "recorded answer"
        assert event["cost_usd"] == pytest.approx(0.007)
        assert len(event["prompt_sha256"]) == 64
        assert event["usage"]["completion_tokens"] == 20

    def test_recorded_content_is_redacted(self, tmp_path, monkeypatch):
        """recorded.jsonl is cassette raw material intended for check-in: a
        response echoing a credential (models routinely quote injected file
        context) must land redacted like every other persisted surface."""
        import src.utils as utils

        record_file = tmp_path / "recorded.jsonl"
        monkeypatch.setenv("UNIGROK_EVAL_RECORD", "1")
        monkeypatch.setenv("UNIGROK_EVAL_RECORD_FILE", str(record_file))
        leaked = "the key is XAI_API_KEY=xai-recordedsecret1234 as configured"
        raw = FakeClient(responses=[make_response(content=leaked)])
        _seed_inference_client(utils, monkeypatch, raw)

        client = get_xai_client()
        chat = client.chat.create(model="grok-4.3")
        chat.append("user prompt")
        response = chat.sample()
        assert response.content == leaked  # passthrough stays untouched

        event = json.loads(record_file.read_text().splitlines()[0])
        assert "xai-recordedsecret1234" not in event["content"]
        assert "[REDACTED" in event["content"]

    def test_hasattr_parse_parity_preserved(self, monkeypatch, tmp_path):
        """The reviewer capability-gates on hasattr(chat, 'parse') — the
        recording proxy must not invent the attribute on chats that lack it."""
        import src.utils as utils

        monkeypatch.setenv("UNIGROK_EVAL_RECORD", "1")
        monkeypatch.setenv("UNIGROK_EVAL_RECORD_FILE", str(tmp_path / "r.jsonl"))

        class _ParselessChat:
            def append(self, message):
                return self

            def sample(self):
                return make_response(content="x")

        raw = SimpleNamespace(chat=SimpleNamespace(create=lambda **kw: _ParselessChat()))
        _seed_inference_client(utils, monkeypatch, raw)
        chat = get_xai_client().chat.create(model="m")
        assert not hasattr(chat, "parse")
        assert hasattr(FakeChat(responses=[]), "parse")


# ─────────────────────────────────────────────────────────────────────────────
# Live tier gating (decision logic only — no network)
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveBatchGating:
    def _fast_tasks(self, count):
        return [
            _task(
                id=f"fast{i}",
                mode="fast",
                plane="api",
                graders=[{"type": "contains", "value": "x"}],
            )
            for i in range(count)
        ]

    def test_batch_service_usable_requires_full_surface(self):
        no_batch = SimpleNamespace()
        usable, reason = batch_service_usable(no_batch)
        assert usable is False and "no batch service" in reason

        partial = SimpleNamespace(batch=SimpleNamespace(create=lambda *a, **k: None))
        usable, reason = batch_service_usable(partial)
        assert usable is False and "lacks" in reason

        full = SimpleNamespace(batch=SimpleNamespace(
            create=lambda *a, **k: None, add=lambda *a, **k: None,
            get=lambda *a, **k: None, list_batch_results=lambda *a, **k: None,
        ))
        # Installed xai_sdk 1.17 chat.create accepts batch_request_id, so the
        # capability half of the probe passes against the real SDK.
        usable, reason = batch_service_usable(full)
        assert usable is True

    def test_batch_engages_only_for_four_plus_all_fast_tasks(self):
        use, note = batch_mode_decision(self._fast_tasks(4), usable=True, reason="ok")
        assert use is True

        use, note = batch_mode_decision(self._fast_tasks(3), usable=True, reason="ok")
        assert use is False and "need >= 4" in note

        mixed = self._fast_tasks(3) + [_task(id="agentic1")]
        use, note = batch_mode_decision(mixed, usable=True, reason="ok")
        assert use is False and "non-fast" in note

        use, note = batch_mode_decision(self._fast_tasks(8), usable=False, reason="no batch")
        assert use is False and "unavailable" in note

        cli_tasks = [
            _task(
                id=f"cli{i}",
                mode="fast",
                plane="cli",
                graders=[{"type": "contains", "value": "x"}],
            )
            for i in range(4)
        ]
        use, note = batch_mode_decision(cli_tasks, usable=True, reason="ok")
        assert use is False and "explicit CLI-plane" in note

        auto_tasks = [
            _task(id=f"auto{i}", mode="fast", graders=[{"type": "contains", "value": "x"}])
            for i in range(4)
        ]
        use, note = batch_mode_decision(auto_tasks, usable=True, reason="ok")
        assert use is False and "requires explicit" in note


# ─────────────────────────────────────────────────────────────────────────────
# RoutingAdvisor: semantic task-memory evidence (UNIGROK_TASK_RAG)
# Precedence: calibration > semantic (shadow|active only) > telemetry > static
# ─────────────────────────────────────────────────────────────────────────────

import src.rag as rag_module
from src.rag import SemanticVerdict


def _semantic_verdict(prefers, evidence=4):
    high, low = 0.9, 0.1
    return SemanticVerdict(
        prefers_planning=prefers,
        planning_signal=high if prefers else low,
        coding_signal=low if prefers else high,
        evidence_count=evidence,
        confidence=0.5,
    )


_FLIPPING_TELEMETRY = [
    {"plane": "API", "model": DEFAULT_PLANNING_MODEL, "samples": 40,
     "success_rate": 0.9, "avg_cost": 0.01},
    {"plane": "API", "model": DEFAULT_CODING_MODEL, "samples": 40,
     "success_rate": 0.5, "avg_cost": 0.002},
]


class TestRoutingAdvisorSemantic:
    @pytest.fixture(autouse=True)
    def _fresh_rag_state(self):
        rag_module.reset_task_rag_state()
        yield
        rag_module.reset_task_rag_state()

    @pytest.mark.asyncio
    async def test_calibration_true_beats_semantic_false(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL)
        )
        advisor.inject_semantic(_semantic_verdict(False))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is True
        assert advisor._last_decision.source == "calibration"

    @pytest.mark.asyncio
    async def test_calibration_false_beats_semantic_true(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_calibration(
            _calibration_rows(DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
                              p_rate=0.6, c_rate=0.6)
        )
        advisor.inject_semantic(_semantic_verdict(True))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is False
        assert advisor._last_decision.source == "calibration"

    @pytest.mark.asyncio
    async def test_active_semantic_false_blocks_telemetry_flip(self, monkeypatch):
        """Mirrors the calibration semantics one precedence rung down: a
        decidable semantic False is final even when telemetry would flip."""
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_stats(_FLIPPING_TELEMETRY)
        advisor.inject_semantic(_semantic_verdict(False))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is False
        assert advisor._last_decision.source == "semantic"
        assert rag_module.get_task_rag_stats()["applied_flips"] == 1

    @pytest.mark.asyncio
    async def test_active_semantic_true_flips_static_baseline(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_stats([])  # empty telemetry → static baseline False
        advisor.inject_semantic(_semantic_verdict(True))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is True
        assert advisor._last_decision.source == "semantic"
        assert advisor._last_decision.applied is True
        assert rag_module.get_task_rag_stats()["applied_flips"] == 1

    @pytest.mark.asyncio
    async def test_undecidable_semantic_falls_through_to_telemetry(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_stats(_FLIPPING_TELEMETRY)
        advisor.inject_semantic(_semantic_verdict(None))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is True  # telemetry decides, exactly as before semantic existed
        assert advisor._last_decision.source == "telemetry"
        assert advisor._last_decision.shadow is False

    @pytest.mark.asyncio
    async def test_shadow_returns_baseline_and_records_flip(self, monkeypatch):
        monkeypatch.setenv("UNIGROK_TASK_RAG", "shadow")
        advisor = RoutingAdvisor()
        advisor.inject_stats([])  # static baseline False
        advisor.inject_semantic(_semantic_verdict(True))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is False, "shadow mode must NEVER apply the semantic verdict"
        decision = advisor._last_decision
        assert decision.shadow is True
        assert decision.source == "static"
        assert decision.evidence_count == 4
        assert rag_module.get_task_rag_stats()["shadow_flips"] == 1
        assert rag_module.get_task_rag_stats()["applied_flips"] == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [None, "off", "mirror"])
    async def test_off_and_mirror_never_consult_semantic(self, monkeypatch, mode):
        if mode is None:
            monkeypatch.delenv("UNIGROK_TASK_RAG", raising=False)
        else:
            monkeypatch.setenv("UNIGROK_TASK_RAG", mode)
        advisor = RoutingAdvisor()
        advisor.inject_semantic(_semantic_verdict(True))  # must be ignored
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is False
        assert advisor._last_decision.source == "static"
        assert advisor._last_decision.shadow is False

    @pytest.mark.asyncio
    async def test_testing_env_without_injection_never_reaches_rag(self, monkeypatch):
        """UNI_GROK_TESTING=1 (conftest) keeps semantic evidence inert unless
        injected — offline evals and the seed suite stay byte-identical."""
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        gatherer = AsyncMock()
        monkeypatch.setattr(rag_module, "gather_semantic_evidence", gatherer)
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL,
            prompt="borderline business summary",
        ) is False
        gatherer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_three_arg_legacy_call_skips_semantic(self, monkeypatch):
        """No prompt → today's behavior exactly, even with a pinned verdict."""
        monkeypatch.setenv("UNIGROK_TASK_RAG", "active")
        advisor = RoutingAdvisor()
        advisor.inject_semantic(_semantic_verdict(True))
        assert await advisor.prefers_planning(
            None, DEFAULT_PLANNING_MODEL, DEFAULT_CODING_MODEL
        ) is False
        assert advisor._last_decision.source == "static"
