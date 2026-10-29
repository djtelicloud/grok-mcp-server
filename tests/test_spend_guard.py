from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from unigrok_public import state as state_module
from unigrok_public.caller_budget import (
    CallerBudgetConfigurationError,
    CallerBudgetExceeded,
    CallerBudgetUnavailable,
    begin_local_spend_guard_attempt,
    begin_spend_guard_attempt,
    finish_local_spend_guard_attempt,
    finish_spend_guard_attempt,
    load_local_daily_spend_guard_limit,
)
from unigrok_public.state import PublicStateStore


@pytest.fixture(autouse=True)
def _clean_local_guard_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", raising=False)
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)


def test_local_spend_guard_limit_is_optional_and_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_local_daily_spend_guard_limit() is None
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.25")
    assert load_local_daily_spend_guard_limit() == pytest.approx(1.25)

    for invalid in ("nope", "-1", "nan", "inf", "1000000000"):
        monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", invalid)
        with pytest.raises(CallerBudgetConfigurationError) as captured:
            load_local_daily_spend_guard_limit()
        assert captured.value.code == "invalid_local_spend_guard_limit"

    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "922337203.6854776")
    with pytest.raises(CallerBudgetConfigurationError):
        load_local_daily_spend_guard_limit()


@pytest.mark.asyncio
async def test_atomic_reservations_prevent_parallel_admission(
    tmp_path: Path,
) -> None:
    path = tmp_path / "atomic-guard.db"
    stores = [PublicStateStore(path) for _ in range(4)]
    await stores[0].initialize()

    async def reserve(index: int) -> dict[str, object]:
        return await stores[index].reserve_spend_guard(
            reservation_id=f"parallel-{index}",
            scope="local:aggregate",
            limit_usd=1.0,
            reserve_usd=0.6,
            stale_after_seconds=300,
        )

    results = await asyncio.gather(*(reserve(index) for index in range(4)))
    assert sum(bool(item["execute_provider"]) for item in results) == 1
    assert sum(item["reason"] == "limit_reached" for item in results) == 3

    status = await stores[0].spend_guard_status(scope="local:aggregate")
    assert status["pending_usd"] == pytest.approx(0.6)
    assert status["committed_usd"] == 0.0


