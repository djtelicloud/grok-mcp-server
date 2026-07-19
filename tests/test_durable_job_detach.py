"""A1 contracts: durable jobs outlive the MCP sync-window waiter."""

from __future__ import annotations

import asyncio

import pytest

from unigrok_public import server


@pytest.mark.asyncio
async def test_durable_job_survives_caller_cancel_at_sync_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: pending after short window; work still completes for pollers."""
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0.05)
    finished = asyncio.Event()

    async def produce() -> dict:
        await asyncio.sleep(0.25)
        finished.set()
        return {"status": "complete", "text": "ok", "value": 42}

    pending = await server._run_durable_job(
        produce, ctx=None, kind="web_search", sync_window=0.05
    )
    assert pending["status"] == "pending"
    job_id = pending["job_id"]

    async def cancelled_waiter() -> None:
        # Waiter cancel must not tear down provider work.
        await server.agent_result(job_id, wait_seconds=1)
        raise AssertionError("waiter should have been cancelled before terminal")

    waiter = asyncio.create_task(cancelled_waiter())
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await asyncio.wait_for(finished.wait(), timeout=1.0)
    terminal = await server.agent_result(job_id, wait_seconds=5)
    assert terminal["status"] == "complete"
    assert terminal.get("value") == 42


@pytest.mark.asyncio
async def test_cancel_job_is_explicit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: only explicit cancel_job stops work; sync-window does not."""
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0.05)
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def produce() -> dict:
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"status": "complete", "text": "late"}

    pending = await server._run_durable_job(
        produce, ctx=None, kind="chat", sync_window=0.05
    )
    job_id = pending["job_id"]
    assert pending["status"] == "pending"
    assert await server.cancel_job(job_id) is True
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    release.set()


@pytest.mark.asyncio
async def test_sync_window_pending_is_not_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: sync-window expiry returns pending without cancelling produce."""
    monkeypatch.setattr(server, "AGENT_SYNC_WINDOW_SECONDS", 0.05)
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    async def produce() -> dict:
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finished.set()
        return {"status": "complete", "text": "done"}

    pending = await server._run_durable_job(
        produce, ctx=None, kind="chat", sync_window=0.05
    )
    assert pending["status"] == "pending"
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    assert not cancelled.is_set()
    terminal = await server.agent_result(pending["job_id"], wait_seconds=5)
    assert terminal["status"] == "complete"
