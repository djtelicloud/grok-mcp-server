"""Hosted caller caps and an independent local spend-admission guard."""

from __future__ import annotations

import hashlib
import json
import math
import os
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
_LOCAL_SPEND_GUARD_ENV = "UNIGROK_LOCAL_DAILY_BUDGET_USD"
_LOCAL_SPEND_GUARD_SCOPE = "local:aggregate"
# The exact SQLite tick maximum rounds upward when represented as a float.
# Step down once so every accepted configuration remains representable when
# the state layer converts it back to exact ticks.
_SPEND_GUARD_MAX_USD = math.nextafter(
    ((1 << 63) - 1) / 10_000_000_000,
    -math.inf,
)
_MAX_BUDGET_BYTES = 65_536
_MAX_BUDGET_ENTRIES = 256
_MAX_PRINCIPAL_CHARS = 160


class CallerBudgetConfigurationError(ValueError):
    """A caller-budget or local spend-guard setting is malformed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Caller budget configuration is invalid.")


class CallerBudgetError(RuntimeError):
    """Base class for a request rejected by hosted budget enforcement."""


class CallerBudgetExceeded(CallerBudgetError):
    """The authenticated caller has reached its configured daily cap."""


class CallerBudgetUnavailable(CallerBudgetError):
    """The configured budget cannot be evaluated safely, so spend is denied."""


class _SpendGuardPolicy(NamedTuple):
    runtime: str
    scope: str
    limit_usd: float
    telemetry_baseline_caller: str | None


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
    """Parse the canonical OAuth-principal to daily-USD map.

    An absent variable intentionally means that hosted caller caps are disabled.
    Once present, every structural or semantic error rejects the whole map.
    """
    if _BUDGET_ENV not in os.environ:
        return {}
    raw = str(os.environ.get(_BUDGET_ENV, "") or "").strip()
    if not raw:
        raise CallerBudgetConfigurationError("empty")
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
        if (
            not math.isfinite(limit)
            or limit < 0
            or limit > _SPEND_GUARD_MAX_USD
        ):
            raise CallerBudgetConfigurationError("invalid_limit")
        budgets[principal] = limit
    return budgets


def load_local_daily_spend_guard_limit() -> float | None:
    """Parse the optional local UTC-day admission-guard target.

    This value is not a provider-enforced upper bound. The guard reserves a
    caller-declared amount before each provider attempt and reconciles actual
    cost afterward; one attempt can still exceed its reservation.
    """
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


def validate_caller_budget_configuration() -> None:
    """Startup validation hook for hosted caps and the local admission guard."""
    load_caller_budgets()
    load_local_daily_spend_guard_limit()


def _hosted_spend_guard_scope(principal: str) -> str:
    digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    return f"hosted:oauth-sha256:{digest}"


def _active_spend_guard_policy() -> _SpendGuardPolicy | None:
    """Resolve the current runtime's exact spend policy without raw identity output."""
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
            # A valid configuration intentionally omits uncapped principals.
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
    """Begin one hosted or local metered attempt under its UTC-day guard.

    The default reserves the scope's entire remaining balance atomically, which
    serializes metered calls for that scope. Supplying ``reserve_usd`` preserves
    fixed-reservation behavior. Either reservation is only an admission guard;
    one provider call can still exceed it without a provider-enforced ceiling.

    ``None`` means that runtime's guard is disabled or a valid hosted map omitted
    the exact active principal. Only ``execute_provider is True`` authorizes a call.
    """
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
    """Finish a unified attempt, binding reconciliation to runtime and identity.

    The scope must be the exact value returned by ``begin_spend_guard_attempt``.
    ``None`` cost is durable and fail-closed; it must never be coerced to zero.
    """
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
    """Atomically reserve one local metered attempt, or return None when disabled.

    Only a result with ``execute_provider=true`` authorizes a provider call.
    Reusing an attempt id is idempotent and returns ``execute_provider=false``;
    the integration must attach to/replay the stored outcome, never call again.

    This is intentionally named a guard. Without a provider-enforced per-call
    upper bound, local code cannot prevent one call from costing more than the
    declared reservation or the remaining daily dollars.
    """
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
    """Idempotently reconcile an admitted attempt to known or unknown cost.

    ``None`` records durable unknown spend and blocks later admissions for the
    same UTC day. A later authoritative amount can resolve that state.
    """
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


async def enforce_caller_budget(store: PublicStateStore) -> None:
    """Reject provider spend when the active principal is at its daily cap.

    The hot path is a no-op when the environment variable is absent. If caps
    are configured, missing authenticated context and ledger failures deny the
    request rather than silently re-enabling owner spend. Principals omitted
    from a valid map retain the historical uncapped behavior.
    """
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
