"""Unit tests for bounded runtime hydration."""

import asyncio

import pytest

from src.hydration import (
    HydrationResult,
    get_hydration_service,
    reset_hydration_services,
)


class FakeStore:
    def __init__(self, cost_to_return=1.5, should_fail=False):
        self.cost_to_return = cost_to_return
        self.should_fail = should_fail
        self.get_caller_cost_today_calls = 0

    async def get_caller_cost_today(self, caller_principal: str) -> float:
        self.get_caller_cost_today_calls += 1
        if self.should_fail:
            raise RuntimeError("store offline")
        return self.cost_to_return


class FakeHook:
    def __init__(self, name, scope, gate=None):
        self.name = name
        self.scope = scope
        self.gate = gate
        self.calls = 0
        self.last_ctx = None

    async def hydrate(self, store, ctx):
        self.calls += 1
        self.last_ctx = ctx
        if self.gate is not None:
            await self.gate.wait()
        await store.get_caller_cost_today("test")
        return HydrationResult()


@pytest.fixture
def service():
    reset_hydration_services()
    return get_hydration_service(FakeStore())


def test_service_is_bound_to_store_instance():
    reset_hydration_services()
    first_store = FakeStore()
    second_store = FakeStore()

    first = get_hydration_service(first_store)

    assert get_hydration_service(first_store) is first
    assert get_hydration_service(second_store) is not first


@pytest.mark.asyncio
async def test_process_day_idempotency(service):
    hook = FakeHook("day_hook", "process_day")
    service.register(hook)

    await service.hydrate_process_day()
    await service.hydrate_process_day()

    assert hook.calls == 1


@pytest.mark.asyncio
async def test_process_lifetime_idempotency(service):
    hook = FakeHook("lifetime_hook", "process_lifetime")
    service.register(hook)

    await service.hydrate_process_lifetime()
    await service.hydrate_process_lifetime()

    assert hook.calls == 1


@pytest.mark.asyncio
async def test_session_idempotency(service):
    hook = FakeHook("session_hook", "session")
    service.register(hook)

    await service.hydrate_session("sess-1")
    await service.hydrate_session("sess-1")
    await service.hydrate_session("sess-2")

    assert hook.calls == 2


@pytest.mark.asyncio
async def test_concurrent_first_use_runs_hook_once(service):
    gate = asyncio.Event()
    hook = FakeHook("concurrent_hook", "process_lifetime", gate=gate)
    service.register(hook)

    first = asyncio.create_task(service.hydrate_hook(hook.name))
    second = asyncio.create_task(service.hydrate_hook(hook.name))
    await asyncio.sleep(0)
    gate.set()

    assert await asyncio.gather(first, second) == [True, True]
    assert hook.calls == 1


@pytest.mark.asyncio
async def test_failed_hydration_retries(service):
    service.store.should_fail = True
    hook = FakeHook("fail_hook", "process_day")
    service.register(hook)

    assert await service.hydrate_hook(hook.name) is False

    service.store.should_fail = False
    assert await service.hydrate_hook(hook.name) is True
    assert hook.calls == 2


def test_invalid_scope_is_rejected(service):
    with pytest.raises(ValueError, match="unsupported hydration scope"):
        service.register(FakeHook("invalid", "request"))
