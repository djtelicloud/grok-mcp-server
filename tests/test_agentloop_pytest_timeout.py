"""AgentLoop must honor run_local_tests max_seconds over the 30s default."""

from __future__ import annotations

import asyncio

import pytest

from src.utils import dispatch_internal_tool, register_internal_tool


@pytest.mark.asyncio
async def test_dispatch_extends_timeout_for_run_local_tests(monkeypatch):
    seen: dict[str, float] = {}

    async def fake_tests(max_seconds: int = 60, **_kwargs):
        await asyncio.sleep(0.05)
        return f"ok:{max_seconds}"

    register_internal_tool("run_local_tests", fake_tests)

    real_wait_for = asyncio.wait_for

    async def capture_wait_for(awaitable, timeout=None):
        seen["timeout"] = float(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", capture_wait_for)
    obs = await dispatch_internal_tool(
        "run_local_tests",
        {"max_seconds": 90, "target": "tests"},
        timeout_sec=30.0,
    )
    assert obs.success is True
    assert seen["timeout"] == pytest.approx(95.0)