@pytest.mark.asyncio
async def test_atomic_reserve_remaining_serializes_one_call_per_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remaining-guard.db"
    stores = [PublicStateStore(path) for _ in range(4)]
    await stores[0].initialize()

    async def reserve(index: int) -> dict[str, object]:
        return await stores[index].reserve_spend_guard(
            reservation_id=f"remaining-{index}",
            scope="local:aggregate",
            limit_usd=1.0,
            reserve_remaining=True,
            stale_after_seconds=300,
        )

    results = await asyncio.gather(*(reserve(index) for index in range(4)))
    admitted = [item for item in results if item["execute_provider"]]
    assert len(admitted) == 1
    assert admitted[0]["reservation_mode"] == "remaining"
    assert admitted[0]["reserved_usd"] == pytest.approx(1.0)
    assert sum(item["reason"] == "scope_busy" for item in results) == 3

    await stores[0].reconcile_spend_guard(
        reservation_id=str(admitted[0]["reservation_id"]),
        actual_cost_usd=0.25,
    )
    next_attempt = await stores[1].reserve_spend_guard(
        reservation_id="remaining-after-reconcile",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_remaining=True,
        stale_after_seconds=300,
    )
    assert next_attempt["execute_provider"] is True
    assert next_attempt["reserved_usd"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_reserve_remaining_waits_for_existing_fixed_attempt(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "mixed-reservation-modes.db")
    fixed = await store.reserve_spend_guard(
        reservation_id="fixed-in-flight",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.2,
        stale_after_seconds=300,
    )
    assert fixed["execute_provider"] is True

    remaining = await store.reserve_spend_guard(
        reservation_id="remaining-must-wait",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_remaining=True,
        stale_after_seconds=300,
    )
    assert remaining["admitted"] is False
    assert remaining["reason"] == "scope_busy"

    await store.reconcile_spend_guard(
        reservation_id="fixed-in-flight",
        actual_cost_usd=0.15,
    )
    admitted = await store.reserve_spend_guard(
        reservation_id="remaining-after-fixed",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_remaining=True,
        stale_after_seconds=300,
    )
    assert admitted["execute_provider"] is True
    assert admitted["reserved_usd"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_reservation_and_reconciliation_are_idempotent(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "idempotent-guard.db")
    first = await store.reserve_spend_guard(
        reservation_id="same-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.4,
        stale_after_seconds=300,
    )
    replay = await store.reserve_spend_guard(
        reservation_id="same-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.4,
        stale_after_seconds=300,
    )
    assert first["execute_provider"] is True
    assert replay["admitted"] is True
    assert replay["execute_provider"] is False
    assert replay["created"] is False

    reconciled = await store.reconcile_spend_guard(
        reservation_id="same-attempt",
        actual_cost_usd=0.25,
    )
    repeated = await store.reconcile_spend_guard(
        reservation_id="same-attempt",
        actual_cost_usd=0.25,
    )
    assert reconciled["status"] == "reconciled"
    assert repeated["status"] == "reconciled"
    assert repeated["committed_usd"] == pytest.approx(0.25)
    assert repeated["pending_usd"] == 0.0


@pytest.mark.asyncio
async def test_conflicting_reconciliation_stays_fail_closed(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "conflicting-reconcile.db")
    await store.reserve_spend_guard(
        reservation_id="conflicting-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.4,
        stale_after_seconds=300,
    )
    await store.reconcile_spend_guard(
        reservation_id="conflicting-attempt",
        actual_cost_usd=0.9,
    )
    conflict = await store.reconcile_spend_guard(
        reservation_id="conflicting-attempt",
        actual_cost_usd=0.1,
    )
    repeated = await store.reconcile_spend_guard(
        reservation_id="conflicting-attempt",
        actual_cost_usd=0.1,
    )
    assert conflict["status"] == "unknown"
    assert conflict["reason"] == "conflicting_reconciliation"
    assert repeated["status"] == "unknown"

    denied = await store.reserve_spend_guard(
        reservation_id="blocked-after-conflict",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.1,
        stale_after_seconds=300,
    )
    assert denied["reason"] == "unknown_cost"

    resolved = await store.resolve_spend_guard_unknown(
        reservation_id="conflicting-attempt",
        authoritative_cost_usd=0.9,
    )
    assert resolved["status"] == "reconciled"
    assert resolved["committed_usd"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_unknown_cost_fails_closed_until_authoritatively_resolved(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "unknown-guard.db")
    await store.reserve_spend_guard(
        reservation_id="unknown-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.2,
        stale_after_seconds=300,
    )
    unknown = await store.reconcile_spend_guard(
        reservation_id="unknown-attempt",
        actual_cost_usd=None,
    )
    assert unknown["status"] == "unknown"
    assert unknown["unknown_count"] == 1

    denied = await store.reserve_spend_guard(
        reservation_id="blocked-after-unknown",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.1,
        stale_after_seconds=300,
    )
    assert denied["admitted"] is False
    assert denied["reason"] == "unknown_cost"

    resolved = await store.resolve_spend_guard_unknown(
        reservation_id="unknown-attempt",
        authoritative_cost_usd=0.3,
    )
    assert resolved["status"] == "reconciled"
    assert resolved["unknown_count"] == 0
    admitted = await store.reserve_spend_guard(
        reservation_id="admitted-after-resolution",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.7,
        stale_after_seconds=300,
    )
    assert admitted["execute_provider"] is True


@pytest.mark.asyncio
async def test_stale_reservation_becomes_unknown_but_only_for_its_utc_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = {"now": "2026-08-04T12:00:00+00:00"}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    store = PublicStateStore(tmp_path / "stale-guard.db")
    await store.reserve_spend_guard(
        reservation_id="crashed-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.4,
        stale_after_seconds=60,
    )

    clock["now"] = "2026-08-04T12:02:00+00:00"
    denied = await store.reserve_spend_guard(
        reservation_id="same-day-after-crash",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.1,
        stale_after_seconds=60,
    )
    assert denied["reason"] == "unknown_cost"
    status = await store.spend_guard_status(scope="local:aggregate")
    assert status["blocked_unknown"] is True

    clock["now"] = "2026-08-05T00:00:01+00:00"
    next_day = await store.reserve_spend_guard(
        reservation_id="next-day-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.4,
        stale_after_seconds=60,
    )
    assert next_day["execute_provider"] is True
    assert next_day["utc_day"] == "2026-08-05"


@pytest.mark.asyncio
async def test_attempt_id_replay_remains_idempotent_after_utc_rollover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = {"now": "2026-08-04T23:59:55+00:00"}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    store = PublicStateStore(tmp_path / "rollover-replay.db")
    first = await store.reserve_spend_guard(
        reservation_id="rollover-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_remaining=True,
        stale_after_seconds=60,
    )
    assert first["execute_provider"] is True

    clock["now"] = "2026-08-05T00:00:05+00:00"
    replay = await store.reserve_spend_guard(
        reservation_id="rollover-attempt",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_remaining=True,
        stale_after_seconds=60,
    )
    assert replay["utc_day"] == "2026-08-04"
    assert replay["admitted"] is True
    assert replay["execute_provider"] is False
    assert replay["reservation_mode"] == "remaining"
    assert replay["reserved_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_daily_exact_row_ceiling_fails_closed_at_scale_and_keeps_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ceiling = 128
    monkeypatch.setattr(
        state_module,
        "SPEND_GUARD_MAX_ROWS_PER_SCOPE_DAY",
        ceiling,
    )
    store = PublicStateStore(tmp_path / "bounded-guard.db")

    for index in range(ceiling):
        reservation_id = f"bounded-{index}"
        admission = await store.reserve_spend_guard(
            reservation_id=reservation_id,
            scope="local:aggregate",
            limit_usd=10.0,
            reserve_usd=0.01,
            stale_after_seconds=300,
        )
        assert admission["execute_provider"] is True
        await store.reconcile_spend_guard(
            reservation_id=reservation_id,
            actual_cost_usd=0.001,
        )

    denied = await store.reserve_spend_guard(
        reservation_id="bounded-overflow",
        scope="local:aggregate",
        limit_usd=10.0,
        reserve_usd=0.01,
        stale_after_seconds=300,
    )
    assert denied["admitted"] is False
    assert denied["execute_provider"] is False
    assert denied["reason"] == "ledger_capacity"

    # The ceiling applies only to new ids. Exact retries are looked up first and
    # therefore remain idempotent even when the scope/day is full.
    replay = await store.reserve_spend_guard(
        reservation_id="bounded-0",
        scope="local:aggregate",
        limit_usd=10.0,
        reserve_usd=0.01,
        stale_after_seconds=300,
    )
    assert replay["status"] == "reconciled"
    assert replay["execute_provider"] is False
    assert replay["reason"] == "reservation_exists"
    assert replay["committed_usd"] == pytest.approx(ceiling * 0.001)

    with sqlite3.connect(store.path) as connection:
        row_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM spend_guard_reservations
            WHERE scope='local:aggregate'
            """
        ).fetchone()
    assert row_count == (ceiling,)


@pytest.mark.asyncio
async def test_spend_ledger_survives_short_telemetry_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    utc_day = datetime.now(UTC).date().isoformat()
    clock = {"now": f"{utc_day}T10:00:00+00:00"}
    monkeypatch.setattr(state_module, "utc_now", lambda: clock["now"])
    monkeypatch.setenv("UNIGROK_STATE_RETENTION_HOURS", "1")
    store = PublicStateStore(tmp_path / "retention-guard.db")
    await store.reserve_spend_guard(
        reservation_id="retained-spend",
        scope="local:aggregate",
        limit_usd=1.0,
        reserve_usd=0.5,
        stale_after_seconds=300,
    )
    await store.reconcile_spend_guard(
        reservation_id="retained-spend",
        actual_cost_usd=0.4,
    )

    # Same-day reconciled spend remains accounting and replay evidence even when
    # its timestamp is older than the ordinary runtime retention window.
    clock["now"] = f"{utc_day}T12:00:00+00:00"
    await store.save_telemetry({"caller": "test", "cost_usd": 0.0})
    status = await store.spend_guard_status(scope="local:aggregate")
    assert status["committed_usd"] == pytest.approx(0.4)
    assert status["unknown_count"] == 0


@pytest.mark.asyncio
async def test_retention_prunes_only_prior_day_known_final_spend_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_STATE_RETENTION_HOURS", "1")
    store = PublicStateStore(tmp_path / "final-spend-retention.db")
    cases = (
        ("old-final", "scope:old-final", 0.1),
        ("old-pending", "scope:old-pending", "pending"),
        ("old-unknown", "scope:old-unknown", None),
        ("same-day-final", "scope:same-day-final", 0.2),
    )
    for reservation_id, scope, outcome in cases:
        await store.reserve_spend_guard(
            reservation_id=reservation_id,
            scope=scope,
            limit_usd=1.0,
            reserve_usd=0.25,
            stale_after_seconds=300,
        )
        if outcome != "pending":
            await store.reconcile_spend_guard(
                reservation_id=reservation_id,
                actual_cost_usd=outcome,
            )

    old_timestamp = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE spend_guard_reservations
            SET utc_day='2000-01-01', updated_at=?, reconciled_at=?
            WHERE reservation_id IN ('old-final', 'old-pending', 'old-unknown')
            """,
            (old_timestamp, old_timestamp),
        )
        connection.execute(
            """
            UPDATE spend_guard_reservations
            SET updated_at=?, reconciled_at=?
            WHERE reservation_id='same-day-final'
            """,
            (old_timestamp, old_timestamp),
        )
        connection.commit()

    assert await store.prune_retention() == 1
    with sqlite3.connect(store.path) as connection:
        retained = dict(
            connection.execute(
                "SELECT reservation_id, status FROM spend_guard_reservations"
            ).fetchall()
        )
    assert retained == {
        "old-pending": "pending",
        "old-unknown": "unknown",
        "same-day-final": "reconciled",
    }

    # Exact replay protection for a known final row is deliberately finite: after
    # its UTC day and configured retention both expire, the old id may be reused.
    after_retention = await store.reserve_spend_guard(
        reservation_id="old-final",
        scope="scope:old-final",
        limit_usd=1.0,
        reserve_usd=0.25,
        stale_after_seconds=300,
    )
    assert after_retention["execute_provider"] is True


@pytest.mark.asyncio
async def test_wrapper_blocks_unknown_and_records_single_call_overshoot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.0")
    store = PublicStateStore(tmp_path / "wrapper-guard.db")
    admission = await begin_local_spend_guard_attempt(
        store,
        attempt_id="provider-attempt",
        reserve_usd=0.4,
        stale_after_seconds=300,
    )
    assert admission is not None
    assert admission["execute_provider"] is True

    # The provider exceeded the local reservation. Reconciliation is honest;
    # local code could not prevent this one-call overshoot.
    outcome = await finish_local_spend_guard_attempt(
        store,
        attempt_id="provider-attempt",
        actual_cost_usd=1.2,
    )
    assert outcome["committed_usd"] == pytest.approx(1.2)
    with pytest.raises(CallerBudgetExceeded):
        await begin_local_spend_guard_attempt(
            store,
            attempt_id="after-overshoot",
            reserve_usd=0.1,
            stale_after_seconds=300,
        )

    second_store = PublicStateStore(tmp_path / "unknown-wrapper.db")
    await begin_local_spend_guard_attempt(
        second_store,
        attempt_id="unknown-provider-cost",
        reserve_usd=0.2,
        stale_after_seconds=300,
    )
    unknown = await finish_local_spend_guard_attempt(
        second_store,
        attempt_id="unknown-provider-cost",
        actual_cost_usd=None,
    )
    assert unknown["status"] == "unknown"
    with pytest.raises(CallerBudgetUnavailable):
        await begin_local_spend_guard_attempt(
            second_store,
            attempt_id="blocked-by-unknown",
            reserve_usd=0.1,
            stale_after_seconds=300,
        )


@pytest.mark.asyncio
async def test_unified_local_guard_defaults_to_remaining_and_unknown_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1.0")
    store = PublicStateStore(tmp_path / "unified-local.db")
    first = await begin_spend_guard_attempt(
        store,
        attempt_id="unified-local-first",
        stale_after_seconds=300,
    )
    assert first is not None
    assert first["guard_runtime"] == "local"
    assert first["reservation_mode"] == "remaining"
    assert first["reserved_usd"] == pytest.approx(1.0)

    await finish_spend_guard_attempt(
        store,
        attempt_id="unified-local-first",
        guard_scope=str(first["scope"]),
        actual_cost_usd=0.2,
    )
    second = await begin_spend_guard_attempt(
        store,
        attempt_id="unified-local-unknown",
        stale_after_seconds=300,
    )
    assert second is not None
    assert second["reserved_usd"] == pytest.approx(0.8)
    unknown = await finish_spend_guard_attempt(
        store,
        attempt_id="unified-local-unknown",
        guard_scope=str(second["scope"]),
        actual_cost_usd=None,
    )
    assert unknown["status"] == "unknown"

    with pytest.raises(CallerBudgetUnavailable):
        await begin_spend_guard_attempt(
            store,
            attempt_id="unified-local-blocked",
            stale_after_seconds=300,
        )
