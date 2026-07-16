"""chat_with_vision / chat_with_files must honor caller budgets and record spend."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.tools import chats as chat_tools
from src.utils import CallerBudgetExceeded, GrokSessionStore


class _FakeCtx:
    elapsed = 0.4
    context_injected = False
    finish_reason = "final_answer"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def format_output(self, text, _objs=None):
        return text


class _FakeResponse:
    content = "ok"
    citations = None
    cost_usd = 0.09
    finish_reason = "final_answer"
    usage = type("U", (), {"prompt_tokens": 2, "completion_tokens": 3})()


@pytest.mark.asyncio
async def test_chat_with_vision_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "vision-budget.db")
    monkeypatch.setattr(chat_tools, "store", store)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    token = set_active_principal("http:key-test")
    try:
        with pytest.raises(CallerBudgetExceeded):
            await chat_tools.chat_with_vision(
                prompt="what is this?",
                image_urls=["https://example.test/a.png"],
            )
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_chat_with_files_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "files-budget.db")
    monkeypatch.setattr(chat_tools, "store", store)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    token = set_active_principal("http:key-test")
    try:
        with pytest.raises(CallerBudgetExceeded):
            await chat_tools.chat_with_files(prompt="summarize", file_ids=["file-1"])
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_chat_with_vision_records_telemetry(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "vision-telemetry.db")
    monkeypatch.setattr(chat_tools, "store", store)
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)

    async def _fake_run_blocking(fn, timeout=60.0):
        return _FakeResponse()

    monkeypatch.setattr(chat_tools, "run_blocking", _fake_run_blocking)
    monkeypatch.setattr(chat_tools, "GrokInvocationContext", lambda *a, **k: _FakeCtx())

    token = set_active_principal("http:key-vision")
    try:
        result = await chat_tools.chat_with_vision(
            prompt="what is this?",
            image_urls=["https://example.test/a.png"],
        )
        assert result.cost_usd == 0.09
        spent = await store.get_caller_cost_today("http:key-vision")
        assert spent == pytest.approx(0.09)
    finally:
        reset_active_principal(token)
        await store.close()
