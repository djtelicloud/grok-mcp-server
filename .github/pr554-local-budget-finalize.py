from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")


caller_budget = Path("src/unigrok_public/caller_budget.py")
replace_once(
    caller_budget,
    "import secrets\nfrom dataclasses import dataclass\n",
    "import secrets\nfrom contextvars import ContextVar\nfrom dataclasses import dataclass\n",
)
replace_once(
    caller_budget,
    '''@dataclass(slots=True)
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


''',
    '''@dataclass(slots=True)
class CallerBudgetReservation:
    store: Any | None = None
    token: str | None = None
    settled: bool = False
    context_token: Any | None = None

    def _release_context(self) -> None:
        token = self.context_token
        if token is None:
            return
        self.context_token = None
        _ACTIVE_LOCAL_BUDGET_RESERVATION.reset(token)

    async def settle(self, cost_usd: float) -> None:
        if self.settled:
            return
        if self.store is None or self.token is None:
            self.settled = True
            return
        try:
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
        finally:
            self._release_context()

    async def exhaust(self) -> None:
        if self.settled:
            return
        if self.store is None or self.token is None:
            self.settled = True
            return
        try:
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
        finally:
            self._release_context()


_ACTIVE_LOCAL_BUDGET_RESERVATION: ContextVar[CallerBudgetReservation | None] = (
    ContextVar("unigrok_active_local_budget_reservation", default=None)
)


''',
)
replace_once(
    caller_budget,
    '''async def reserve_caller_budget(store: PublicStateStore) -> CallerBudgetReservation:
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
''',
    '''async def reserve_caller_budget(store: PublicStateStore) -> CallerBudgetReservation:
    active = _ACTIVE_LOCAL_BUDGET_RESERVATION.get()
    if active is not None and not active.settled:
        return CallerBudgetReservation()
    if active is not None:
        _ACTIVE_LOCAL_BUDGET_RESERVATION.set(None)
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
    reservation = CallerBudgetReservation(store=store, token=token)
    reservation.context_token = _ACTIVE_LOCAL_BUDGET_RESERVATION.set(reservation)
    return reservation
''',
)

server = Path("src/unigrok_public/server.py")
replace_once(
    server,
    '''async def _guarded_provider_call(
    plane: str,
    model: str | None,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run one provider operation through the shared circuit breaker."""
    reservation = None
    if plane == "api":
        await enforce_caller_budget(STATE)
        reservation = await reserve_caller_budget(STATE)
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
''',
    '''async def _guarded_provider_call(
    plane: str,
    model: str | None,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run one provider operation through the shared circuit breaker."""
    reservation = None
    if plane == "api":
        await enforce_caller_budget(STATE)
    admission = _breaker_before_call(plane, model)
    if plane == "api":
        try:
            reservation = await reserve_caller_budget(STATE)
        except Exception:
            _breaker_abandon_probe(admission)
            raise
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
''',
)
replace_once(
    server,
    '''        reservation = None
        if target == "api":
            _require_metered_api_enabled()
            await enforce_caller_budget(STATE)
            reservation = await reserve_caller_budget(STATE)
        admission = _breaker_before_call(target, target_model)
        capability_unavailable = False
''',
    '''        reservation = None
        if target == "api":
            _require_metered_api_enabled()
            await enforce_caller_budget(STATE)
        admission = _breaker_before_call(target, target_model)
        if target == "api":
            try:
                reservation = await reserve_caller_budget(STATE)
            except Exception:
                _breaker_abandon_probe(admission)
                raise
        capability_unavailable = False
''',
)

tests = Path("tests/test_local_daily_budget.py")
replace_once(
    tests,
    '''import pytest

from unigrok_public.caller_budget import (
''',
    '''import pytest

from unigrok_public import server
from unigrok_public.caller_budget import (
''',
)
with tests.open("a", encoding="utf-8") as handle:
    handle.write(
        '''

@pytest.mark.asyncio
async def test_nested_local_budget_reservation_reuses_outer_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1")
    store = PublicStateStore(tmp_path / "budget.db")

    outer = await reserve_caller_budget(store)
    inner = await reserve_caller_budget(store)

    assert outer.store is store
    assert inner.store is None
    await inner.exhaust()
    status = await store.get_local_budget_status(1.0)
    assert status["status"] == "busy"

    await outer.settle(0.4)
    replacement = await reserve_caller_budget(store)
    assert replacement.store is store
    await replacement.settle(0.1)


@pytest.mark.asyncio
async def test_open_breaker_does_not_strand_local_budget_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNIGROK_LOCAL_DAILY_BUDGET_USD", "1")
    store = PublicStateStore(tmp_path / "budget.db")
    monkeypatch.setattr(server, "STATE", store)

    def reject(_plane: str, _model: str | None):
        raise RuntimeError("circuit breaker open")

    monkeypatch.setattr(server, "_breaker_before_call", reject)
    called = False

    async def operation() -> dict[str, object]:
        nonlocal called
        called = True
        return {"cost_usd": 0.0}

    with pytest.raises(RuntimeError, match="circuit breaker open"):
        await server._guarded_provider_call("api", "grok-test", operation)

    assert called is False
    status = await store.get_local_budget_status(1.0)
    assert status["status"] == "ready"
    reservation = await reserve_caller_budget(store)
    assert reservation.store is store
    await reservation.settle(0.0)
'''
    )
