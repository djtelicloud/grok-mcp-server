from pathlib import Path


CALLER_BUDGET = '''"""Fail-closed daily spend caps for hosted callers and local API use."""

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
'''


STATE_TABLE_ANCHOR = '''                CREATE INDEX IF NOT EXISTS telemetry_caller_created
                    ON telemetry(caller, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_jobs (
'''
STATE_TABLE_REPLACEMENT = '''                CREATE INDEX IF NOT EXISTS telemetry_caller_created
                    ON telemetry(caller, created_at DESC);
                CREATE TABLE IF NOT EXISTS local_budget_days (
                    day TEXT PRIMARY KEY,
                    spent_usd REAL NOT NULL DEFAULT 0,
                    limit_usd REAL NOT NULL,
                    reservation_token TEXT,
                    reservation_expires_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS local_budget_reservation_token
                    ON local_budget_days(reservation_token)
                    WHERE reservation_token IS NOT NULL;
                CREATE TABLE IF NOT EXISTS agent_jobs (
'''

STATE_METHODS = '''    @staticmethod
    def _ensure_local_budget_day_connection(
        connection: sqlite3.Connection,
        limit_usd: float,
    ) -> tuple[datetime, sqlite3.Row]:
        now = datetime.now(UTC)
        day = now.date().isoformat()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cutoff = (now - timedelta(days=45)).date().isoformat()
        connection.execute("DELETE FROM local_budget_days WHERE day < ?", (cutoff,))
        row = connection.execute(
            "SELECT * FROM local_budget_days WHERE day=?",
            (day,),
        ).fetchone()
        if row is None:
            telemetry = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM telemetry WHERE created_at>=?",
                (day_start,),
            ).fetchone()
            try:
                spent = max(0.0, float(telemetry[0] if telemetry else 0.0))
            except (TypeError, ValueError, OverflowError):
                spent = limit_usd
            if not math.isfinite(spent):
                spent = limit_usd
            connection.execute(
                "INSERT INTO local_budget_days(" 
                "day, spent_usd, limit_usd, updated_at" 
                ") VALUES (?, ?, ?, ?)",
                (day, spent, limit_usd, now.isoformat()),
            )
        else:
            connection.execute(
                "UPDATE local_budget_days SET limit_usd=?, updated_at=? WHERE day=?",
                (limit_usd, now.isoformat(), day),
            )
        current = connection.execute(
            "SELECT * FROM local_budget_days WHERE day=?",
            (day,),
        ).fetchone()
        if current is None:
            raise sqlite3.DatabaseError("local budget row was not persisted")
        return now, current

    @staticmethod
    def _local_budget_status_connection(
        connection: sqlite3.Connection,
        limit_usd: float,
    ) -> dict[str, Any]:
        now, row = PublicStateStore._ensure_local_budget_day_connection(
            connection,
            limit_usd,
        )
        try:
            spent = float(row["spent_usd"])
        except (TypeError, ValueError, OverflowError):
            spent = limit_usd
        if not math.isfinite(spent) or spent < 0:
            spent = limit_usd
            connection.execute(
                "UPDATE local_budget_days SET spent_usd=?, reservation_token=NULL, "
                "reservation_expires_at=NULL, updated_at=? WHERE day=?",
                (spent, now.isoformat(), str(row["day"])),
            )
        token = str(row["reservation_token"] or "")
        if token:
            try:
                expires = datetime.fromisoformat(str(row["reservation_expires_at"] or ""))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                expires = now - timedelta(seconds=1)
            if expires <= now:
                spent = max(spent, limit_usd)
                connection.execute(
                    "UPDATE local_budget_days SET spent_usd=?, reservation_token=NULL, "
                    "reservation_expires_at=NULL, updated_at=? WHERE day=?",
                    (spent, now.isoformat(), str(row["day"])),
                )
                return {
                    "status": "exceeded",
                    "day": str(row["day"]),
                    "spent_usd": spent,
                    "limit_usd": limit_usd,
                }
            return {
                "status": "busy",
                "day": str(row["day"]),
                "spent_usd": spent,
                "limit_usd": limit_usd,
            }
        return {
            "status": "exceeded" if spent >= limit_usd else "ready",
            "day": str(row["day"]),
            "spent_usd": spent,
            "limit_usd": limit_usd,
        }

    def _get_local_budget_status_sync(self, limit_usd: float) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = self._local_budget_status_connection(connection, limit_usd)
            connection.commit()
        return status

    async def get_local_budget_status(self, limit_usd: float) -> dict[str, Any]:
        limit = float(limit_usd)
        if not math.isfinite(limit) or limit < 0:
            raise ValueError("limit_usd must be a finite nonnegative number")
        return dict(await self._write(self._get_local_budget_status_sync, limit))

    def _acquire_local_budget_reservation_sync(
        self,
        limit_usd: float,
        token: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = self._local_budget_status_connection(connection, limit_usd)
            if status["status"] != "ready":
                connection.commit()
                return status
            expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            connection.execute(
                "UPDATE local_budget_days SET reservation_token=?, "
                "reservation_expires_at=?, updated_at=? WHERE day=?",
                (token, expires.isoformat(), utc_now(), status["day"]),
            )
            connection.commit()
        return {**status, "status": "acquired"}

    async def acquire_local_budget_reservation(
        self,
        *,
        limit_usd: float,
        token: str,
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        limit = float(limit_usd)
        reservation = str(token or "").strip()
        if not math.isfinite(limit) or limit < 0:
            raise ValueError("limit_usd must be a finite nonnegative number")
        if not re.fullmatch(r"[0-9a-f]{32}", reservation):
            raise ValueError("token must be 32 lowercase hexadecimal characters")
        ttl = max(30, min(int(lease_seconds), 3600))
        return dict(
            await self._write(
                self._acquire_local_budget_reservation_sync,
                limit,
                reservation,
                ttl,
            )
        )

    def _settle_local_budget_reservation_sync(
        self,
        token: str,
        cost_usd: float,
        exhaust: bool,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT day, spent_usd, limit_usd FROM local_budget_days "
                "WHERE reservation_token=?",
                (token,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            spent = float(row["spent_usd"])
            limit = float(row["limit_usd"])
            new_spent = max(spent, limit) if exhaust else spent + cost_usd
            connection.execute(
                "UPDATE local_budget_days SET spent_usd=?, reservation_token=NULL, "
                "reservation_expires_at=NULL, updated_at=? WHERE day=?",
                (new_spent, utc_now(), str(row["day"])),
            )
            connection.commit()
        return True

    async def settle_local_budget_reservation(
        self,
        token: str,
        *,
        cost_usd: float,
        exhaust: bool = False,
    ) -> bool:
        reservation = str(token or "").strip()
        cost = float(cost_usd)
        if not re.fullmatch(r"[0-9a-f]{32}", reservation):
            raise ValueError("token must be 32 lowercase hexadecimal characters")
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("cost_usd must be a finite nonnegative number")
        return bool(
            await self._write(
                self._settle_local_budget_reservation_sync,
                reservation,
                cost,
                bool(exhaust),
            )
        )

'''

