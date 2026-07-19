from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

from unigrok_public.caller_budget import (
    CallerBudgetConfigurationError,
    CallerBudgetExceeded,
    CallerBudgetUnavailable,
    enforce_caller_budget,
    load_caller_budgets,
)
from unigrok_public.identity import (
    principal_label,
    reset_active_principal,
    set_active_principal,
)
from unigrok_public.state import PublicStateStore

ISSUER = "https://control.grokmcp.org"
SUBJECT = "github:123456"
PRINCIPAL = (
    "oauth:"
    f"{quote(ISSUER, safe='-._~')}:"
    f"{quote(SUBJECT, safe='-._~')}"
)
OTHER_PRINCIPAL = (
    "oauth:"
    f"{quote(ISSUER, safe='-._~')}:"
    f"{quote('github:999999', safe='-._~')}"
)
PRINCIPAL_LABEL = principal_label(PRINCIPAL)
OTHER_PRINCIPAL_LABEL = principal_label(OTHER_PRINCIPAL)
assert PRINCIPAL_LABEL is not None
assert OTHER_PRINCIPAL_LABEL is not None


@pytest.fixture(autouse=True)
def _clean_budget_context(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("UNIGROK_CALLER_BUDGETS", raising=False)
    monkeypatch.delenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", raising=False)
    token = set_active_principal(None)
    try:
        yield
    finally:
        reset_active_principal(token)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    budgets: dict[str, object],
) -> None:
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", ISSUER)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", json.dumps(budgets))


class _UnexpectedStore:
    async def get_caller_cost_today(self, principal: str) -> float:
        raise AssertionError(f"unexpected ledger query for {principal}")


class _RecordingStore:
    def __init__(self, cost: float) -> None:
        self.cost = cost
        self.callers: list[str] = []

    async def get_caller_cost_today(self, caller: str) -> float:
        self.callers.append(caller)
        return self.cost


@pytest.mark.asyncio
async def test_absent_budget_config_preserves_local_noop() -> None:
    await enforce_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]
    assert load_caller_budgets() == {}


def test_valid_budget_map_is_exact_and_numeric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.25, OTHER_PRINCIPAL: 0})
    assert load_caller_budgets() == {PRINCIPAL: 1.25, OTHER_PRINCIPAL: 0.0}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not-json", "invalid_json"),
        ("[]", "not_object"),
        ("{}", "empty"),
        (f'{{"{PRINCIPAL}":1,"{PRINCIPAL}":2}}', "duplicate_principal"),
        (json.dumps({"oauth:github:123456": 1}), "invalid_principal"),
        (json.dumps({PRINCIPAL: "1.0"}), "invalid_limit"),
        (json.dumps({PRINCIPAL: True}), "invalid_limit"),
        (json.dumps({PRINCIPAL: -0.01}), "invalid_limit"),
        (json.dumps({PRINCIPAL: float("inf")}), "invalid_limit"),
    ],
)
def test_budget_config_rejects_ambiguous_or_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    code: str,
) -> None:
    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", ISSUER)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", raw)
    with pytest.raises(CallerBudgetConfigurationError) as captured:
        load_caller_budgets()
    assert captured.value.code == code


def test_budget_config_rejects_unlisted_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv(
        "UNIGROK_OAUTH_AUTHORIZATION_SERVERS", "https://other.example.com"
    )
    with pytest.raises(CallerBudgetConfigurationError) as captured:
        load_caller_budgets()
    assert captured.value.code == "invalid_principal"


@pytest.mark.asyncio
async def test_daily_cost_query_is_exact_principal_and_utc_day(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "budget.db")
    current_id = await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.4}
    )
    old_id = await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 7.0}
    )
    await store.save_telemetry(
        {"caller": OTHER_PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 9.0}
    )
    old_timestamp = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE telemetry SET created_at=? WHERE id=?", (old_timestamp, old_id)
        )
        connection.commit()

    assert current_id > 0
    assert await store.get_caller_cost_today(PRINCIPAL_LABEL) == pytest.approx(0.4)
    assert await store.get_caller_cost_today(OTHER_PRINCIPAL_LABEL) == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_budget_allows_under_cap_then_rejects_at_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    token = set_active_principal(PRINCIPAL)
    try:
        store = PublicStateStore(tmp_path / "budget.db")
        await store.save_telemetry(
            {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.4}
        )
        await enforce_caller_budget(store)
        await store.save_telemetry(
            {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.6}
        )
        with pytest.raises(CallerBudgetExceeded, match="Daily caller budget exhausted"):
            await enforce_caller_budget(store)
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_budget_queries_the_telemetry_principal_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    token = set_active_principal(PRINCIPAL)
    try:
        store = _RecordingStore(0.25)
        await enforce_caller_budget(store)  # type: ignore[arg-type]
        assert store.callers == [PRINCIPAL_LABEL]
        assert PRINCIPAL not in store.callers
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_zero_budget_denies_before_any_spend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 0})
    token = set_active_principal(PRINCIPAL)
    try:
        with pytest.raises(CallerBudgetExceeded):
            await enforce_caller_budget(PublicStateStore(tmp_path / "budget.db"))
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_configured_budget_requires_bound_oauth_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    with pytest.raises(CallerBudgetUnavailable, match="authenticated OAuth principal"):
        await enforce_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unlisted_authenticated_principal_keeps_compatibility_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    token = set_active_principal(OTHER_PRINCIPAL)
    try:
        await enforce_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_ledger_failure_denies_configured_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    token = set_active_principal(PRINCIPAL)
    try:
        with pytest.raises(CallerBudgetUnavailable, match="ledger is unavailable"):
            await enforce_caller_budget(_UnexpectedStore())  # type: ignore[arg-type]
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_daily_cost_query_rejects_unstorable_caller(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "budget.db")
    with pytest.raises(ValueError, match="caller must be 1-160 characters"):
        await store.get_caller_cost_today("x" * 161)
