from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from unigrok_public import server
from unigrok_public.caller_budget import CallerBudgetExceeded, CallerBudgetUnavailable
from unigrok_public.state import PublicStateStore


@pytest.fixture
def guarded_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> PublicStateStore:
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.0")
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)
    store = PublicStateStore(tmp_path / "server-spend-guard.db")
    monkeypatch.setattr(server, "STATE", store)
    monkeypatch.setattr(server, "_CIRCUIT_BREAKERS", {})
    return store


@pytest.mark.asyncio
async def test_server_serializes_concurrent_metered_provider_calls(
    guarded_server: PublicStateStore,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def provider() -> dict[str, object]:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"text": "ok", "cost_usd": 0.25}

    first = asyncio.create_task(
        server._guarded_provider_call("api", "grok-test", provider)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)

    with pytest.raises(CallerBudgetExceeded, match="pending"):
        await server._guarded_provider_call("api", "grok-test", provider)
    assert calls == 1

    release.set()
    assert (await first)["text"] == "ok"
    status = await guarded_server.spend_guard_status(scope="local:aggregate")
    assert status["committed_usd"] == pytest.approx(0.25)
    assert status["pending_usd"] == 0
    assert status["unknown_count"] == 0

    assert (await server._guarded_provider_call("api", "grok-test", provider))[
        "text"
    ] == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_server_missing_provider_cost_blocks_later_spend(
    guarded_server: PublicStateStore,
) -> None:
    calls = 0

    async def missing_cost() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"text": "receipt omitted"}

    assert (await server._guarded_provider_call("api", "grok-test", missing_cost))[
        "text"
    ] == "receipt omitted"
    status = await guarded_server.spend_guard_status(scope="local:aggregate")
    assert status["unknown_count"] == 1

    with pytest.raises(CallerBudgetUnavailable, match="unresolved cost"):
        await server._guarded_provider_call("api", "grok-test", missing_cost)
    assert calls == 1


@pytest.mark.asyncio
async def test_cancelled_provider_attempt_records_unknown_cost(
    guarded_server: PublicStateStore,
) -> None:
    entered = asyncio.Event()

    async def cancelled_provider() -> dict[str, object]:
        entered.set()
        await asyncio.Event().wait()
        return {"text": "unreachable", "cost_usd": 0.0}

    task = asyncio.create_task(
        server._guarded_provider_call("api", "grok-test", cancelled_provider)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status = await guarded_server.spend_guard_status(scope="local:aggregate")
    assert status["unknown_count"] == 1
    with pytest.raises(CallerBudgetUnavailable, match="unresolved cost"):
        await server._guarded_provider_call(
            "api", "grok-test", cancelled_provider
        )


@pytest.mark.asyncio
async def test_repeated_open_breaker_rejections_create_no_spend_rows(
    guarded_server: PublicStateStore,
) -> None:
    calls = 0

    async def provider() -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not run while the circuit is open")

    key = server._breaker_key("api", "grok-test")
    server._CIRCUIT_BREAKERS[key] = {
        "failures": 3,
        "trips": 1,
        "open_until": server.time.monotonic() + 60,
        "half_open": False,
        "generation": 1,
    }

    for _ in range(1_000):
        with pytest.raises(RuntimeError, match="circuit breaker open"):
            await server._guarded_provider_call("api", "grok-test", provider)

    status = await guarded_server.spend_guard_status(scope="local:aggregate")
    assert status["committed_usd"] == 0
    assert status["pending_usd"] == 0
    assert status["unknown_count"] == 0
    assert calls == 0
    with sqlite3.connect(guarded_server.path) as connection:
        row_count = connection.execute(
            "SELECT COUNT(*) FROM spend_guard_reservations"
        ).fetchone()
    assert row_count == (0,)


@pytest.mark.asyncio
async def test_spend_denial_abandons_claimed_half_open_probe(
    monkeypatch: pytest.MonkeyPatch,
    guarded_server: PublicStateStore,
) -> None:
    calls = 0

    async def deny_spend() -> None:
        raise CallerBudgetUnavailable("spend denied")

    async def provider() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"text": "must not run", "cost_usd": 0.0}

    key = server._breaker_key("api", "grok-test")
    server._CIRCUIT_BREAKERS[key] = {
        "failures": 3,
        "trips": 1,
        "open_until": server.time.monotonic() - 1,
        "half_open": False,
        "generation": 1,
    }
    monkeypatch.setattr(server, "_begin_api_spend_guard", deny_spend)

    with pytest.raises(CallerBudgetUnavailable, match="spend denied"):
        await server._guarded_provider_call("api", "grok-test", provider)

    state = server._CIRCUIT_BREAKERS[key]
    assert calls == 0
    assert state["half_open"] is False
    assert state["generation"] == 2
    assert state["open_until"] > server.time.monotonic()
