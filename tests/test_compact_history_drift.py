"""Compaction must not wipe concurrent same-session appends."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_compact_rejects_same_count_different_high_water_mark(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("UNI_GROK_TESTING", raising=False)
    store = GrokSessionStore(db_path=tmp_path / "compact-aba.db")
    history = [
        {"role": "user", "content": f"u{i} " + ("x" * 200), "time": "t"}
        for i in range(6)
    ]
    await store.replace_messages("sess", history)
    before_max, before_count = await store.message_snapshot("sess")

    async def fake_run_blocking(fn, timeout=None):
        async with store._lock:
            await store._conn.execute("BEGIN IMMEDIATE;")
            await store._conn.execute(
                "DELETE FROM messages WHERE id = ("
                "SELECT MIN(id) FROM messages WHERE session_name = ?)",
                ("sess",),
            )
            await store._conn.execute(
                "INSERT INTO messages "
                "(session_name, role, content, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, NULL)",
                ("sess", "user", "replacement with same count", "t"),
            )
            await store._conn.commit()
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

    after_max, after_count = await store.message_snapshot("sess")
    rows = await store.load_messages("sess")
    assert result == history
    assert before_count == after_count == 6
    assert after_max > before_max
    assert any("replacement with same count" in row["content"] for row in rows)
    await store.close()


@pytest.mark.asyncio
async def test_snapshot_replace_rejects_append_before_transaction(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "snapshot.db")
    await store.save_message_pair("sess", "u1", "a1")
    expected_max, expected_count = await store.message_snapshot("sess")
    await store.save_message_pair("sess", "u2", "a2")

    replaced = await store.replace_messages_if_snapshot(
        "sess",
        [{"role": "system", "content": "stale compacted history"}],
        expected_max_id=expected_max,
        expected_count=expected_count,
    )

    assert replaced is False
    rows = await store.load_messages("sess")
    assert [row["content"] for row in rows] == ["u1", "a1", "u2", "a2"]
    await store.close()


@pytest.mark.asyncio
async def test_save_message_pair_rolls_back_half_turn(tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "pair.db")
    await store._ensure_initialized()
    async with store._lock:
        await store._conn.execute(
            "CREATE TRIGGER reject_assistant BEFORE INSERT ON messages "
            "WHEN NEW.role = 'assistant' BEGIN "
            "SELECT RAISE(ABORT, 'assistant rejected'); END"
        )
        await store._conn.commit()

    with pytest.raises(Exception, match="assistant rejected"):
        await store.save_message_pair("sess", "user persisted?", "assistant")

    assert await store.message_snapshot("sess") == (0, 0)
    assert await store.get_session("sess") is None
    await store.close()
