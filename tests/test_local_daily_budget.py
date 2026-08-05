from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from unigrok_public.caller_budget import (
    CallerBudgetConfigurationError,
    CallerBudgetExceeded,
    CallerBudgetUnavailable,
    enforce_caller_budget,
    load_local_daily_budget,
    reserve_caller_budget,
)
from unigrok_public.state import PublicStateStore


@pytest.fixture(autouse=True)
def _clean_local_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raising=False)
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)


@pytest.mark.parametrize("raw", ["not-a-number", "nan", "inf", "-0.01"])
def test_local_budget_rejects_invalid_limits(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raw)
    with pytest.raises(CallerBudgetConfigurationError) as captured:
        load_local_daily_budget()
    assert captured.value.code == "invalid_local_limit"


def test_local_budget_parses_zero_and_positive_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_local_daily_budget() is None
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "0")
    assert load_local_daily_budget() == 0.0
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.25")
    assert load_local_daily_budget() == 1.25


@pytest.mark.asyncio
async def test_zero_local_budget_denies_before_provider_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "0")
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(PublicStateStore(tmp_path / "budget.db"))


@pytest.mark.asyncio
async def test_local_budget_reservation_is_atomic_across_store_instances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1")
    path = tmp_path / "budget.db"
    first_store = PublicStateStore(path)
    second_store = PublicStateStore(path)

    first = await reserve_caller_budget(first_store)
    with pytest.raises(CallerBudgetUnavailable, match="already in flight"):
        await reserve_caller_budget(second_store)
    await first.settle(0.4)

    second = await reserve_caller_budget(second_store)
    await second.settle(0.6)
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(first_store)


@pytest.mark.asyncio
async def test_local_budget_backfills_current_day_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1")
    store = PublicStateStore(tmp_path / "budget.db")
    await store.save_telemetry(
        {"caller": "local", "request_kind": "agent", "cost_usd": 0.8}
    )
    reservation = await reserve_caller_budget(store)
    await reservation.settle(0.2)
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(store)


@pytest.mark.asyncio
async def test_unknown_provider_outcome_exhausts_the_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "5")
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_caller_budget(store)
    await reservation.exhaust()
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(store)


@pytest.mark.asyncio
async def test_invalid_reported_cost_exhausts_the_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "5")
    store = PublicStateStore(tmp_path / "budget.db")
    reservation = await reserve_caller_budget(store)
    with pytest.raises(CallerBudgetUnavailable, match="not safely reportable"):
        await reservation.settle(float("nan"))
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(store)


@pytest.mark.asyncio
async def test_expired_reservation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "5")
    store = PublicStateStore(tmp_path / "budget.db")
    await reserve_caller_budget(store)
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE local_budget_days SET reservation_expires_at=?",
            (expired,),
        )
        connection.commit()
    with pytest.raises(CallerBudgetExceeded):
        await reserve_caller_budget(store)


class _UnexpectedStore:
    def __getattr__(self, name: str):
        raise AssertionError(f"unexpected store access: {name}")


@pytest.mark.asyncio
async def test_cloudrun_ignores_local_budget_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "0")
    await enforce_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]
    reservation = await reserve_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]
    await reservation.settle(0.0)
