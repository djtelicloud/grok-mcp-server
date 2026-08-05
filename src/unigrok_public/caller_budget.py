"""Fail-closed daily spend caps for hosted callers and local operators."""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from .identity import get_active_principal, principal_kind, principal_label
from .remote_auth import (
    authorization_servers,
    canonical_oauth_principal,
    is_cloudrun_runtime,
)

if TYPE_CHECKING:
    from .state import PublicStateStore

_BUDGET_ENV = "UNIGROK_CALLER_BUDGETS"
_LOCAL_BUDGET_ENV = "UNIGROK_LOCAL_DAILY_BUDGET_USD"
_MAX_BUDGET_BYTES = 65_536
_MAX_BUDGET_ENTRIES = 256
_MAX_PRINCIPAL_CHARS = 160
_LOCAL_BUDGET_LEASE_SECONDS = 900
_LOCAL_BUDGET_RETENTION_DAYS = 8


class CallerBudgetConfigurationError(ValueError):
    """The caller-budget configuration is malformed or cannot be enforced."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Caller budget configuration is invalid.")


class CallerBudgetError(RuntimeError):
    """Base class for a request rejected by budget enforcement."""


class CallerBudgetExceeded(CallerBudgetError):
    """The applicable caller or local daily cap has been reached."""


class CallerBudgetUnavailable(CallerBudgetError):
    """The configured budget cannot be evaluated safely, so spend is denied."""


@dataclass(frozen=True, slots=True)
class LocalBudgetReservation:
    day: str
    token: str
    limit_usd: float


def _is_configured_canonical_principal(principal: str) -> bool:
    parts = principal.split(":", 2)
    if len(parts) != 3 or parts[0] != "oauth":
        return False
    issuer = unquote(parts[1])
    subject = unquote(parts[2])
    if not issuer or not subject or issuer not in set(authorization_servers()):
        return False
    return canonical_oauth_principal(issuer, subject) == principal


def load_caller_budgets() -> dict[str, float]:
    """Parse the canonical OAuth-principal to daily-USD map."""
    raw = str(os.environ.get(_BUDGET_ENV, "") or "").strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > _MAX_BUDGET_BYTES:
        raise CallerBudgetConfigurationError("too_large")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for principal, limit in pairs:
            if principal in parsed:
                raise CallerBudgetConfigurationError("duplicate_principal")
            parsed[principal] = limit
        return parsed

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError:
        raise CallerBudgetConfigurationError("invalid_json") from None
    if not isinstance(document, dict):
        raise CallerBudgetConfigurationError("not_object")
    if not document:
        raise CallerBudgetConfigurationError("empty")
    if len(document) > _MAX_BUDGET_ENTRIES:
        raise CallerBudgetConfigurationError("too_many_entries")

    budgets: dict[str, float] = {}
    for principal, raw_limit in document.items():
        if (
            not isinstance(principal, str)
            or not principal
            or len(principal) > _MAX_PRINCIPAL_CHARS
            or principal != principal.strip()
            or any(ord(char) <= 31 or ord(char) == 127 for char in principal)
            or not _is_configured_canonical_principal(principal)
        ):
            raise CallerBudgetConfigurationError("invalid_principal")
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, float)):
            raise CallerBudgetConfigurationError("invalid_limit")
        limit = float(raw_limit)
        if not math.isfinite(limit) or limit < 0:
            raise CallerBudgetConfigurationError("invalid_limit")
        budgets[principal] = limit
    return budgets


def load_local_daily_budget() -> float | None:
    raw = str(os.environ.get(_LOCAL_BUDGET_ENV, "") or "").strip()
    if not raw:
        return None
    if len(raw) > 64:
        raise CallerBudgetConfigurationError("invalid_local_limit")
    try:
        limit = float(raw)
    except ValueError:
        raise CallerBudgetConfigurationError("invalid_local_limit") from None
    if not math.isfinite(limit) or limit < 0:
        raise CallerBudgetConfigurationError("invalid_local_limit")
    return limit


def validate_caller_budget_configuration() -> None:
    load_caller_budgets()
    load_local_daily_budget()


def _utc_day_window(now: datetime) -> tuple[str, str, str]:
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start.date().isoformat(), day_start.isoformat(), day_end.isoformat()


def _connect_local_budget(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _ensure_local_budget_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS local_daily_budget (
            day TEXT PRIMARY KEY,
            spent_usd REAL NOT NULL,
            lease_token TEXT,
            lease_expires_at TEXT,
            updated_at TEXT NOT NULL,
            CHECK(spent_usd >= 0),
            CHECK((lease_token IS NULL) = (lease_expires_at IS NULL))
        )
        """
    )


def _valid_cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    cost = float(value)
    if not math.isfinite(cost) or cost < 0:
        return None
    return cost


