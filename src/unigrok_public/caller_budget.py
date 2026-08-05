"""Fail-closed daily spend caps for hosted callers and local API use."""

from __future__ import annotations

import json
import math
import os
import secrets
from dataclasses import dataclass
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
_LOCAL_BUDGET_LEASE_SECONDS = 900
_MAX_BUDGET_BYTES = 65_536
_MAX_BUDGET_ENTRIES = 256
_MAX_PRINCIPAL_CHARS = 160


class CallerBudgetConfigurationError(ValueError):
    """A caller-budget setting is malformed or cannot be enforced."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Caller budget configuration is invalid.")


class CallerBudgetError(RuntimeError):
    """Base class for a request rejected by budget enforcement."""


class CallerBudgetExceeded(CallerBudgetError):
    """The applicable daily cap has been reached."""


class CallerBudgetUnavailable(CallerBudgetError):
    """The configured budget cannot be evaluated safely, so spend is denied."""


@dataclass(slots=True)
class CallerBudgetReservation:
    store: Any | None = None
    token: str | None = None
    settled: bool = False

    async def settle(self, cost_usd: float) -> None:
        if self.settled:
            return
        if self.store is None or self.token is None:
            self.settled = True
            return
        try:
            cost = float(cost_usd)
        except (TypeError, ValueError, OverflowError):
            await self.exhaust()
            raise CallerBudgetUnavailable(
                "Provider cost was not safely reportable; the local daily budget was exhausted."
            ) from None
        if not math.isfinite(cost) or cost < 0:
            await self.exhaust()
            raise CallerBudgetUnavailable(
                "Provider cost was not safely reportable; the local daily budget was exhausted."
            )
        try:
            settled = bool(
                await self.store.settle_local_budget_reservation(
                    self.token,
                    cost_usd=cost,
                    exhaust=False,
                )
            )
        except Exception:
            raise CallerBudgetUnavailable(
                "Local daily budget settlement failed; further provider spend was denied."
            ) from None
        if not settled:
            raise CallerBudgetUnavailable(
                "Local daily budget reservation was lost; further provider spend was denied."
            )
        self.settled = True

    async def exhaust(self) -> None:
        if self.settled:
            return
        if self.store is None or self.token is None:
            self.settled = True
            return
        try:
            settled = bool(
                await self.store.settle_local_budget_reservation(
                    self.token,
                    cost_usd=0.0,
                    exhaust=True,
                )
            )
        except Exception:
            raise CallerBudgetUnavailable(
                "Local daily budget failure could not be recorded safely."
            ) from None
        if not settled:
            raise CallerBudgetUnavailable(
                "Local daily budget reservation was lost after an unknown provider outcome."
            )
        self.settled = True


def _is_configured_canonical_principal(principal: str) -> bool:
    parts = principal.split(":", 2)
    if len(parts) != 3 or parts[0] != "oauth":
        return False
    issuer = unquote(parts[1])
    subject = unquote(parts[2])
    if not issuer or not subject or issuer not in set(authorization_servers()):
        return False
    return canonical_oauth_principal(issuer, subject) == principal


def load_local_daily_budget() -> float | None:
    raw = str(os.environ.get(_LOCAL_BUDGET_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        limit = float(raw)
    except ValueError:
        raise CallerBudgetConfigurationError("invalid_local_limit") from None
    if not math.isfinite(limit) or limit < 0:
        raise CallerBudgetConfigurationError("invalid_local_limit")
    return limit


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


def validate_caller_budget_configuration() -> None:
    load_caller_budgets()
    load_local_daily_budget()


async def _enforce_local_budget(store: PublicStateStore) -> float | None:
    if is_cloudrun_runtime():
        return None
    limit = load_local_daily_budget()
    if limit is None:
        return None
    try:
        status = await store.get_local_budget_status(limit)
    except Exception:
        raise CallerBudgetUnavailable(
            "Local daily budget ledger is unavailable; provider spend was denied."
        ) from None
    state = str(status.get("status") or "")
    try:
        spent = float(status.get("spent_usd") or 0.0)
    except (TypeError, ValueError, OverflowError):
        raise CallerBudgetUnavailable(
            "Local daily budget ledger is invalid; provider spend was denied."
        ) from None
    if not math.isfinite(spent) or spent < 0:
        raise CallerBudgetUnavailable(
            "Local daily budget ledger is invalid; provider spend was denied."
        )
    if state == "busy":
        raise CallerBudgetUnavailable(
            "Another metered provider request is already in flight under the local daily budget."
        )
    if state not in {"ready", "exceeded"}:
        raise CallerBudgetUnavailable(
            "Local daily budget state is invalid; provider spend was denied."
        )
    if state == "exceeded" or spent >= limit:
        raise CallerBudgetExceeded(
            f"Local daily budget exhausted (${spent:.6f}/${limit:.6f})."
        )
    return limit


async def enforce_caller_budget(store: PublicStateStore) -> None:
    """Reject provider spend when a configured local or hosted cap is exhausted."""
    await _enforce_local_budget(store)
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


async def reserve_caller_budget(store: PublicStateStore) -> CallerBudgetReservation:
    await enforce_caller_budget(store)
    if is_cloudrun_runtime():
        return CallerBudgetReservation()
    limit = load_local_daily_budget()
    if limit is None:
        return CallerBudgetReservation()
    token = secrets.token_hex(16)
    try:
        status = await store.acquire_local_budget_reservation(
            limit_usd=limit,
            token=token,
            lease_seconds=_LOCAL_BUDGET_LEASE_SECONDS,
        )
    except Exception:
        raise CallerBudgetUnavailable(
            "Local daily budget reservation failed; provider spend was denied."
        ) from None
    state = str(status.get("status") or "")
    try:
        spent = float(status.get("spent_usd") or 0.0)
    except (TypeError, ValueError, OverflowError):
        spent = float("nan")
    if state == "exceeded" or (math.isfinite(spent) and spent >= limit):
        raise CallerBudgetExceeded(
            f"Local daily budget exhausted (${spent:.6f}/${limit:.6f})."
        )
    if state != "acquired":
        raise CallerBudgetUnavailable(
            "Another metered provider request is already in flight under the local daily budget."
        )
    return CallerBudgetReservation(store=store, token=token)
