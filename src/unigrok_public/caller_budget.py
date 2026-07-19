"""Fail-closed daily spend caps for authenticated hosted callers."""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from .identity import get_active_principal, principal_kind, principal_label
from .remote_auth import authorization_servers, canonical_oauth_principal

if TYPE_CHECKING:
    from .state import PublicStateStore

_BUDGET_ENV = "UNIGROK_CALLER_BUDGETS"
_MAX_BUDGET_BYTES = 65_536
_MAX_BUDGET_ENTRIES = 256
_MAX_PRINCIPAL_CHARS = 160


class CallerBudgetConfigurationError(ValueError):
    """The hosted caller-budget map is malformed or cannot be enforced."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Caller budget configuration is invalid.")


class CallerBudgetError(RuntimeError):
    """Base class for a request rejected by hosted budget enforcement."""


class CallerBudgetExceeded(CallerBudgetError):
    """The authenticated caller has reached its configured daily cap."""


class CallerBudgetUnavailable(CallerBudgetError):
    """The configured budget cannot be evaluated safely, so spend is denied."""


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
    """Startup validation hook for the hosted runtime."""
    load_caller_budgets()


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