def _reserve_local_budget_sync(
    path: Path,
    reservation: LocalBudgetReservation,
    now_text: str,
    lease_expires_at: str,
    day_start: str,
    day_end: str,
    prune_before: str,
) -> LocalBudgetReservation:
    connection = _connect_local_budget(path)
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_local_budget_table(connection)
        connection.execute("DELETE FROM local_daily_budget WHERE day < ?", (prune_before,))
        row = connection.execute(
            "SELECT spent_usd, lease_token, lease_expires_at "
            "FROM local_daily_budget WHERE day=?",
            (reservation.day,),
        ).fetchone()
        if row is None:
            telemetry_row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM telemetry "
                "WHERE created_at>=? AND created_at<?",
                (day_start, day_end),
            ).fetchone()
            spent = _valid_cost(telemetry_row[0] if telemetry_row else 0.0)
            if spent is None:
                raise CallerBudgetUnavailable(
                    "Local daily budget telemetry is invalid; provider spend was denied."
                )
            connection.execute(
                "INSERT INTO local_daily_budget("
                "day, spent_usd, lease_token, lease_expires_at, updated_at"
                ") VALUES (?, ?, NULL, NULL, ?)",
                (reservation.day, spent, now_text),
            )
            lease_token = None
            lease_expiry = None
        else:
            spent = _valid_cost(row["spent_usd"])
            lease_token = str(row["lease_token"] or "") or None
            lease_expiry = str(row["lease_expires_at"] or "") or None
            if spent is None or bool(lease_token) != bool(lease_expiry):
                connection.execute(
                    "UPDATE local_daily_budget SET spent_usd=?, lease_token=NULL, "
                    "lease_expires_at=NULL, updated_at=? WHERE day=?",
                    (reservation.limit_usd, now_text, reservation.day),
                )
                connection.commit()
                committed = True
                raise CallerBudgetUnavailable(
                    "Local daily budget state is invalid; provider spend was denied."
                )

        if lease_token and lease_expiry:
            try:
                expiry = datetime.fromisoformat(lease_expiry)
                if expiry.tzinfo is None:
                    raise ValueError
            except ValueError:
                expiry = datetime.min.replace(tzinfo=UTC)
            now = datetime.fromisoformat(now_text)
            if expiry > now:
                raise CallerBudgetUnavailable(
                    "Another metered local operation is still active; provider spend was denied."
                )
            connection.execute(
                "UPDATE local_daily_budget SET spent_usd=?, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE day=?",
                (max(spent, reservation.limit_usd), now_text, reservation.day),
            )
            connection.commit()
            committed = True
            raise CallerBudgetExceeded(
                "A prior metered operation ended without an exact receipt; "
                "the local daily budget is exhausted."
            )

        if spent >= reservation.limit_usd:
            raise CallerBudgetExceeded(
                f"Local daily budget exhausted "
                f"(${spent:.6f}/${reservation.limit_usd:.6f})."
            )

        connection.execute(
            "UPDATE local_daily_budget SET lease_token=?, lease_expires_at=?, "
            "updated_at=? WHERE day=?",
            (
                reservation.token,
                lease_expires_at,
                now_text,
                reservation.day,
            ),
        )
        connection.commit()
        committed = True
        return reservation
    finally:
        if not committed and connection.in_transaction:
            connection.rollback()
        connection.close()


def _update_local_budget_sync(
    path: Path,
    reservation: LocalBudgetReservation,
    now_text: str,
    cost_usd: float | None,
    exhaust: bool,
    release: bool,
) -> float:
    connection = _connect_local_budget(path)
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_local_budget_table(connection)
        row = connection.execute(
            "SELECT spent_usd, lease_token FROM local_daily_budget WHERE day=?",
            (reservation.day,),
        ).fetchone()
        if row is None or str(row["lease_token"] or "") != reservation.token:
            raise CallerBudgetUnavailable(
                "Local daily budget reservation is unavailable; provider spend was denied."
            )
        spent = _valid_cost(row["spent_usd"])
        if spent is None:
            connection.execute(
                "UPDATE local_daily_budget SET spent_usd=?, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE day=?",
                (reservation.limit_usd, now_text, reservation.day),
            )
            connection.commit()
            committed = True
            raise CallerBudgetUnavailable(
                "Local daily budget state is invalid; provider spend was denied."
            )
        if release:
            updated = spent
        elif exhaust:
            updated = max(spent, reservation.limit_usd)
        else:
            if cost_usd is None:
                raise CallerBudgetUnavailable(
                    "Provider cost receipt is unavailable; provider spend was denied."
                )
            updated = spent + cost_usd
        connection.execute(
            "UPDATE local_daily_budget SET spent_usd=?, lease_token=NULL, "
            "lease_expires_at=NULL, updated_at=? WHERE day=?",
            (updated, now_text, reservation.day),
        )
        connection.commit()
        committed = True
        return updated
    finally:
        if not committed and connection.in_transaction:
            connection.rollback()
        connection.close()