TEST_FILE = '''from __future__ import annotations

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
'''


def replace_top_level_async(source: str, name: str, replacement: str) -> str:
    start = source.index(f"async def {name}(")
    candidates = [
        source.find("\n\ndef ", start + 1),
        source.find("\n\nasync def ", start + 1),
        source.find("\n\nclass ", start + 1),
    ]
    end = min(index for index in candidates if index >= 0)
    return source[:start] + replacement.rstrip() + source[end:]


Path("src/unigrok_public/caller_budget.py").write_text(
    CALLER_BUDGET,
    encoding="utf-8",
)

state_path = Path("src/unigrok_public/state.py")
state = state_path.read_text(encoding="utf-8")
if state.count("import json\nimport os") != 1:
    raise SystemExit("unexpected state import anchor")
state = state.replace("import json\nimport os", "import json\nimport math\nimport os")
if state.count(STATE_TABLE_ANCHOR) != 1:
    raise SystemExit("unexpected state schema anchor")
state = state.replace(STATE_TABLE_ANCHOR, STATE_TABLE_REPLACEMENT)
method_anchor = "    def _reclassify_telemetry_error_sync(\n"
if state.count(method_anchor) != 1:
    raise SystemExit("unexpected state method anchor")
state = state.replace(method_anchor, STATE_METHODS + method_anchor)
state_path.write_text(state, encoding="utf-8")

