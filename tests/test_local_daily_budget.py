from __future__ import annotations

import pytest

from unigrok_public import caller_budget


def test_local_daily_budget_unset_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raising=False)
    assert caller_budget.load_local_daily_budget() is None


def test_local_daily_budget_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "nope")
    with pytest.raises(caller_budget.CallerBudgetConfigurationError):
        caller_budget.load_local_daily_budget()


@pytest.mark.asyncio
async def test_local_daily_budget_fail_closed_and_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.5")
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)

    class Store:
        async def get_total_cost_today(self) -> float:
            return 1.5

    with pytest.raises(caller_budget.CallerBudgetExceeded, match="Local daily budget"):
        await caller_budget.enforce_local_daily_budget(Store())  # type: ignore[arg-type]

    class BrokenStore:
        async def get_total_cost_today(self) -> float:
            raise RuntimeError("ledger down")

    with pytest.raises(caller_budget.CallerBudgetUnavailable):
        await caller_budget.enforce_local_daily_budget(BrokenStore())  # type: ignore[arg-type]


def test_transport_security_enabled_on_main_server() -> None:
    from unigrok_public import server

    settings = server.mcp.settings.transport_security
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "localhost" in settings.allowed_hosts
    assert "127.0.0.1" in settings.allowed_hosts
