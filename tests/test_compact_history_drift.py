"""Compaction must not wipe concurrent same-session appends."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils import GrokSessionStore, maybe_compact_history


@pytest.mark.asyncio
async def test_compact_skips_replace_on_message_count_drift(tmp_path, monkeypatch):
    monkeypatch.delenv("UNI_GROK_TESTING", raising=False)
    store = GrokSessionStore(db_path=tmp_path / "compact.db")
    history = [
        {"role": "user", "content": f"u{i} " + ("x" * 200), "time": "t"}
        for i in range(6)
    ]
    await store.replace_messages("sess", history)

    def _summarize_side_effect(*_a, **_k):
        raise AssertionError("should use async mock path")

    mock_chat = MagicMock()

    async def _during_sample():
        await store.save_message("sess", "user", "concurrent append during fold")
        return SimpleNamespace(content="COMPACT SUMMARY", cost_usd=0.0)

    # run_blocking calls the sync sample; inject concurrent append via
    # a sync sample that schedules the append through the store's event loop
    # by using a side_effect that mutates via asyncio.run_coroutine_threadsafe
    # — simpler: patch run_blocking to append then return.

    async def fake_run_blocking(fn, timeout=None):
        await store.save_message("sess", "user", "concurrent append during fold")
        return SimpleNamespace(content="COMPACT SUMMARY", cost_usd=0.0)

    monkeypatch.setattr("src.utils.resolve_model", AsyncMock(return_value="grok-code"))
    monkeypatch.setattr("src.utils.check_circuit_breaker", lambda *_a, **_k: None)
    monkeypatch.setattr("src.utils._fold_available", lambda: False)
    monkeypatch.setattr("src.utils.run_blocking", fake_run_blocking)
    monkeypatch.setattr("src.utils.record_xai_success", lambda *_a, **_k: None)
    monkeypatch.setattr("src.utils.record_xai_failure", lambda *_a, **_k: None)
    monkeypatch.setattr("src.utils._compact_threshold_tokens", lambda: 1)
    monkeypatch.setattr(
        "src.utils._compact_threshold_for", AsyncMock(return_value=1)
    )

    result = await maybe_compact_history("sess", history, store, force=True)
    assert result == history
    rows = await store.load_messages("sess")
    assert any("concurrent append" in (r.get("content") or "") for r in rows)
    assert len(rows) == 7
    await store.close()