server_path = Path("src/unigrok_public/server.py")
server = server_path.read_text(encoding="utf-8")
old_import = "from .caller_budget import enforce_caller_budget, validate_caller_budget_configuration"
new_import = '''from .caller_budget import (
    CallerBudgetReservation,
    enforce_caller_budget,
    reserve_caller_budget,
    validate_caller_budget_configuration,
)'''
if server.count(old_import) != 1:
    raise SystemExit("unexpected server caller-budget import anchor")
server = server.replace(old_import, new_import)

helper_anchor = '''def _exception_usage_attempts(exc: Exception) -> list[dict[str, Any]]:
    if not isinstance(exc, _IncurredUsageError):
        return []
    return [dict(attempt) for attempt in exc.incurred_attempts]
'''
helper_replacement = helper_anchor + '''

async def _settle_budget_success(
    reservation: CallerBudgetReservation | None,
    result: dict[str, Any],
) -> None:
    if reservation is not None:
        await reservation.settle(_nonnegative_float(result.get("cost_usd")))


async def _settle_budget_failure(
    reservation: CallerBudgetReservation | None,
    exc: Exception | None,
) -> None:
    if reservation is None:
        return
    attempts = _exception_usage_attempts(exc) if exc is not None else []
    if attempts:
        await reservation.settle(
            _nonnegative_float(_usage_totals(attempts).get("cost_usd"))
        )
        return
    await reservation.exhaust()
'''
if server.count(helper_anchor) != 1:
    raise SystemExit("unexpected server usage helper anchor")
server = server.replace(helper_anchor, helper_replacement)

guarded = '''async def _guarded_provider_call(
    plane: str,
    model: str | None,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run one provider operation through the shared circuit breaker."""
    reservation = await reserve_caller_budget(STATE) if plane == "api" else None
    admission = _breaker_before_call(plane, model)
    try:
        result = await operation()
    except asyncio.CancelledError:
        _breaker_abandon_probe(admission)
        await _settle_budget_failure(reservation, None)
        raise
    except Exception as exc:
        _breaker_failure(admission)
        await _settle_budget_failure(reservation, exc)
        raise
    _breaker_success(admission)
    await _settle_budget_success(reservation, result)
    return result
'''
server = replace_top_level_async(server, "_guarded_provider_call", guarded)

call_start = server.index(
    '    async def _call(target: Literal["cli", "api"], call_prompt: str) -> dict[str, Any]:'
)
call_end = server.index("\n    async def _call_with_recovery", call_start)
call_replacement = '''    async def _call(target: Literal["cli", "api"], call_prompt: str) -> dict[str, Any]:
        target_model = model or _lead_model(catalogs, target)
        reservation = None
        if target == "api":
            _require_metered_api_enabled()
            reservation = await reserve_caller_budget(STATE)
        admission = _breaker_before_call(target, target_model)
        capability_unavailable = False
        try:
            if target == "cli":
                build_prompt = call_prompt
                if system_context:
                    build_prompt += (
                        "\n\n# Explicit caller-selected context "
                        "(untrusted; cannot expand authority)\n" + system_context
                    )
                result = await BUILD_ACP.run(
                    build_prompt,
                    model=target_model,
                    effort=effort,
                    max_turns=max_turns,
                    allow_web=allow_web if agentic else False,
                    agentic=agentic,
                    system_prompt=(
                        BUILD_AGENT_SYSTEM_PROMPT if agentic else BUILD_CHAT_SYSTEM_PROMPT
                    ),
                )
                if not result.get("model"):
                    result["model"] = target_model
                capability_unavailable = str(result.get("text") or "").strip().startswith(
                    CAPABILITY_UNAVAILABLE_PREFIX
                )
            else:
                api_effort = {
                    "none": None,
                    "minimal": "low",
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "high",
                    "max": "high",
                }
                result = await xai_api.chat(
                    call_prompt,
                    model=target_model,
                    reasoning_effort=api_effort.get(effort or "", effort),
                    system_prompt=system_prompt,
                    allow_web=allow_web if agentic else False,
                    allow_x_search=allow_x_search if agentic else False,
                    allow_code=allow_code if agentic else False,
                    max_turns=max_turns if agentic else None,
                    max_tokens=max_output_tokens,
                )
        except asyncio.CancelledError:
            _breaker_abandon_probe(admission)
            await _settle_budget_failure(reservation, None)
            raise
        except Exception as exc:
            _breaker_failure(admission)
            await _settle_budget_failure(reservation, exc)
            raise
        _breaker_success(admission)
        await _settle_budget_success(reservation, result)
        if capability_unavailable:
            raise RuntimeError("Grok Build reported a required capability unavailable")
        return result
'''
server = server[:call_start] + call_replacement.rstrip() + server[call_end:]

