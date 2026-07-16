"""Research/distill jobs always declare metered API and fail closed without a key."""

from __future__ import annotations

import pytest

from src.jobs import JobManager
from src.utils import GrokSessionStore


@pytest.mark.asyncio
async def test_describe_surfaces_api_plane(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "jobs.db")
    await store.create_job("j1", prompt="p", model="grok-4.5", caller="cursor")
    mgr = JobManager(store)
    view = await mgr.get("j1")
    assert view["plane"] == "API"
    assert view["billing_class"] == "metered"
    await store.close()


@pytest.mark.asyncio
async def test_run_job_fails_closed_without_api_key(tmp_path, monkeypatch):
    store = GrokSessionStore(db_path=tmp_path / "jobs2.db")
    mgr = JobManager(store)
    monkeypatch.setattr("src.jobs.xai_api_key_configured", lambda: False)
    await store.create_job("j2", prompt="research me", model="grok-4.5")
    await mgr._run_job("j2", "research me", "grok-4.5", None)
    row = await store.get_job("j2")
    assert row["status"] == "error"
    assert "metered API" in (row.get("result") or "")
    await store.close()
