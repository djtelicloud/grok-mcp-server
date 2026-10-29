"""Fail-closed daily spend caps for hosted callers and local operators."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
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
_LOCAL_SPEND_GUARD_ENV = "UNIGROK_LOCAL_DAILY_BUDGET_USD"
_LOCAL_SPEND_GUARD_SCOPE = "local:aggregate"
_SPEND_GUARD_MAX_USD = math.nextafter(
    ((1 << 63) - 1) / 10_000_000_000,
    -math.inf,
)
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
    if len( ledger_caller := principal_label(principal) or "" ) == 0:
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


class _SpendGuardPolicy(NamedTuple):
    runtime: str
    scope: str
    limit_usd: float
    telemetry_baseline_caller: str | None


def load_local_daily_spend_guard_limit() -> float | None:
    """Parse the optional local UTC-day admission-guard target."""
    raw = str(os.environ.get(_LOCAL_SPEND_GUARD_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        limit = float(raw)
    except ValueError as exc:
        raise CallerBudgetConfigurationError(
            "invalid_local_spend_guard_limit"
        ) from exc
    if not math.isfinite(limit) or limit < 0 or limit > _SPEND_GUARD_MAX_USD:
        raise CallerBudgetConfigurationError("invalid_local_spend_guard_limit")
    return limit


def _hosted_spend_guard_scope(principal: str) -> str:
    digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    return f"hosted:oauth-sha256:{digest}"


def _active_spend_guard_policy() -> _SpendGuardPolicy | None:
    if is_cloudrun_runtime():
        if _BUDGET_ENV not in os.environ:
            return None
        try:
            budgets = load_caller_budgets()
        except CallerBudgetConfigurationError:
            raise CallerBudgetUnavailable(
                "Hosted caller-budget configuration is invalid; spend was denied."
            ) from None
        principal = get_active_principal()
        if (
            not principal
            or principal_kind(principal) != "oauth"
            or not _is_configured_canonical_principal(principal)
        ):
            raise CallerBudgetUnavailable(
                "Hosted spend guard requires an exact authenticated OAuth principal."
            )
        limit = budgets.get(principal)
        if limit is None:
            return None
        telemetry_caller = principal_label(principal)
        if not telemetry_caller:
            raise CallerBudgetUnavailable(
                "Hosted caller-budget attribution is unavailable; spend was denied."
            )
        return _SpendGuardPolicy(
            "hosted",
            _hosted_spend_guard_scope(principal),
            limit,
            telemetry_caller,
        )

    try:
        limit = load_local_daily_spend_guard_limit()
    except CallerBudgetConfigurationError:
        raise CallerBudgetUnavailable(
            "Local spend-guard configuration is invalid; spend was denied."
        ) from None
    if limit is None:
        return None
    return _SpendGuardPolicy("local", _LOCAL_SPEND_GUARD_SCOPE, limit, None)


async def _begin_spend_guard_with_policy(
    store: PublicStateStore,
    *,
    policy: _SpendGuardPolicy,
    attempt_id: str,
    stale_after_seconds: int,
    reserve_usd: float | None,
) -> dict[str, Any]:
    try:
        admission = await store.reserve_spend_guard(
            reservation_id=attempt_id,
            scope=policy.scope,
            limit_usd=policy.limit_usd,
            reserve_usd=reserve_usd,
            reserve_remaining=reserve_usd is None,
            stale_after_seconds=stale_after_seconds,
            telemetry_baseline_caller=policy.telemetry_baseline_caller,
        )
    except Exception:
        raise CallerBudgetUnavailable(
            "Spend-guard ledger is unavailable; provider spend was denied."
        ) from None
    admission = dict(admission)
    admission["guard_runtime"] = policy.runtime
    if admission.get("admitted"):
        return admission
    if admission.get("reason") in {"limit_reached", "scope_busy"}:
        detail = (
            "another metered attempt is pending"
            if admission.get("reason") == "scope_busy"
            else "the daily balance is exhausted"
        )
        raise CallerBudgetExceeded(
            f"Daily spend guard declined the requested reservation because {detail} "
            f"(${float(admission['reserved_usd']):.6f} requested, "
            f"${float(admission['remaining_usd']):.6f} unreserved)."
        )
    if admission.get("reason") == "ledger_capacity":
        raise CallerBudgetUnavailable(
            "Spend-guard daily ledger capacity was reached; provider spend was denied."
        )
    raise CallerBudgetUnavailable(
        "Spend guard contains unresolved cost; provider spend was denied."
    )


async def begin_spend_guard_attempt(
    store: PublicStateStore,
    *,
    attempt_id: str,
    stale_after_seconds: int,
    reserve_usd: float | None = None,
) -> dict[str, Any] | None:
    policy = _active_spend_guard_policy()
    if policy is None:
        return None
    return await _begin_spend_guard_with_policy(
        store,
        policy=policy,
        attempt_id=attempt_id,
        stale_after_seconds=stale_after_seconds,
        reserve_usd=reserve_usd,
    )


async def finish_spend_guard_attempt(
    store: PublicStateStore,
    *,
    attempt_id: str,
    guard_scope: str,
    actual_cost_usd: float | None,
    unknown_reason: str = "provider_cost_unavailable",
) -> dict[str, Any]:
    policy = _active_spend_guard_policy()
    if policy is None or policy.scope != guard_scope:
        raise CallerBudgetUnavailable(
            "Spend-guard identity or configuration changed; reconciliation failed."
        )
    try:
        outcome = await store.reconcile_spend_guard(
            reservation_id=attempt_id,
            actual_cost_usd=actual_cost_usd,
            unknown_reason=unknown_reason,
            expected_scope=guard_scope,
        )
    except Exception:
        raise CallerBudgetUnavailable(
            "Spend-guard reconciliation failed; spend state is unresolved."
        ) from None
    result = dict(outcome)
    result["guard_runtime"] = policy.runtime
    return result


async def begin_local_spend_guard_attempt(
    store: PublicStateStore,
    *,
    attempt_id: str,
    reserve_usd: float,
    stale_after_seconds: int,
) -> dict[str, Any] | None:
    if is_cloudrun_runtime():
        return None
    limit = load_local_daily_spend_guard_limit()
    if limit is None:
        return None
    try:
        admission = await store.reserve_spend_guard(
            reservation_id=attempt_id,
            scope=_LOCAL_SPEND_GUARD_SCOPE,
            limit_usd=limit,
            reserve_usd=reserve_usd,
            stale_after_seconds=stale_after_seconds,
        )
    except Exception:
        raise CallerBudgetUnavailable(
            "Local spend guard is unavailable; provider spend was denied."
        ) from None
    if admission.get("admitted"):
        return admission
    if admission.get("reason") == "limit_reached":
        raise CallerBudgetExceeded(
            "Local daily spend guard declined the requested reservation "
            f"(${float(admission['reserved_usd']):.6f} requested, "
            f"${float(admission['remaining_usd']):.6f} unreserved)."
        )
    if admission.get("reason") == "ledger_capacity":
        raise CallerBudgetUnavailable(
            "Local spend-guard daily ledger capacity was reached; provider spend "
            "was denied."
        )
    raise CallerBudgetUnavailable(
        "Local spend guard contains unresolved cost; provider spend was denied."
    )


async def finish_local_spend_guard_attempt(
    store: PublicStateStore,
    *,
    attempt_id: str,
    actual_cost_usd: float | None,
    unknown_reason: str = "provider_cost_unavailable",
) -> dict[str, Any]:
    try:
        return await store.reconcile_spend_guard(
            reservation_id=attempt_id,
            actual_cost_usd=actual_cost_usd,
            unknown_reason=unknown_reason,
            expected_scope=_LOCAL_SPEND_GUARD_SCOPE,
        )
    except Exception:
        raise CallerBudgetUnavailable(
            "Local spend guard reconciliation failed; spend state is unresolved."
        ) from None


_BUDGET_DENIAL_MARKERS: tuple[str, ...] = (
    "caller budget",
    "daily budget",
    "monthly budget",
    "hourly request budget",
    "spend guard",
    "spend-guard",
    "budget exhausted",
    "budget reached",
    "declined the requested reservation",
)


def is_caller_budget_error(exc: BaseException | None) -> bool:
    """Return True when an exception indicates a caller-budget or spend-guard block."""
    if exc is None:
        return False
    if isinstance(exc, CallerBudgetError):
        return True
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if isinstance(cause, CallerBudgetError):
        return True
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _BUDGET_DENIAL_MARKERS)


async def inspect_caller_budget_pressure(
    store: PublicStateStore | None,
) -> dict[str, Any]:
    """Non-mutating preflight check of caller/local budget exhaustion for M2 routing."""
    if store is None:
        return {"exhausted": False, "reason": None, "source": None}
    if os.environ.get(_BUDGET_ENV, "").strip():
        try:
            budgets = load_caller_budgets()
            principal = get_active_principal()
            if principal and principal in budgets:
                limit = budgets[principal]
                ledger_caller = principal_label(principal) or ""
                if ledger_caller:
                    spent = float(await store.get_caller_cost_today(ledger_caller))
                    if spent >= limit:
                        return {
                            "exhausted": True,
                            "reason": "caller_daily_budget_exhausted",
                            "source": "hosted_principal_budget",
                        }
        except Exception:
            return {
                "exhausted": True,
                "reason": "caller_budget_ledger_unavailable",
                "source": "hosted_principal_budget",
            }
    if not is_cloudrun_runtime():
        try:
            local_limit = load_local_daily_budget()
            if local_limit is not None and local_limit <= 0.0:
                return {
                    "exhausted": True,
                    "reason": "local_daily_budget_exhausted",
                    "source": "local_spend_guard",
                }
        except Exception:
            return {
                "exhausted": True,
                "reason": "local_spend_guard_unavailable",
                "source": "local_spend_guard",
            }
    return {"exhausted": False, "reason": None, "source": None}