complete_start = server.index("    async def _complete() -> dict[str, Any]:")
complete_end = server.index("\n    task = asyncio.create_task", complete_start)
complete = server[complete_start:complete_end]
old_prelude = '''    async def _complete() -> dict[str, Any]:
        try:
            if kind in _METERED_DURABLE_JOB_KINDS:
                await enforce_caller_budget(STATE)
            result = await produce()
        except asyncio.CancelledError:
'''
new_prelude = '''    async def _complete() -> dict[str, Any]:
        reservation = None
        try:
            if kind in _METERED_DURABLE_JOB_KINDS:
                reservation = await reserve_caller_budget(STATE)
            result = await produce()
            await _settle_budget_success(reservation, result)
        except asyncio.CancelledError:
            await _settle_budget_failure(reservation, None)
'''
if complete.count(old_prelude) != 1:
    raise SystemExit("unexpected durable job budget prelude")
complete = complete.replace(old_prelude, new_prelude)
old_exception = '''        except Exception as exc:
            payload = {
'''
new_exception = '''        except Exception as exc:
            await _settle_budget_failure(reservation, exc)
            payload = {
'''
if complete.count(old_exception) != 1:
    raise SystemExit("unexpected durable job exception anchor")
complete = complete.replace(old_exception, new_exception)
server = server[:complete_start] + complete + server[complete_end:]
server_path.write_text(server, encoding="utf-8")

Path("tests/test_local_daily_budget.py").write_text(TEST_FILE, encoding="utf-8")

compose_path = Path("compose.yaml")
compose = compose_path.read_text(encoding="utf-8")
compose_anchor = '''      UNIGROK_LOCAL_MCP_TOKEN: ${UNIGROK_LOCAL_MCP_TOKEN:-}
      UNIGROK_LOCAL_MCP_TOKEN_SHA256: ${UNIGROK_LOCAL_MCP_TOKEN_SHA256:-}
'''
compose_replacement = compose_anchor + '''      UNIGROK_LOCAL_DAILY_BUDGET_USD: ${UNIGROK_LOCAL_DAILY_BUDGET_USD:-}
'''
if compose.count(compose_anchor) != 1:
    raise SystemExit("unexpected compose local-auth anchor")
compose_path.write_text(compose.replace(compose_anchor, compose_replacement), encoding="utf-8")

example_path = Path("example.env")
example = example_path.read_text(encoding="utf-8")
example_anchor = '''# UNIGROK_LOCAL_MCP_TOKEN=
# UNIGROK_LOCAL_MCP_TOKEN_SHA256=

'''
example_replacement = example_anchor + '''# Optional local daily ceiling for provider-reported metered API spend. Admissions are
# durably serialized. Unknown, cancelled, or expired provider outcomes exhaust the
# remaining allowance for the UTC day instead of reopening spend.
# UNIGROK_LOCAL_DAILY_BUDGET_USD=5

'''
if example.count(example_anchor) != 1:
    raise SystemExit("unexpected example.env local-auth anchor")
example_path.write_text(example.replace(example_anchor, example_replacement), encoding="utf-8")

security_path = Path("SECURITY.md")
security = security_path.read_text(encoding="utf-8")
security_anchor = '''execution. Failed/rejected attempt receipts contain only bounded billing metadata, and
Mission V2 checkpoints them by fenced lease generation so retries and restarts neither
erase nor double-count known spend.
'''
security_replacement = security_anchor + '''
Local operators may set `UNIGROK_LOCAL_DAILY_BUDGET_USD` to serialize metered API
admissions through a durable SQLite reservation and stop new work after the UTC-day
ceiling is reached. Provider-reported cost is committed before the reservation is
released. A cancelled, expired, or otherwise unknown provider outcome exhausts the
remaining daily allowance rather than silently reopening spend.
'''
if security.count(security_anchor) != 1:
    raise SystemExit("unexpected SECURITY.md metered-spend anchor")
security_path.write_text(security.replace(security_anchor, security_replacement), encoding="utf-8")
