"""Read-only process hydration for bounded in-memory runtime state.

Hydration hooks recover in-process counters and caches from the configured
``SessionStoreProtocol``. This is intentionally separate from agent session
rehydration, whose continuity remains in Git and disk-backed skills.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Protocol, Set, Tuple
from weakref import WeakKeyDictionary

from .storage import SessionStoreProtocol

_LOGGER = logging.getLogger("GrokMCP.Hydration")

HydrationScope = Literal["process_day", "process_lifetime", "session"]
_VALID_SCOPES = frozenset(("process_day", "process_lifetime", "session"))


@dataclass(frozen=True)
class HydrationContext:
    session_id: Optional[str] = None


@dataclass(frozen=True)
class HydrationResult:
    detail: str = ""


class HydrationHook(Protocol):
    name: str
    scope: HydrationScope

    async def hydrate(
        self, store: SessionStoreProtocol, ctx: HydrationContext
    ) -> HydrationResult: ...


class HydrationService:
    """Coordinate idempotent hooks for one concrete store instance.

    Hook execution happens outside locks. A shared in-flight future prevents
    concurrent first-use requests from running the same hook twice. Failed or
    cancelled hydration is never marked complete, so a later request retries.
    """

    def __init__(self, store: SessionStoreProtocol):
        self.store = store
        self._hooks: Dict[str, HydrationHook] = {}
        self._hooks_lock = threading.Lock()
        self._state_lock = asyncio.Lock()
        self._completed: Set[Tuple[str, str]] = set()
        self._in_flight: Dict[Tuple[str, str], asyncio.Future[bool]] = {}

    def register(self, hook: HydrationHook) -> None:
        if not hook.name:
            raise ValueError("hydration hook name must be non-empty")
        if hook.scope not in _VALID_SCOPES:
            raise ValueError(f"unsupported hydration scope: {hook.scope!r}")
        if hook.name in self._hooks:
            return
        with self._hooks_lock:
            if hook.name in self._hooks:
                return
            hooks = dict(self._hooks)
            hooks[hook.name] = hook
            self._hooks = hooks

    @staticmethod
    def _scope_token(
        hook: HydrationHook, ctx: HydrationContext
    ) -> Optional[str]:
        if hook.scope == "process_day":
            return datetime.now().date().isoformat()
        if hook.scope == "process_lifetime":
            return "process"
        return ctx.session_id

    async def hydrate_hook(
        self, hook_name: str, ctx: Optional[HydrationContext] = None
    ) -> bool:
        """Hydrate one hook, returning whether its scope is hydrated."""
        hook = self._hooks.get(hook_name)
        if hook is None:
            return False

        active_ctx = ctx or HydrationContext()
        scope_token = self._scope_token(hook, active_ctx)
        if scope_token is None:
            _LOGGER.warning("Hydration hook %s requires a session id", hook.name)
            return False

        key = (hook.name, scope_token)
        owner = False
        async with self._state_lock:
            if key in self._completed:
                return True
            future = self._in_flight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future
                owner = True

        if not owner:
            return await asyncio.shield(future)

        succeeded = False
        try:
            await hook.hydrate(self.store, active_ctx)
            succeeded = True
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.warning("Hydration failed for hook %s: %s", hook.name, exc)
            return False
        finally:
            async with self._state_lock:
                if succeeded:
                    self._completed.add(key)
                completed_future = self._in_flight.pop(key, None)
                if completed_future is not None and not completed_future.done():
                    completed_future.set_result(succeeded)

    def _hook_names_for_scope(self, scope: HydrationScope) -> list[str]:
        return [
            hook.name for hook in self._hooks.values() if hook.scope == scope
        ]

    async def hydrate_process_day(self) -> None:
        names = self._hook_names_for_scope("process_day")
        if names:
            await asyncio.gather(
                *(self.hydrate_hook(name) for name in names),
                return_exceptions=True,
            )

    async def hydrate_process_lifetime(self) -> None:
        names = self._hook_names_for_scope("process_lifetime")
        if names:
            await asyncio.gather(
                *(self.hydrate_hook(name) for name in names),
                return_exceptions=True,
            )

    async def hydrate_session(self, session_id: str) -> None:
        names = self._hook_names_for_scope("session")
        if names:
            ctx = HydrationContext(session_id=session_id)
            await asyncio.gather(
                *(self.hydrate_hook(name, ctx) for name in names),
                return_exceptions=True,
            )


_SERVICES_LOCK = threading.Lock()
_SERVICES: WeakKeyDictionary[Any, HydrationService] = WeakKeyDictionary()
_FALLBACK_SERVICES: Dict[int, Tuple[Any, HydrationService]] = {}


def get_hydration_service(store: SessionStoreProtocol) -> HydrationService:
    """Return the service bound to exactly this store instance."""
    with _SERVICES_LOCK:
        try:
            service = _SERVICES.get(store)
        except TypeError:
            entry = _FALLBACK_SERVICES.get(id(store))
            if entry is not None and entry[0] is store:
                return entry[1]
            service = HydrationService(store)
            _FALLBACK_SERVICES[id(store)] = (store, service)
            return service
        if service is None:
            service = HydrationService(store)
            _SERVICES[store] = service
        return service


def reset_hydration_services() -> None:
    """Reset the process registry (tests and controlled service teardown)."""
    with _SERVICES_LOCK:
        _SERVICES.clear()
        _FALLBACK_SERVICES.clear()


def reset_hydration_service() -> None:
    """Backward-compatible singular alias."""
    reset_hydration_services()
