"""Caller ownership for forget_fact."""

from __future__ import annotations

import pytest

from src.identity import reset_active_caller, set_active_caller
from src.tools import knowledge as knowledge_tools
from src.utils import GrokSessionStore


@pytest.fixture
async def kstore(tmp_path, monkeypatch):
    store = GrokSessionStore(db_path=tmp_path / "facts.db")
    monkeypatch.setattr(knowledge_tools, "store", store)
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_forget_fact_scopes_to_bound_caller(kstore):
    token_a = set_active_caller("cursor")
    try:
        saved = await knowledge_tools.remember_fact("cursor-owned durable fact")
    finally:
        reset_active_caller(token_a)
    fid = int(saved["fact_id"])

    token_b = set_active_caller("vscode")
    try:
        foreign = await knowledge_tools.forget_fact(fid)
    finally:
        reset_active_caller(token_b)
    assert foreign["status"] == "not_found"

    token_a = set_active_caller("cursor")
    try:
        own = await knowledge_tools.forget_fact(fid)
    finally:
        reset_active_caller(token_a)
    assert own["status"] == "deleted"
