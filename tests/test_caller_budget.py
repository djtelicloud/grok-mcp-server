from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

from unigrok_public import state as state_module
from unigrok_public.caller_budget import (
    CallerBudgetConfigurationError,
    CallerBudgetExceeded,
    CallerBudgetUnavailable,
    begin_spend_guard_attempt,
    enforce_caller_budget,
    finish_spend_guard_attempt,
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
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raising=False)
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


class _BrokenSpendStore:
    async def reserve_spend_guard(self, **_: object) -> dict[str, object]:
        raise OSError("ledger unavailable")


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
        ("", "empty"),
        ("   ", "empty"),
        ("[]", "not_object"),
        ("{}", "empty"),
        (f'{{"{PRINCIPAL}":1,"{PRINCIPAL}":2}}', "duplicate_principal"),
        (json.dumps({"oauth:github:123456": 1}), "invalid_principal"),
        (json.dumps({PRINCIPAL: "1.0"}), "invalid_limit"),
        (json.dumps({PRINCIPAL: True}), "invalid_limit"),
        (json.dumps({PRINCIPAL: -0.01}), "invalid_limit"),
        (json.dumps({PRINCIPAL: float("inf")}), "invalid_limit"),
        (json.dumps({PRINCIPAL: 1_000_000_000}), "invalid_limit"),
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


@pytest.mark.asyncio
async def test_unified_hosted_guard_hashes_exact_active_principal_and_serializes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0, OTHER_PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    token = set_active_principal(PRINCIPAL)
    try:
        path = tmp_path / "hosted-atomic.db"
        stores = [PublicStateStore(path) for _ in range(4)]
        await stores[0].initialize()
        await stores[0].save_telemetry(
            {
                "caller": PRINCIPAL_LABEL,
                "request_kind": "agent",
                "cost_usd": 0.4,
            }
        )
        results = await asyncio.gather(
            *(
                begin_spend_guard_attempt(
                    stores[index],
                    attempt_id=f"hosted-parallel-{index}",
                    stale_after_seconds=300,
                )
                for index in range(4)
            ),
            return_exceptions=True,
        )
        admitted = [
            item
            for item in results
            if isinstance(item, dict) and item.get("execute_provider") is True
        ]
        assert len(admitted) == 1
        assert sum(isinstance(item, CallerBudgetExceeded) for item in results) == 3
        expected_scope = (
            "hosted:oauth-sha256:"
            + hashlib.sha256(PRINCIPAL.encode("utf-8")).hexdigest()
        )
        assert admitted[0]["scope"] == expected_scope
        assert admitted[0]["guard_runtime"] == "hosted"
        assert admitted[0]["committed_usd"] == pytest.approx(0.4)
        assert admitted[0]["reserved_usd"] == pytest.approx(0.6)
        assert PRINCIPAL not in str(admitted[0])
        with stores[0]._connect() as connection:
            baselines = connection.execute(
                "SELECT COUNT(*) FROM spend_guard_telemetry_baselines"
            ).fetchone()
        assert baselines is not None and int(baselines[0]) == 1
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_unified_hosted_guard_omits_uncapped_exact_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    token = set_active_principal(OTHER_PRINCIPAL)
    try:
        result = await begin_spend_guard_attempt(
            _UnexpectedStore(),  # type: ignore[arg-type]
            attempt_id="uncapped-omitted-principal",
            stale_after_seconds=300,
        )
        assert result is None
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_unified_hosted_guard_fails_closed_on_config_or_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    token = set_active_principal(PRINCIPAL)
    try:
        assert (
            await begin_spend_guard_attempt(
                _UnexpectedStore(),  # type: ignore[arg-type]
                attempt_id="absent-config-disabled",
                stale_after_seconds=300,
            )
            is None
        )
    finally:
        reset_active_principal(token)

    monkeypatch.setenv("UNIGROK_OAUTH_AUTHORIZATION_SERVERS", ISSUER)
    monkeypatch.setenv("UNIGROK_CALLER_BUDGETS", "{")
    malformed = set_active_principal(PRINCIPAL)
    try:
        with pytest.raises(CallerBudgetUnavailable, match="configuration"):
            await begin_spend_guard_attempt(
                PublicStateStore(tmp_path / "invalid-config.db"),
                attempt_id="invalid-config",
                stale_after_seconds=300,
            )
    finally:
        reset_active_principal(malformed)

    _configure(monkeypatch, {PRINCIPAL: 1.0})
    with pytest.raises(CallerBudgetUnavailable, match="OAuth principal"):
        await begin_spend_guard_attempt(
            PublicStateStore(tmp_path / "missing-identity.db"),
            attempt_id="missing-identity",
            stale_after_seconds=300,
        )

    hostile = set_active_principal("oauth:not-a-canonical-principal")
    try:
        with pytest.raises(CallerBudgetUnavailable, match="OAuth principal"):
            await begin_spend_guard_attempt(
                PublicStateStore(tmp_path / "invalid-identity.db"),
                attempt_id="invalid-identity",
                stale_after_seconds=300,
            )
    finally:
        reset_active_principal(hostile)


