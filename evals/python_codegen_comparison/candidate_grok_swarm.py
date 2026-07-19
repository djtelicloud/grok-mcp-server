"""Cancellation-safe asynchronous TTL/LRU cache with single-flight loading."""

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
class _CacheEntry(Generic[V]):
    value: V
    expires_at: float


@dataclass(slots=True)
class _InFlight(Generic[V]):
    """One in-flight load; identity is used to detect detachment."""

    future: asyncio.Future[V]


class AsyncTTLCache(Generic[K, V]):
    """Async cache combining TTL expiration, LRU eviction, and single-flight loads.

    Concurrent misses for the same key share one loader. Cancelling a waiter does
    not cancel the shared loader or other waiters. ``invalidate`` / ``clear``
    detach in-flight loads so they finish for existing waiters but never
    repopulate the cache. Intended for a single asyncio event loop.
    """

    def __init__(
        self,
        max_size: int,
        ttl: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("max_size must be an integer greater than zero")
        # bool is a Real subclass; reject it before the Real check.
        if isinstance(ttl, bool) or not isinstance(ttl, Real):
            raise ValueError("ttl must be a finite real number greater than zero")
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite real number greater than zero")

        self._max_size = max_size
        self._ttl = float(ttl)
        self._clock: Callable[[], float] = (
            clock if clock is not None else time.monotonic
        )

        self._lock = asyncio.Lock()
        self._cache: OrderedDict[K, _CacheEntry[V]] = OrderedDict()
        self._inflight: dict[K, _InFlight[V]] = {}

        self._hits = 0
        self._misses = 0
        self._loads = 0
        self._coalesced = 0
        self._evictions = 0

    def _is_fresh(self, entry: _CacheEntry[V], now: float) -> bool:
        return now < entry.expires_at

    def _purge_expired(self, now: float) -> None:
        expired = [
            key for key, entry in self._cache.items() if not self._is_fresh(entry, now)
        ]
        for key in expired:
            del self._cache[key]

    def _insert(self, key: K, value: V, now: float) -> None:
        """Insert a completed entry; purge expired, then LRU-evict if over capacity.

        Caller holds the lock. Evictions are counted only for capacity pressure,
        never for expiration.
        """
        self._purge_expired(now)
        self._cache[key] = _CacheEntry(value=value, expires_at=now + self._ttl)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
            self._evictions += 1

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _consume_future_exception(fut: asyncio.Future[V]) -> None:
        # Avoid "exception was never retrieved" if all waiters cancelled.
        if not fut.cancelled():
            fut.exception()

    async def _run_loader(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        inflight: _InFlight[V],
    ) -> None:
        try:
            value = await loader()
        except BaseException as exc:
            async with self._lock:
                if self._inflight.get(key) is inflight:
                    del self._inflight[key]
            if not inflight.future.done():
                inflight.future.set_exception(exc)
            return

        async with self._lock:
            if self._inflight.get(key) is inflight:
                del self._inflight[key]
                self._insert(key, value, self._clock())

        if not inflight.future.done():
            inflight.future.set_result(value)

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
    ) -> V:
        """Return a fresh cached value or load it with single-flight coalescing.

        Concurrent callers for a missing key await the same shared future via
        ``asyncio.shield`` so cancelling one waiter does not cancel the shared
        future, the loader, or other waiters. A successful non-detached load
        populates the cache even if every waiter was cancelled. Loader exceptions
        are not cached and propagate to all current waiters.
        """
        async with self._lock:
            now = self._clock()
            entry = self._cache.get(key)
            if entry is not None:
                if self._is_fresh(entry, now):
                    self._hits += 1
                    self._cache.move_to_end(key)
                    return entry.value
                del self._cache[key]

            self._misses += 1
            inflight = self._inflight.get(key)
            if inflight is not None:
                self._coalesced += 1
                fut = inflight.future
            else:
                self._loads += 1
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                fut.add_done_callback(self._consume_future_exception)
                inflight = _InFlight(future=fut)
                self._inflight[key] = inflight
                task = asyncio.create_task(
                    self._run_loader(key, loader, inflight),
                    name="AsyncTTLCache.load",
                )
                task.add_done_callback(self._consume_task_exception)

        # Shield so task cancellation does not cancel the shared future (and
        # thereby every other waiter). The loader task is independent.
        return await asyncio.shield(fut)

    async def invalidate(self, key: K) -> bool:
        """Remove a cached value and detach any in-flight load for ``key``.

        A detached loader still completes for its existing waiters but must not
        repopulate the cache. Returns True if a value or in-flight load existed.
        """
        async with self._lock:
            removed = False
            if key in self._cache:
                del self._cache[key]
                removed = True
            if key in self._inflight:
                del self._inflight[key]
                removed = True
            return removed

    async def clear(self) -> None:
        """Remove all cached values and detach all in-flight loads.

        Detached loaders continue for existing waiters but cannot repopulate
        the cache.
        """
        async with self._lock:
            self._cache.clear()
            self._inflight.clear()

    async def snapshot_stats(self) -> CacheStats:
        """Return an immutable point-in-time snapshot of cumulative statistics."""
        async with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                loads=self._loads,
                coalesced=self._coalesced,
                evictions=self._evictions,
            )

    async def __len__(self) -> int:
        """Return the number of fresh cached entries, purging expired ones first."""
        async with self._lock:
            self._purge_expired(self._clock())
            return len(self._cache)
