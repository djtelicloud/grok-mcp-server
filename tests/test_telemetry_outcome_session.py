from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from unigrok_public import server
from unigrok_public.state import PublicStateStore, utc_now


def _create_legacy_telemetry(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                caller TEXT NOT NULL,
                request_kind TEXT NOT NULL,
                route TEXT,
                requested_plane TEXT,
                resolved_plane TEXT,
                model TEXT,
                success INTEGER,
                verified INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                fallback_reason TEXT,
                stop_reason TEXT,
                metadata TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO telemetry(
                created_at, caller, request_kind, success, verified,
                latency_ms, cost_usd, metadata
            ) VALUES (?, 'legacy', 'agent', NULL, 0, 1, 0, '{}')
            """,
            (utc_now(),),
        )
        connection.commit()


@pytest.mark.asyncio
async def test_telemetry_session_migration_preserves_rows_and_adds_index(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    _create_legacy_telemetry(path)

    store = PublicStateStore(path)
    await store.initialize()
    new_id = await store.save_telemetry(
        {
            "session_name": "demo:verification",
            "caller": "codex",
            "request_kind": "agent",
            "success": True,
        }
    )

    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(telemetry)").fetchall()
        }
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(telemetry)").fetchall()
        }
        rows = connection.execute(
            "SELECT id, session_name, success FROM telemetry ORDER BY id"
        ).fetchall()

    assert "session_name" in columns
    assert "telemetry_session_created" in indexes
    assert rows == [(1, None, None), (new_id, "demo:verification", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["", "EndTurn", "end_turn", "stop"])
async def test_agent_records_session_and_success_for_completed_stop_reasons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_reason: str,
) -> None:
    state = PublicStateStore(tmp_path / f"agent-{stop_reason or 'empty'}.db")

    async def completed_turn(**_kwargs: object) -> dict:
        return {
            "text": "A complete provider answer.",
            "model": "test-model",
            "resolved_plane": "local",
            "stop_reason": stop_reason,
            "cost_usd": 0.0,
            "orchestration": {"route": "direct"},
        }

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_execute_team_turn", completed_turn)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", False)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", False)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})

    result = await server.agent(task="Answer this", session="demo:verification")

    assert result["status"] == "complete"
    with sqlite3.connect(state.path) as connection:
        row = connection.execute(
            "SELECT session_name, success, verified FROM telemetry ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("demo:verification", 1, 0)


@pytest.mark.asyncio
async def test_agent_still_records_telemetry_when_outcome_classifier_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = PublicStateStore(tmp_path / "classifier-error.db")

    async def completed_turn(**_kwargs: object) -> dict:
        return {
            "text": "A complete provider answer.",
            "model": "test-model",
            "resolved_plane": "cli",
            "stop_reason": "EndTurn",
            "cost_usd": 0.0,
            "orchestration": {"route": "direct"},
        }

    def classifier_error(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_execute_team_turn", completed_turn)
    monkeypatch.setattr(server, "is_nonanswer_completion", classifier_error)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", False)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", False)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})

    result = await server.agent(task="Answer this", session="demo:verification")

    assert result["status"] == "complete"
    with sqlite3.connect(state.path) as connection:
        row = connection.execute(
            "SELECT session_name, success FROM telemetry ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("demo:verification", None)


@pytest.mark.asyncio
async def test_agent_records_failed_stop_and_forget_session_clears_join_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = PublicStateStore(tmp_path / "failed-stop.db")
    await state.append_turn("demo:forget-me", "Question", "Prior answer")

    async def incomplete_turn(**_kwargs: object) -> dict:
        return {
            "text": "A partial provider answer.",
            "model": "test-model",
            "resolved_plane": "api",
            "stop_reason": "length",
            "cost_usd": 0.0,
            "orchestration": {"route": "direct"},
        }

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_execute_team_turn", incomplete_turn)
    monkeypatch.setattr(server, "AUTONOMY_ENABLED", False)
    monkeypatch.setattr(server, "MISSION_V2_ENABLED", False)
    monkeypatch.setattr(server, "SHADOW_DONE_VOTE", False)
    monkeypatch.setattr(server, "_DURABLE_JOBS", {})

    result = await server.agent(task="Answer this", session="demo:forget-me")
    assert result["status"] == "complete"

    with sqlite3.connect(state.path) as connection:
        row = connection.execute(
            "SELECT session_name, success FROM telemetry ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("demo:forget-me", 0)

    assert await state.delete_session("demo:forget-me") is True
    with sqlite3.connect(state.path) as connection:
        cleared = connection.execute(
            "SELECT session_name, success FROM telemetry ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert cleared == (None, 0)