@pytest.mark.asyncio
async def test_unified_hosted_guard_fails_closed_on_ledger_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    token = set_active_principal(PRINCIPAL)
    try:
        with pytest.raises(CallerBudgetUnavailable, match="ledger"):
            await begin_spend_guard_attempt(
                _BrokenSpendStore(),  # type: ignore[arg-type]
                attempt_id="broken-hosted-ledger",
                stale_after_seconds=300,
            )
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_unified_hosted_finish_is_identity_bound_and_unknown_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0, OTHER_PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    store = PublicStateStore(tmp_path / "hosted-identity-bound.db")
    token = set_active_principal(PRINCIPAL)
    try:
        admission = await begin_spend_guard_attempt(
            store,
            attempt_id="hosted-identity-bound",
            stale_after_seconds=300,
        )
        assert admission is not None
        other = set_active_principal(OTHER_PRINCIPAL)
        try:
            with pytest.raises(CallerBudgetUnavailable, match="identity"):
                await finish_spend_guard_attempt(
                    store,
                    attempt_id="hosted-identity-bound",
                    guard_scope=str(admission["scope"]),
                    actual_cost_usd=0.25,
                )
        finally:
            reset_active_principal(other)

        unknown = await finish_spend_guard_attempt(
            store,
            attempt_id="hosted-identity-bound",
            guard_scope=str(admission["scope"]),
            actual_cost_usd=None,
        )
        assert unknown["status"] == "unknown"
        with pytest.raises(CallerBudgetUnavailable, match="unresolved cost"):
            await begin_spend_guard_attempt(
                store,
                attempt_id="hosted-blocked-by-unknown",
                stale_after_seconds=300,
            )
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_hosted_upgrade_baselines_midday_telemetry_once_across_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setattr(
        state_module,
        "utc_now",
        lambda: "2026-08-04T12:00:00+00:00",
    )
    path = tmp_path / "hosted-midday-upgrade.db"
    store = PublicStateStore(path)
    await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.4}
    )
    await store.save_telemetry(
        {
            "caller": OTHER_PRINCIPAL_LABEL,
            "request_kind": "agent",
            "cost_usd": 9.0,
        }
    )

    token = set_active_principal(PRINCIPAL)
    try:
        first = await begin_spend_guard_attempt(
            store,
            attempt_id="first-after-upgrade",
            stale_after_seconds=300,
        )
        assert first is not None
        assert first["committed_usd"] == pytest.approx(0.4)
        assert first["reserved_usd"] == pytest.approx(0.6)
        assert PRINCIPAL not in str(first["scope"])

        await finish_spend_guard_attempt(
            store,
            attempt_id="first-after-upgrade",
            guard_scope=str(first["scope"]),
            actual_cost_usd=0.2,
        )
        # Normal telemetry persistence after a guarded call must not be added
        # again: the day baseline is immutable after its first transaction.
        await store.save_telemetry(
            {
                "caller": PRINCIPAL_LABEL,
                "request_kind": "agent",
                "cost_usd": 0.2,
            }
        )

        restarted = PublicStateStore(path)
        second = await begin_spend_guard_attempt(
            restarted,
            attempt_id="second-after-restart",
            stale_after_seconds=300,
        )
        assert second is not None
        assert second["committed_usd"] == pytest.approx(0.6)
        assert second["reserved_usd"] == pytest.approx(0.4)

        with restarted._connect() as connection:
            baseline = connection.execute(
                """
                SELECT telemetry_caller, baseline_ticks
                FROM spend_guard_telemetry_baselines
                WHERE scope=? AND utc_day=?
                """,
                (str(first["scope"]), "2026-08-04"),
            ).fetchone()
        assert baseline is not None
        assert str(baseline["telemetry_caller"]) == PRINCIPAL_LABEL
        assert PRINCIPAL not in str(dict(baseline))
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_first_hosted_baseline_survives_same_day_startup_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    day_start = datetime.now(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    clock = {"now": (day_start + timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    monkeypatch.setattr(
        state_module,
        "_retention_cutoff",
        lambda: (day_start + timedelta(hours=12)).isoformat(),
    )
    path = tmp_path / "same-day-prebaseline-retention.db"
    store = PublicStateStore(path)
    await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.4}
    )

    # Simulate process restart long after the configured retention cutoff but
    # before this caller has ever established a durable daily baseline.
    clock["now"] = (day_start + timedelta(hours=13)).isoformat()
    restarted = PublicStateStore(path)
    await restarted.initialize()
    with restarted._connect() as connection:
        telemetry_count = connection.execute(
            "SELECT COUNT(*) FROM telemetry WHERE caller=?",
            (PRINCIPAL_LABEL,),
        ).fetchone()
    assert telemetry_count is not None and int(telemetry_count[0]) == 1

    token = set_active_principal(PRINCIPAL)
    try:
        admission = await begin_spend_guard_attempt(
            restarted,
            attempt_id="first-after-short-retention",
            stale_after_seconds=300,
        )
        assert admission is not None
        assert admission["committed_usd"] == pytest.approx(0.4)
        assert admission["reserved_usd"] == pytest.approx(0.6)
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_legacy_reservation_replay_reconstructs_pre_guard_baseline_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    day_start = datetime.now(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    clock = {"now": (day_start + timedelta(hours=9)).isoformat()}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    path = tmp_path / "legacy-row-without-baseline.db"
    store = PublicStateStore(path)
    await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.4}
    )
    token = set_active_principal(PRINCIPAL)
    try:
        clock["now"] = (day_start + timedelta(hours=10)).isoformat()
        first = await begin_spend_guard_attempt(
            store,
            attempt_id="legacy-existing-reservation",
            stale_after_seconds=3600,
        )
        assert first is not None
        with store._connect() as connection:
            connection.execute("DELETE FROM spend_guard_telemetry_baselines")
            connection.commit()

        clock["now"] = (day_start + timedelta(hours=10, minutes=1)).isoformat()
        await finish_spend_guard_attempt(
            store,
            attempt_id="legacy-existing-reservation",
            guard_scope=str(first["scope"]),
            actual_cost_usd=0.2,
        )
        clock["now"] = (day_start + timedelta(hours=10, minutes=2)).isoformat()
        await store.save_telemetry(
            {
                "caller": PRINCIPAL_LABEL,
                "request_kind": "agent",
                "cost_usd": 0.2,
            }
        )

        clock["now"] = (day_start + timedelta(hours=10, minutes=3)).isoformat()
        replay = await begin_spend_guard_attempt(
            PublicStateStore(path),
            attempt_id="legacy-existing-reservation",
            stale_after_seconds=3600,
        )
        assert replay is not None
        assert replay["execute_provider"] is False
        assert replay["committed_usd"] == pytest.approx(0.6)

        next_attempt = await begin_spend_guard_attempt(
            PublicStateStore(path),
            attempt_id="after-legacy-replay",
            stale_after_seconds=3600,
        )
        assert next_attempt is not None
        assert next_attempt["reserved_usd"] == pytest.approx(0.4)
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_hosted_baseline_survives_telemetry_retention_and_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("UNIGROK_STATE_RETENTION_HOURS", "1")
    path = tmp_path / "hosted-baseline-retention.db"
    store = PublicStateStore(path)
    await store.save_telemetry(
        {"caller": PRINCIPAL_LABEL, "request_kind": "agent", "cost_usd": 0.45}
    )
    token = set_active_principal(PRINCIPAL)
    try:
        first = await begin_spend_guard_attempt(
            store,
            attempt_id="before-retention",
            stale_after_seconds=300,
        )
        assert first is not None
        await finish_spend_guard_attempt(
            store,
            attempt_id="before-retention",
            guard_scope=str(first["scope"]),
            actual_cost_usd=0.1,
        )
        with store._connect() as connection:
            connection.execute(
                "UPDATE telemetry SET created_at='2000-01-01T00:00:00+00:00'"
            )
            connection.commit()
        await store.prune_retention()

        with store._connect() as connection:
            telemetry_count = connection.execute(
                "SELECT COUNT(*) FROM telemetry"
            ).fetchone()
        assert telemetry_count is not None and int(telemetry_count[0]) == 0

        restarted = PublicStateStore(path)
        second = await begin_spend_guard_attempt(
            restarted,
            attempt_id="after-retention-restart",
            stale_after_seconds=300,
        )
        assert second is not None
        assert second["committed_usd"] == pytest.approx(0.55)
        assert second["reserved_usd"] == pytest.approx(0.45)
    finally:
        reset_active_principal(token)


@pytest.mark.asyncio
async def test_unified_hosted_replay_never_executes_after_utc_rollover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, {PRINCIPAL: 1.0})
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    clock = {"now": "2026-08-04T23:59:55+00:00"}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    token = set_active_principal(PRINCIPAL)
    try:
        store = PublicStateStore(tmp_path / "hosted-rollover.db")
        first = await begin_spend_guard_attempt(
            store,
            attempt_id="hosted-rollover",
            stale_after_seconds=60,
        )
        assert first is not None and first["execute_provider"] is True
        clock["now"] = "2026-08-05T00:00:05+00:00"
        replay = await begin_spend_guard_attempt(
            store,
            attempt_id="hosted-rollover",
            stale_after_seconds=60,
        )
        assert replay is not None
        assert replay["utc_day"] == "2026-08-04"
        assert replay["execute_provider"] is False
        with store._connect() as connection:
            baseline_days = connection.execute(
                "SELECT utc_day FROM spend_guard_telemetry_baselines"
            ).fetchall()
        assert [str(row["utc_day"]) for row in baseline_days] == ["2026-08-04"]
    finally:
        reset_active_principal(token)
