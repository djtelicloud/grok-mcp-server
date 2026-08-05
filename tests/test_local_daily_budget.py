from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from unigrok_public import server
from unigrok_public.caller_budget import (
    CallerBudgetConfigurationError,
    CallerBudgetExceeded,
    CallerBudgetUnavailable,
    load_local_daily_budget,
    release_local_budget,
    reserve_local_budget,
    settle_local_budget,
    settle_local_budget_error,
)
from unigrok_public.state import PublicStateStore


@pytest.fixture(autouse=True)
def _clean_local_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch, limit: str = "1.0") -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", limit)


def _budget_row(store: PublicStateStore) -> tuple[float, str | None, str | None]:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT spent_usd, lease_token, lease_expires_at "
            "FROM local_daily_budget ORDER BY day DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return float(row[0]), row[1], row[2]


def test_local_daily_budget_parses_optional_finite_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_local_daily_budget() is None
    _configure(monkeypatch, "1.25")
    assert load_local_daily_budget() == pytest.approx(1.25)


@pytest.mark.parametrize("value", ["not-a-number", "-0.01", "nan", "inf"])
def test_local_daily_budget_rejects_invalid_limits(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _configure(monkeypatch, value)
    with pytest.raises(CallerBudgetConfigurationError) as captured:
        load_local_daily_budget()
    assert captured.value.code == "invalid_local_limit"


@pytest.mark.asyncio
async def test_zero_local_budget_denies_before_provider_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, "0")
    with pytest.raises(CallerBudgetExceeded, match="Local daily budget exhausted"):
        await reserve_local_budget(PublicStateStore(tmp_path / "budget.db"))


@pytest.mark.asyncio
async def test_local_budget_initialization_failure_is_typed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)

    class BrokenStore:
        path = tmp_path / "budget.db"

        async def initialize(self) -> None:
            raise sqlite3.OperationalError("unavailable")

    with pytest.raises(CallerBudgetUnavailable, match="ledger is unavailable"):
        await reserve_local_budget(BrokenStore())


@pytest.mark.asyncio
async def test_cloudrun_ignores_local_daily_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    assert await reserve_local_budget(PublicStateStore(tmp_path / "budget.db")) is None


@pytest.mark.asyncio
async def test_local_budget_serializes_and_releases_reservations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    path = tmp_path / "budget.db"
    first_store = PublicStateStore(path)
    second_store = PublicStateStore(path)
    await first_store.initialize()
    await second_store.initialize()
    outcomes = await asyncio.gather(
        reserve_local_budget(first_store),
        reserve_local_budget(second_store),
        return_exceptions=True,
    )
    reservations = [item for item in outcomes if not isinstance(item, BaseException)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(reservations) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CallerBudgetUnavailable)
    first = reservations[0]
    assert first is not None
    await release_local_budget(first_store, first)
    second = await reserve_local_budget(second_store)
    assert second is not None
    await settle_local_budget(second_store, second, {"cost_usd": 0.25})
    spent, lease_token, lease_expiry = _budget_row(second_store)
    assert spent == pytest.approx(0.25)
    assert lease_token is None
    assert lease_expiry is None


@pytest.mark.asyncio
async def test_local_budget_settles_exact_cost_and_exhausts_at_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    first = await reserve_local_budget(store)
    assert first is not None
    await settle_local_budget(store, first, {"cost_usd": 0.4})
    second = await reserve_local_budget(store)
    assert second is not None
    await settle_local_budget(store, second, {"cost_usd": 0.6})
    with pytest.raises(CallerBudgetExceeded, match="Local daily budget exhausted"):
        await reserve_local_budget(store)


