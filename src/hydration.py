"""Hydration Service for broader application telemetry recovery.

This module provides an orchestration seam to manage hydration hooks over
the SessionStoreProtocol. It separates runtime telemetry process hydration
(floors for in-process budgets, gates, caches) from intelligence session
rehydration (a git/disk skill).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Set

from .storage import SessionStoreProtocol

_LOGGER = logging.getLogger("GrokMCP.Hydration")

@dataclass
class HydrationContext:
    session_id: Optional[str] = None

class HydrationResult:
    pass

class HydrationHook(Protocol):
    name: str
    scope: str  # "process_day" | "process_lifetime" | "session"

    async def hydrate(self, store: SessionStoreProtocol, ctx: HydrationContext) -> HydrationResult:
        ...

class HydrationService:
    def __init__(self, store: SessionStoreProtocol):
        self.store = store
        self._hooks: Dict[str, HydrationHook] = {}
        self._lock = threading.Lock()
        
        # State tracking for idempotency
        self._process_day_hydrated: Dict[str, str] = {}  # hook_name -> iso_date
        self._process_lifetime_hydrated: Set[str] = set()  # hook_name
        self._session_hydrated: Dict[str, Set[str]] = {}  # session_id -> set of hook_names

    def register(self, hook: HydrationHook) -> None:
        with self._lock:
            self._hooks[hook.name] = hook

    async def hydrate_hook(self, hook_name: str, ctx: Optional[HydrationContext] = None) -> None:
        """Hydrate a specific hook by name, subject to its scope idempotency."""
        hook = self._hooks.get(hook_name)
        if not hook:
            return

        ctx = ctx or HydrationContext()
        today = datetime.now().date().isoformat()
        
        with self._lock:
            if hook.scope == "process_day":
                if self._process_day_hydrated.get(hook.name) == today:
                    return
            elif hook.scope == "process_lifetime":
                if hook.name in self._process_lifetime_hydrated:
                    return
            elif hook.scope == "session":
                if not ctx.session_id:
                    _LOGGER.warning(f"Hook {hook.name} requires session_id but none provided.")
                    return
                if hook.name in self._session_hydrated.get(ctx.session_id, set()):
                    return

        # Execute hydrate outside lock to avoid blocking other operations
        try:
            await hook.hydrate(self.store, ctx)
        except Exception as exc:
            _LOGGER.warning(f"Hydration failed for hook {hook.name}: {exc}")
            return  # Fail open, do not mark as hydrated so it can retry

        # Mark as hydrated upon success
        with self._lock:
            if hook.scope == "process_day":
                self._process_day_hydrated[hook.name] = today
            elif hook.scope == "process_lifetime":
                self._process_lifetime_hydrated.add(hook.name)
            elif hook.scope == "session":
                if ctx.session_id:
                    if ctx.session_id not in self._session_hydrated:
                        self._session_hydrated[ctx.session_id] = set()
                    self._session_hydrated[ctx.session_id].add(hook.name)

    async def hydrate_process_day(self) -> None:
        """Hydrate all process_day scoped hooks."""
        hooks_to_run = []
        with self._lock:
            for hook in self._hooks.values():
                if hook.scope == "process_day":
                    hooks_to_run.append(hook.name)
                    
        # Parallelize independent hooks
        if hooks_to_run:
            tasks = [self.hydrate_hook(name) for name in hooks_to_run]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def hydrate_process_lifetime(self) -> None:
        """Hydrate all process_lifetime scoped hooks."""
        hooks_to_run = []
        with self._lock:
            for hook in self._hooks.values():
                if hook.scope == "process_lifetime":
                    hooks_to_run.append(hook.name)
                    
        if hooks_to_run:
            tasks = [self.hydrate_hook(name) for name in hooks_to_run]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def hydrate_session(self, session_id: str) -> None:
        """Hydrate all session scoped hooks for a given session_id."""
        hooks_to_run = []
        ctx = HydrationContext(session_id=session_id)
        with self._lock:
            for hook in self._hooks.values():
                if hook.scope == "session":
                    hooks_to_run.append(hook.name)
                    
        if hooks_to_run:
            tasks = [self.hydrate_hook(name, ctx) for name in hooks_to_run]
            await asyncio.gather(*tasks, return_exceptions=True)

_SERVICE: Optional[HydrationService] = None

def get_hydration_service(store: SessionStoreProtocol) -> HydrationService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = HydrationService(store)
    return _SERVICE

def reset_hydration_service() -> None:
    """Reset the global hydration service (for tests)."""
    global _SERVICE
    _SERVICE = None
