# Implementation Plan: cancellation-safe asynchronous TTL/LRU cache

Implement a single Python 3.12 module named `async_ttl_cache.py` using only the
standard library.

## Goal

Provide a small generic asynchronous cache that combines TTL expiration, LRU
eviction, and single-flight loading. The difficult part is correct behavior when
multiple callers, cancellation, invalidation, and loader failures overlap.

## Required public API

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

class AsyncTTLCache(Generic[K, V]):
    def __init__(
        self,
        max_size: int,
        ttl: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None: ...

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
    ) -> V: ...

    async def invalidate(self, key: K) -> bool: ...
    async def clear(self) -> None: ...
    async def snapshot_stats(self) -> CacheStats: ...
    async def __len__(self) -> int: ...
```

Do not add required constructor arguments or change these names/signatures.
Private helpers and private dataclasses are allowed.

## Behavior

1. `max_size` must be an integer greater than zero. Reject booleans and invalid
   values with `ValueError`.
2. `ttl` must be a finite real number greater than zero. Reject booleans, NaN,
   infinity, and invalid values with `ValueError`.
3. Use `time.monotonic` when no clock is supplied. A supplied clock is called
   synchronously and returns the current numeric timestamp.
4. A cached entry is fresh while `clock() < expires_at`. At equality it is
   expired. Expired entries behave like misses and are removed lazily.
5. A fresh hit returns immediately and updates that key to most-recently-used.
6. On a miss, start `loader` exactly once for that key. Concurrent callers for
   the same missing key must await the same in-flight load. Loads for different
   keys must be able to run concurrently.
7. Cancelling one waiter must not cancel the shared loader or affect other
   waiters. If every waiter is cancelled, the loader still finishes normally
   and may populate the cache.
8. If the loader raises any exception, do not cache it. All current waiters see
   that failure, and the next call may start a new load.
9. `invalidate(key)` removes a cached value and detaches any in-flight load for
   that key. It returns `True` if either existed. A detached loader continues for
   its existing waiters but must never repopulate the cache. A later call for the
   key starts a new independent load immediately.
10. `clear()` removes all cached values and detaches all in-flight loads. Those
    loaders continue for existing waiters but cannot repopulate the cache.
11. After a successful non-detached load, insert the result with a TTL measured
    from completion time. If insertion makes the cache exceed `max_size`, evict
    least-recently-used completed entries until it fits. Never evict or count an
    in-flight load as a cached entry.
12. `__len__` returns the number of fresh cached entries. It must lazily remove
    all expired entries before counting.
13. The implementation is intended for one asyncio event loop. Protect shared
    state with an `asyncio.Lock`; do not hold that lock while awaiting a loader.

## Statistics

Statistics begin at zero and are cumulative:

- `hits`: each `get_or_load` call served from a fresh cached value.
- `misses`: each `get_or_load` call not served from a fresh cached value,
  including callers that join an in-flight load.
- `loads`: loader tasks actually started.
- `coalesced`: miss calls that join an existing in-flight load.
- `evictions`: entries removed only because `max_size` was exceeded. Expiration,
  invalidation, and clear do not increment it.

`snapshot_stats` returns an immutable point-in-time `CacheStats` value.

## Quality constraints

- Standard library only.
- Fully type annotated; no `Any` unless unavoidable at a narrow boundary.
- No background cleanup loop, global mutable state, sleeps, polling, or busy
  waiting.
- Do not suppress loader exceptions.
- Avoid unhandled-task warnings when a detached or unobserved loader fails.
- Include concise docstrings where they clarify the concurrency contract.

## Output contract

Return only the complete contents of `async_ttl_cache.py`. Do not include
Markdown fences, tests, usage examples, or commentary.
