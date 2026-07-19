from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from numbers import Real
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class CacheStats:
    hits: int
    misses: int
    loads: int
    coalesced: int
    evictions: int


@dataclass(slots=True)
class _Entry(Generic[V]):
    value: V
    expires_at: float


@dataclass(slots=True)
class _Flight(Generic[V]):
    token: object
    task: asyncio.Task[V]


class AsyncTTLCache(Generic[K, V]):
    """An event-loop-local TTL/LRU cache with cancellation-safe single flight."""

    def __init__(
        self,
        max_size: int,
        ttl: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("max_size must be an integer greater than zero")
        if isinstance(ttl, bool) or not isinstance(ttl, Real):
            raise ValueError("ttl must be a finite real number greater than zero")
        ttl_value = float(ttl)
        if not math.isfinite(ttl_value) or ttl_value <= 0:
            raise ValueError("ttl must be a finite real number greater than zero")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")

        self._max_size = max_size
        self._ttl = ttl_value
        self._clock = time.monotonic if clock is None else clock
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._inflight: dict[K, _Flight[V]] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._loads = 0
        self._coalesced = 0
        self._evictions = 0

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("clock must return a finite real number")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("clock must return a finite real number")
        return result

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in expired:
            del self._entries[key]

    @staticmethod
    def _observe_task(task: asyncio.Task[V]) -> None:
        """Retrieve failures when a flight outlives all of its waiters."""
        if not task.cancelled():
            task.exception()

    async def _discard_flight(self, key: K, token: object) -> None:
        async with self._lock:
            current = self._inflight.get(key)
            if current is not None and current.token is token:
                del self._inflight[key]

    async def _load_and_store(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        token: object,
    ) -> V:
        try:
            value = await loader()
            completed_at = self._now()
        except BaseException:
            await self._discard_flight(key, token)
            raise

        async with self._lock:
            current = self._inflight.get(key)
            if current is not None and current.token is token:
                del self._inflight[key]
                self._purge_expired(completed_at)
                self._entries[key] = _Entry(value, completed_at + self._ttl)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_size:
                    self._entries.popitem(last=False)
                    self._evictions += 1
        return value

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
    ) -> V:
        """Return a fresh value or share one cancellation-isolated loader task."""
        if not callable(loader):
            raise ValueError("loader must be callable")

        async with self._lock:
            now = self._now()
            entry = self._entries.get(key)
            if entry is not None and now < entry.expires_at:
                self._entries.move_to_end(key)
                self._hits += 1
                return entry.value
            if entry is not None:
                del self._entries[key]

            self._misses += 1
            flight = self._inflight.get(key)
            if flight is not None:
                self._coalesced += 1
            else:
                token = object()
                task = asyncio.create_task(self._load_and_store(key, loader, token))
                task.add_done_callback(self._observe_task)
                flight = _Flight(token, task)
                self._inflight[key] = flight
                self._loads += 1

        return await asyncio.shield(flight.task)

    async def invalidate(self, key: K) -> bool:
        """Remove cached state and detach, but do not cancel, an active flight."""
        async with self._lock:
            entry_existed = self._entries.pop(key, None) is not None
            flight_existed = self._inflight.pop(key, None) is not None
            return entry_existed or flight_existed

    async def clear(self) -> None:
        """Remove all entries and detach every active flight without cancelling it."""
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()

    async def snapshot_stats(self) -> CacheStats:
        async with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                loads=self._loads,
                coalesced=self._coalesced,
                evictions=self._evictions,
            )

    async def __len__(self) -> int:
        async with self._lock:
            self._purge_expired(self._now())
            return len(self._entries)