async def _local_budget_io(operation: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(operation, *args)
    except CallerBudgetError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
        raise CallerBudgetUnavailable(
            "Local daily budget ledger is unavailable; provider spend was denied."
        ) from None


async def reserve_local_budget(
    store: PublicStateStore,
) -> LocalBudgetReservation | None:
    if is_cloudrun_runtime():
        return None
    limit = load_local_daily_budget()
    if limit is None:
        return None
    try:
        await store.initialize()
    except (OSError, sqlite3.Error):
        raise CallerBudgetUnavailable(
            "Local daily budget ledger is unavailable; provider spend was denied."
        ) from None
    now = datetime.now(UTC)
    day, day_start, day_end = _utc_day_window(now)
    reservation = LocalBudgetReservation(day, secrets.token_hex(16), limit)
    lease_expiry = (now + timedelta(seconds=_LOCAL_BUDGET_LEASE_SECONDS)).isoformat()
    prune_before = (now.date() - timedelta(days=_LOCAL_BUDGET_RETENTION_DAYS)).isoformat()
    return await _local_budget_io(
        _reserve_local_budget_sync,
        store.path,
        reservation,
        now.isoformat(),
        lease_expiry,
        day_start,
        day_end,
        prune_before,
    )


async def settle_local_budget(
    store: PublicStateStore,
    reservation: LocalBudgetReservation | None,
    result: dict[str, Any],
) -> None:
    if reservation is None:
        return
    cost = _valid_cost(result.get("cost_usd"))
    if cost is None:
        await _local_budget_io(
            _update_local_budget_sync,
            store.path,
            reservation,
            datetime.now(UTC).isoformat(),
            None,
            True,
            False,
        )
        raise CallerBudgetUnavailable(
            "Provider cost receipt is unavailable; the local daily budget was exhausted."
        )
    await _local_budget_io(
        _update_local_budget_sync,
        store.path,
        reservation,
        datetime.now(UTC).isoformat(),
        cost,
        False,
        False,
    )


async def settle_local_budget_error(
    store: PublicStateStore,
    reservation: LocalBudgetReservation | None,
    error: BaseException,
) -> None:
    if reservation is None:
        return
    attempts = getattr(error, "incurred_attempts", None)
    if not isinstance(attempts, (list, tuple)) or not attempts:
        return
    total = 0.0
    for attempt in attempts:
        if not isinstance(attempt, dict):
            total = -1.0
            break
        cost = _valid_cost(attempt.get("cost_usd"))
        if cost is None:
            total = -1.0
            break
        total += cost
    if total < 0 or not math.isfinite(total):
        await _local_budget_io(
            _update_local_budget_sync,
            store.path,
            reservation,
            datetime.now(UTC).isoformat(),
            None,
            True,
            False,
        )
        raise CallerBudgetUnavailable(
            "Provider error cost receipt is invalid; the local daily budget was exhausted."
        )
    await _local_budget_io(
        _update_local_budget_sync,
        store.path,
        reservation,
        datetime.now(UTC).isoformat(),
        total,
        False,
        False,
    )


async def release_local_budget(
    store: PublicStateStore,
    reservation: LocalBudgetReservation | None,
) -> None:
    if reservation is None:
        return
    await _local_budget_io(
        _update_local_budget_sync,
        store.path,
        reservation,
        datetime.now(UTC).isoformat(),
        0.0,
        False,
        True,
    )


async def enforce_caller_budget(store: PublicStateStore) -> None:
    """Reject hosted provider spend when the active principal is at its daily cap."""
    if not os.environ.get(_BUDGET_ENV, "").strip():
        return
    budgets = load_caller_budgets()
    principal = get_active_principal()
    if not principal or principal_kind(principal) != "oauth":
        raise CallerBudgetUnavailable(
            "Caller budget requires an authenticated OAuth principal."
        )
    limit = budgets.get(principal)
    if limit is None:
        return
    ledger_caller = principal_label(principal)
    if not ledger_caller:
        raise CallerBudgetUnavailable(
            "Caller budget attribution is unavailable; provider spend was denied."
        )
    try:
        spent = float(await store.get_caller_cost_today(ledger_caller))
    except Exception:
        raise CallerBudgetUnavailable(
            "Caller budget ledger is unavailable; provider spend was denied."
        ) from None
    if not math.isfinite(spent) or spent < 0:
        raise CallerBudgetUnavailable(
            "Caller budget ledger is invalid; provider spend was denied."
        )
    if spent >= limit:
        raise CallerBudgetExceeded(
            f"Daily caller budget exhausted (${spent:.6f}/${limit:.6f})."
        )
