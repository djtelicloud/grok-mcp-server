"""Public media tools must honor caller budgets and record Imagine spend."""

from __future__ import annotations

import json

import pytest

from src.identity import reset_active_principal, set_active_principal
from src.tools import media as media_tools
from src.utils import CallerBudgetExceeded, GrokSessionStore


class _FakeCtx:
    elapsed = 0.5

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def format_output(self, text, _objs=None):
        return text


@pytest.mark.asyncio
async def test_generate_image_enforces_caller_budget(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "media-budget.db")
    monkeypatch.setattr(media_tools, "store", store)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps({"http:key-test": 0.0}))
    token = set_active_principal("http:key-test")
    try:
        with pytest.raises(CallerBudgetExceeded):
            await media_tools.generate_image(prompt="a cat")
    finally:
        reset_active_principal(token)
        await store.close()


@pytest.mark.asyncio
async def test_generate_image_records_telemetry(monkeypatch, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "media-telemetry.db")
    monkeypatch.setattr(media_tools, "store", store)
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)

    class _Img:
        url = "https://example.test/img.png"
        prompt = "a cat"
        cost_usd = 0.12

    async def _fake_run_blocking(fn, timeout=120.0):
        return [_Img()]

    monkeypatch.setattr(media_tools, "run_blocking", _fake_run_blocking)
    monkeypatch.setattr(media_tools, "GrokInvocationContext", lambda *a, **k: _FakeCtx())

    token = set_active_principal("http:key-media")
    try:
        result = await media_tools.generate_image(prompt="a cat")
        assert result.cost_usd == 0.12
        spent = await store.get_caller_cost_today("http:key-media")
        assert spent == pytest.approx(0.12)
    finally:
        reset_active_principal(token)
        await store.close()
