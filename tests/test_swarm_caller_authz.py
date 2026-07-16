"""Caller scoping for swarm get/list/cancel (mirror research-job authz)."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_caller, set_active_caller
from src.tools import swarm as swarm_tools


@pytest.fixture
async def swarm_rows(tmp_path, monkeypatch):
    from src.utils import GrokSessionStore

    store = GrokSessionStore(db_path=tmp_path / "swarm.db")
    monkeypatch.setattr(swarm_tools, "store", store)
    await store.create_swarm_task(
        task_id="task-a",
        target_path="a.py",
        focus_node="f",
        base_file_hash="h",
        test_target="pytest",
        bench_command="true",
        budget_usd=1.0,
        seed=1,
        caller="cursor",
    )
    await store.create_swarm_task(
        task_id="task-b",
        target_path="b.py",
        focus_node="g",
        base_file_hash="h",
        test_target="pytest",
        bench_command="true",
        budget_usd=1.0,
        seed=2,
        caller="vscode",
    )
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_list_swarm_tasks_scopes_to_bound_caller(swarm_rows):
    token = set_active_caller("cursor")
    try:
        raw = await swarm_tools.list_swarm_tasks(limit=10)
    finally:
        reset_active_caller(token)
    items = json.loads(raw)
    assert [i["task_id"] for i in items] == ["task-a"]


@pytest.mark.asyncio
async def test_get_and_cancel_foreign_swarm_look_missing(swarm_rows, monkeypatch):
    class _Runner:
        def cancel(self, task_id: str) -> None:
            raise AssertionError(f"must not cancel foreign task {task_id}")

    monkeypatch.setattr(swarm_tools, "_get_runner", lambda: _Runner())
    token = set_active_caller("cursor")
    try:
        status = await swarm_tools.get_swarm_status("task-b", view="json")
        cancel = await swarm_tools.cancel_swarm("task-b")
    finally:
        reset_active_caller(token)
    assert "no swarm task" in status
    assert "no swarm task" in cancel
