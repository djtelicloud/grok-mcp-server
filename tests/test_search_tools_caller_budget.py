"""Direct metered search/code tools must honor caller budgets and record spend."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.tools import system as system_tools
from src.utils import CallerBudgetExceeded, GrokSessionStore


class _FakeCtx:
    elapsed = 0.5

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def format_output(self, text, _objs=None):
        return text


class _FakeResponse:
    content = "ok"
    citations = ["https://example.test"]
    tool_outputs = None
    cost_usd = 0.07
    finish_reason = "final_answer"
    usage = type("U", (), {"prompt_tokens": 3, "completion_tokens": 4})()


@pytest.mark.asyncio
async def test_web_search_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "search-budget.db")
    monkeypatch.setattr(system_tools, "store", store)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    token = set_active_principal("http:key-test")
    try:
        with pytest.raises(CallerBudgetExceeded):
            await system_tools.web_search(prompt="latest news")
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_web_search_records_telemetry(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "search-telemetry.db")
    monkeypatch.setattr(system_tools, "store", store)
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)

    async def _fake_run_blocking(fn, timeout=60.0):
        return _FakeResponse()

    monkeypatch.setattr(system_tools, "run_blocking", _fake_run_blocking)
    monkeypatch.setattr(system_tools, "GrokInvocationContext", lambda *a, **k: _FakeCtx())

    token = set_active_principal("http:key-search")
    try:
        result = await system_tools.web_search(prompt="latest news")
        assert result.cost_usd == 0.07
        spent = await store.get_caller_cost_today("http:key-search")
        assert spent == pytest.approx(0.07)
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_x_search_and_remote_code_enforce_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "search-budget-more.db")
    monkeypatch.setattr(system_tools, "store", store)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    token = set_active_principal("http:key-test")
    try:
        with pytest.raises(CallerBudgetExceeded):
            await system_tools.x_search(prompt="posts about grok")
        with pytest.raises(CallerBudgetExceeded):
            await system_tools.remote_code_execution(prompt="print(1)")
    finally:
        reset_active_principal(token)
        await store.close()
