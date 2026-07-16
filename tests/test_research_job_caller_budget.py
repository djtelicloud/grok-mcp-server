"""Research/distill jobs must honor caller budgets and settle spend into telemetry."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.jobs import JobManager
from src.utils import GrokSessionStore


@pytest.mark.asyncio
async def test_submit_research_job_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "job-budget.db")
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    monkeypatch.setattr(JobManager, "_run_job", AsyncMock(return_value=None))
    manager = JobManager(job_store=store)
    token = set_active_principal("http:key-test")
    try:
        view = await manager.submit("find things", caller="http:key-test")
        assert "error" in view
        assert "budget" in view["error"].lower()
        assert await store.list_jobs(10) == []
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_submit_distill_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "distill-budget.db")
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    monkeypatch.setattr(JobManager, "_run_distill_job", AsyncMock(return_value=None))
    manager = JobManager(job_store=store)
    token = set_active_principal("http:key-test")
    try:
        view = await manager.submit_distill("s1", caller="http:key-test")
        assert "error" in view
        assert "budget" in view["error"].lower()
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_research_job_settles_cost_into_telemetry(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "job-telemetry.db")
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)
    manager = JobManager(job_store=store)

    class _Resp:
        content = "done"
        cost_usd = 0.25

    async def _fake_run_blocking(fn, timeout=60.0):
        return _Resp()

    monkeypatch.setattr("src.jobs.run_blocking", _fake_run_blocking)
    monkeypatch.setattr("src.jobs.get_xai_client", lambda: object())
    monkeypatch.setattr("src.jobs.AGENTIC_TOOLS_SCHEMA", [])
    monkeypatch.setattr(
        "src.jobs._chat_create_supports", lambda *_a, **_k: False
    )

    class _Chat:
        def append(self, *_a, **_k):
            return None

        def defer(self, timeout=None):
            return _Resp()

    class _Client:
        class chat:
            @staticmethod
            def create(**_kwargs):
                return _Chat()

    monkeypatch.setattr("src.jobs.get_xai_client", lambda: _Client())

    view = await manager.submit("find things", model="grok-4.3", caller="http:key-job")
    assert view["status"] == "queued"
    await manager.wait(view["job_id"])
    spent = await store.get_caller_cost_today("http:key-job")
    assert spent == pytest.approx(0.25)
    await store.close()
