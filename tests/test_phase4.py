# tests/test_phase4.py
# Verification tests for Phase 4: SQLite Database Optimization, Schema Migration, and Concurrency.

import asyncio
import pytest
from src.utils import GrokSessionStore, load_history, append_and_save_history
from src.tools.system import db_vacuum

@pytest.fixture
async def store(tmp_path):
    s = GrokSessionStore(db_path=tmp_path / "grok_sessions_test.db")
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_database_migration_and_indexes(store):
    """Verify that schema migration runs successfully, version is set, and indexes exist."""
    try:
        await store._ensure_initialized()
        
        # Check user_version is 1
        async with store._conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            version = row[0]
            assert version >= 1

        # Check indexes exist
        async with store._conn.execute("SELECT name FROM sqlite_master WHERE type='index';") as cursor:
            rows = await cursor.fetchall()
            indexes = {r[0] for r in rows}
            assert "idx_messages_session_id" in indexes
            assert "idx_telemetry_intent" in indexes
            assert "idx_messages_timestamp" in indexes
            assert "idx_telemetry_context_id" in indexes
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_message_metadata_roundtrip(store):
    """Verify metadata is correctly serialized/deserialized in SQLite."""
    session = "test_meta_sess"
    
    # Clear existing messages
    await store.delete_session(session)
    
    # Save a message with metadata
    meta = {"tokens": 150, "cost": 0.003, "nested": {"calls": 2}}
    await store.save_message(session, "assistant", "Response content", metadata=meta)
    
    # Load messages and check
    msgs = await store.load_messages(session)
    assert len(msgs) >= 1
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "Response content"
    assert msgs[-1]["metadata"] == meta


@pytest.mark.asyncio
async def test_sync_history_helpers_metadata(store):
    """Verify synchronous load_history and append_and_save_history support metadata."""
    session = "test_sync_meta_sess"
    
    # Clear session
    await store.delete_session(session)
    
    # Run synchronous helper
    history = []
    meta = {"model_plane": "API", "cached": True}
    await append_and_save_history(session, history, "Prompt message", "Response message", store, metadata=meta)
    
    # Load history synchronously
    loaded = await load_history(session, store)
    assert len(loaded) == 2
    assert loaded[0]["role"] == "user"
    assert loaded[1]["role"] == "assistant"
    assert loaded[1]["metadata"] == meta


@pytest.mark.asyncio
async def test_vacuum_db_tool():
    """Verify vacuum_db runs compaction cleanly."""
    res = await db_vacuum()
    assert "Database vacuum completed successfully" in res


@pytest.mark.asyncio
async def test_database_concurrency_stress(store):
    """Stress test parallel writing tasks to verify retry decorator handles busy locking."""
    session = "test_stress_sess"
    await store.delete_session(session)
    
    # Run 15 concurrent writes
    async def write_task(idx: int):
        await store.save_message(session, "user", f"Stress message {idx}", metadata={"task_idx": idx})
        await store.save_session(session, model="grok-4.3")
        
    tasks = [write_task(i) for i in range(15)]
    await asyncio.gather(*tasks)
    
    # Check messages are all saved
    msgs = await store.load_messages(session)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 15


@pytest.mark.asyncio
async def test_telemetry_context_id_roundtrip(store):
    """Verify context snapshots are persisted with telemetry rows."""
    context_id = "ctx-test-sha-file-context"
    await store.save_telemetry("intent", "API", 1, 0.25, 0.01, context_id=context_id)
    stats = await store.get_telemetry_stats()
    assert stats[0]["context_id"] == context_id
