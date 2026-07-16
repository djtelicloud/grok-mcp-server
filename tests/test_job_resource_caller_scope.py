"""grok://jobs/{id} must honor bound caller fencing."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from src.identity import reset_active_caller, set_active_caller
from src.jobs import JobManager
from src.tools import resources as resources_mod
from src.utils import GrokSessionStore


@pytest.mark.asyncio
async def test_job_resource_hides_foreign_jobs(tmp_path, monkeypatch):
    store = GrokSessionStore(db_path=tmp_path / "jobs.db")
    mgr = JobManager(store)
    monkeypatch.setattr(resources_mod, "get_job_manager", lambda: mgr)
    await store.create_job("j-a", prompt="a", model="m", caller="cursor")
    await store.create_job("j-b", prompt="b", model="m", caller="vscode")

    mcp = FastMCP("probe")
    resources_mod.register_resource_primitives(mcp)

    token = set_active_caller("cursor")
    try:
        own_contents = list(await mcp.read_resource("grok://jobs/j-a"))
        foreign_contents = list(await mcp.read_resource("grok://jobs/j-b"))
    finally:
        reset_active_caller(token)

    def _payload(contents):
        text = (
            getattr(contents[0], "text", None)
            or getattr(contents[0], "content", None)
            or str(contents[0])
        )
        return json.loads(text) if isinstance(text, str) else text

    own_payload = _payload(own_contents)
    foreign_payload = _payload(foreign_contents)
    assert own_payload.get("job_id") == "j-a" or own_payload.get("status") != "not_found"
    assert foreign_payload.get("status") == "not_found"
    await store.close()
