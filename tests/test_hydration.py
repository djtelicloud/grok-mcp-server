"""Unit tests for hydration service."""

import asyncio
import pytest
from datetime import datetime

from src.hydration import (
    HydrationService,
    HydrationContext,
    HydrationResult,
    get_hydration_service,
    reset_hydration_service,
)
from src.storage import SessionStoreProtocol

class FakeStore:
    def __init__(self, cost_to_return=1.5, should_fail=False):
        self.cost_to_return = cost_to_return
        self.should_fail = should_fail
        self.get_caller_cost_today_calls = 0

    async def get_caller_cost_today(self, caller_principal: str) -> float:
        self.get_caller_cost_today_calls += 1
        if self.should_fail:
            raise Exception("Store offline")
        return self.cost_to_return

class FakeHook:
    def __init__(self, name, scope):
        self.name = name
        self.scope = scope
        self.calls = 0
        self.last_ctx = None

    async def hydrate(self, store, ctx):
        self.calls += 1
        self.last_ctx = ctx
        # Let's interact with store to prove it works
        if hasattr(store, "get_caller_cost_today"):
            await store.get_caller_cost_today("test")
        return HydrationResult()

@pytest.fixture
def service():
    reset_hydration_service()
    store = FakeStore()
    return get_hydration_service(store)

@pytest.mark.asyncio
async def test_hydration_service_singleton(service):
    store2 = FakeStore()
    service2 = get_hydration_service(store2)
    assert service is service2

@pytest.mark.asyncio
async def test_process_day_idempotency(service):
    hook = FakeHook("day_hook", "process_day")
    service.register(hook)
    
    await service.hydrate_process_day()
    assert hook.calls == 1
    
    # Second call on same day should be idempotent
    await service.hydrate_process_day()
    assert hook.calls == 1

@pytest.mark.asyncio
async def test_process_lifetime_idempotency(service):
    hook = FakeHook("lifetime_hook", "process_lifetime")
    service.register(hook)
    
    await service.hydrate_process_lifetime()
    assert hook.calls == 1
    
    await service.hydrate_process_lifetime()
    assert hook.calls == 1

@pytest.mark.asyncio
async def test_session_idempotency(service):
    hook = FakeHook("session_hook", "session")
    service.register(hook)
    
    await service.hydrate_session("sess-1")
    assert hook.calls == 1
    
    # Same session should not trigger again
    await service.hydrate_session("sess-1")
    assert hook.calls == 1
    
    # New session should trigger
    await service.hydrate_session("sess-2")
    assert hook.calls == 2

@pytest.mark.asyncio
async def test_fail_open(service):
    service.store.should_fail = True
    hook = FakeHook("fail_hook", "process_day")
    service.register(hook)
    
    await service.hydrate_process_day()
    # Hook was called, store raised exception, HydrationService caught it (fail open)
    assert hook.calls == 1
    
    # Because it failed open, it wasn't marked hydrated. The next call SHOULD try again!
    service.store.should_fail = False
    await service.hydrate_process_day()
    assert hook.calls == 2