@pytest.mark.asyncio
async def test_local_budget_seeds_existing_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    await store.save_telemetry(
        {"caller": "local", "request_kind": "agent", "cost_usd": 0.7}
    )
    reservation = await reserve_local_budget(store)
    assert reservation is not None
    await settle_local_budget(store, reservation, {"cost_usd": 0.2})
    assert _budget_row(store)[0] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_expired_local_budget_lease_exhausts_the_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_local_budget(store)
    assert reservation is not None
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE local_daily_budget SET lease_expires_at=? WHERE day=?",
            (expired, reservation.day),
        )
        connection.commit()
    with pytest.raises(CallerBudgetExceeded, match="without an exact receipt"):
        await reserve_local_budget(store)
    spent, lease_token, lease_expiry = _budget_row(store)
    assert spent == pytest.approx(1.0)
    assert lease_token is None
    assert lease_expiry is None


@pytest.mark.asyncio
async def test_known_error_receipts_settle_the_local_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_local_budget(store)
    assert reservation is not None

    class IncurredError(RuntimeError):
        incurred_attempts = (
            {"cost_usd": 0.2},
            {"cost_usd": 0.15},
        )

    error = IncurredError("provider failed")
    await settle_local_budget_error(store, reservation, error)
    assert _budget_row(store)[0] == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_unknown_error_receipt_keeps_local_budget_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_local_budget(store)
    assert reservation is not None
    await settle_local_budget_error(store, reservation, RuntimeError("unknown outcome"))
    with pytest.raises(CallerBudgetUnavailable, match="still active"):
        await reserve_local_budget(store)


@pytest.mark.asyncio
async def test_missing_success_receipt_exhausts_local_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_local_budget(store)
    assert reservation is not None
    with pytest.raises(CallerBudgetUnavailable, match="was exhausted"):
        await settle_local_budget(store, reservation, {})
    spent, lease_token, lease_expiry = _budget_row(store)
    assert spent == pytest.approx(1.0)
    assert lease_token is None
    assert lease_expiry is None


@pytest.mark.asyncio
async def test_guarded_api_call_reserves_and_settles_local_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    monkeypatch.setattr(server, "STATE", store)
    server._CIRCUIT_BREAKERS.clear()

    async def operation() -> dict[str, Any]:
        return {"cost_usd": 0.3, "text": "ok"}

    result = await server._guarded_provider_call("api", "test-model", operation)
    assert result["text"] == "ok"
    assert _budget_row(store)[0] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_cancelled_guarded_api_call_keeps_local_budget_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    monkeypatch.setattr(server, "STATE", store)
    server._CIRCUIT_BREAKERS.clear()

    async def operation() -> dict[str, Any]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await server._guarded_provider_call("api", "test-model", operation)
    with pytest.raises(CallerBudgetUnavailable, match="still active"):
        await reserve_local_budget(store)


@pytest.mark.asyncio
async def test_guarded_api_call_releases_before_breaker_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    monkeypatch.setattr(server, "STATE", store)

    def reject(_plane: str, _model: str | None) -> Any:
        raise RuntimeError("circuit breaker open")

    monkeypatch.setattr(server, "_breaker_before_call", reject)

    async def operation() -> dict[str, Any]:
        raise AssertionError("operation must not run")

    with pytest.raises(RuntimeError, match="circuit breaker open"):
        await server._guarded_provider_call("api", "test-model", operation)
    reservation = await reserve_local_budget(store)
    assert reservation is not None
    await release_local_budget(store, reservation)


@pytest.mark.asyncio
async def test_metered_durable_job_settles_local_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch)
    store = PublicStateStore(tmp_path / "budget.db")
    monkeypatch.setattr(server, "STATE", store)
    server._DURABLE_JOBS.clear()
    server._JOB_TASKS.clear()

    async def produce() -> dict[str, Any]:
        return {"status": "complete", "cost_usd": 0.2, "text": "done"}

    result = await server._run_durable_job(
        produce,
        ctx=None,
        kind="web_search",
        sync_window=1.0,
    )
    assert result["text"] == "done"
    assert _budget_row(store)[0] == pytest.approx(0.2)
