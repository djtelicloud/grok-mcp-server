"""Timed run_blocking cancels must count orphaned threads."""

from __future__ import annotations

import asyncio
import time

import pytest

from src import utils as u


@pytest.mark.asyncio
async def test_timeout_increments_orphaned_counter(monkeypatch):
    before = u.get_runtime_stats()["timed_threads_orphaned"]

    def slow():
        time.sleep(0.2)
        return "done"

    with pytest.raises(asyncio.TimeoutError):
        await u.run_blocking(slow, timeout=0.05)
    # Allow daemon thread to finish decrementing in-flight.
    await asyncio.sleep(0.25)
    after = u.get_runtime_stats()["timed_threads_orphaned"]
    assert after == before + 1
