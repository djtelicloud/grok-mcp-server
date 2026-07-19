from __future__ import annotations

import asyncio
import dataclasses
import gc
import importlib.util
import inspect
import json
import math
import pathlib
import sys
import unittest
from collections.abc import Callable
from fractions import Fraction
from types import ModuleType
from typing import TypeVar


def load_candidate(path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("candidate_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if len(sys.argv) != 2:
    raise SystemExit("usage: python evaluate.py PATH_TO_CANDIDATE")

CANDIDATE_PATH = pathlib.Path(sys.argv[1]).resolve()
MODULE = load_candidate(CANDIDATE_PATH)
AsyncTTLCache = MODULE.AsyncTTLCache
CacheStats = MODULE.CacheStats
T = TypeVar("T")


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, amount: float) -> None:
        self.value += amount


async def eventually(
    predicate: Callable[[], T],
    *,
    timeout: float = 1.0,
) -> T:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if loop.time() >= deadline:
            raise AssertionError("condition did not become true before timeout")
        await asyncio.sleep(0)


class CacheContractTests(unittest.IsolatedAsyncioTestCase):
    def test_public_api_and_constructor_validation(self) -> None:
        self.assertTrue(dataclasses.is_dataclass(CacheStats))
        self.assertEqual(
            list(inspect.signature(AsyncTTLCache.__init__).parameters),
            ["self", "max_size", "ttl", "clock"],
        )
        self.assertEqual(
            list(inspect.signature(AsyncTTLCache.get_or_load).parameters),
            ["self", "key", "loader"],
        )
        self.assertEqual(
            list(inspect.signature(AsyncTTLCache.invalidate).parameters),
            ["self", "key"],
        )
        self.assertTrue(inspect.iscoroutinefunction(AsyncTTLCache.__len__))

        for value in (0, -1, True, 1.5, "1", None):
            with self.subTest(max_size=value):
                with self.assertRaises(ValueError):
                    AsyncTTLCache(value, 1.0)
        for value in (0, -1, True, math.nan, math.inf, -math.inf, "1", None):
            with self.subTest(ttl=value):
                with self.assertRaises(ValueError):
                    AsyncTTLCache(1, value)

    async def test_ttl_boundary_lazy_expiry_and_stats(self) -> None:
        clock = FakeClock(10.0)
        cache = AsyncTTLCache[str, int](2, 5.0, clock=clock)
        calls = 0

        async def load() -> int:
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await cache.get_or_load("x", load), 1)
        clock.advance(4.999)
        self.assertEqual(await cache.get_or_load("x", load), 1)
        clock.value = 15.0
        self.assertEqual(await cache.get_or_load("x", load), 2)
        self.assertEqual(await cache.__len__(), 1)
        self.assertEqual(
            await cache.snapshot_stats(),
            CacheStats(hits=1, misses=2, loads=2, coalesced=0, evictions=0),
        )

    async def test_ttl_accepts_finite_real_values_beyond_int_and_float(self) -> None:
        cache = AsyncTTLCache[str, int](1, Fraction(1, 2), clock=FakeClock())

        async def load() -> int:
            return 7

        self.assertEqual(await cache.get_or_load("x", load), 7)

    async def test_ttl_is_measured_from_loader_completion(self) -> None:
        clock = FakeClock()
        cache = AsyncTTLCache[str, str](1, 10.0, clock=clock)
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow() -> str:
            started.set()
            await release.wait()
            return "first"

        task = asyncio.create_task(cache.get_or_load("x", slow))
        await asyncio.wait_for(started.wait(), 1)
        clock.advance(100)
        release.set()
        self.assertEqual(await task, "first")
        clock.advance(9.999)

        async def unexpected() -> str:
            raise AssertionError("fresh value was not used")

        self.assertEqual(await cache.get_or_load("x", unexpected), "first")
        clock.advance(0.001)

        async def second() -> str:
            return "second"

        self.assertEqual(await cache.get_or_load("x", second), "second")

    async def test_lru_eviction_and_expiration_do_not_share_counters(self) -> None:
        clock = FakeClock()
        cache = AsyncTTLCache[str, str](2, 10.0, clock=clock)
        calls: dict[str, int] = {}

        async def load(key: str) -> str:
            calls[key] = calls.get(key, 0) + 1
            return f"{key}{calls[key]}"

        self.assertEqual(await cache.get_or_load("a", lambda: load("a")), "a1")
        self.assertEqual(await cache.get_or_load("b", lambda: load("b")), "b1")
        self.assertEqual(await cache.get_or_load("a", lambda: load("a")), "a1")
        self.assertEqual(await cache.get_or_load("c", lambda: load("c")), "c1")
        self.assertEqual(await cache.get_or_load("b", lambda: load("b")), "b2")
        self.assertEqual(calls, {"a": 1, "b": 2, "c": 1})
        self.assertEqual((await cache.snapshot_stats()).evictions, 2)

        clock.advance(10)
        self.assertEqual(await cache.__len__(), 0)
        self.assertEqual((await cache.snapshot_stats()).evictions, 2)

    async def test_expired_mru_is_removed_before_fresh_lru_is_evicted(self) -> None:
        clock = FakeClock()
        cache = AsyncTTLCache[str, str](2, 10.0, clock=clock)

        async def value(text: str) -> str:
            return text

        await cache.get_or_load("a", lambda: value("A"))  # expires at 10
        clock.value = 5
        await cache.get_or_load("b", lambda: value("B"))  # expires at 15
        clock.value = 6
        await cache.get_or_load("a", lambda: value("unused"))  # expired-first key becomes MRU
        clock.value = 10
        await cache.get_or_load("c", lambda: value("C"))

        async def unexpected() -> str:
            raise AssertionError("fresh LRU value was evicted while an expired entry remained")

        self.assertEqual(await cache.get_or_load("b", unexpected), "B")
        self.assertEqual(await cache.__len__(), 2)
        self.assertEqual((await cache.snapshot_stats()).evictions, 0)

    async def test_same_key_single_flight_and_stats(self) -> None:
        cache = AsyncTTLCache[str, int](4, 30.0)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def load() -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return 42

        tasks = [asyncio.create_task(cache.get_or_load("x", load)) for _ in range(20)]
        await asyncio.wait_for(started.wait(), 1)

        async def joined() -> bool:
            return (await cache.snapshot_stats()).coalesced == 19

        await eventually_async(joined)
        release.set()
        self.assertEqual(await asyncio.gather(*tasks), [42] * 20)
        self.assertEqual(calls, 1)
        self.assertEqual(
            await cache.snapshot_stats(),
            CacheStats(hits=0, misses=20, loads=1, coalesced=19, evictions=0),
        )

    async def test_different_keys_load_concurrently(self) -> None:
        cache = AsyncTTLCache[str, str](4, 30.0)
        both_started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0

        async def load(value: str) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_started.set()
            await release.wait()
            active -= 1
            return value

        first = asyncio.create_task(cache.get_or_load("a", lambda: load("A")))
        second = asyncio.create_task(cache.get_or_load("b", lambda: load("B")))
        await asyncio.wait_for(both_started.wait(), 1)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), ["A", "B"])
        self.assertEqual(peak, 2)

    async def test_loader_failure_is_shared_and_not_cached(self) -> None:
        cache = AsyncTTLCache[str, int](2, 30.0)
        release = asyncio.Event()
        started = asyncio.Event()
        calls = 0

        class Boom(RuntimeError):
            pass

        async def failing() -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            raise Boom("failure")

        tasks = [asyncio.create_task(cache.get_or_load("x", failing)) for _ in range(3)]
        await asyncio.wait_for(started.wait(), 1)

        async def joined() -> bool:
            return (await cache.snapshot_stats()).coalesced == 2

        await eventually_async(joined)
        release.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(all(isinstance(item, Boom) for item in outcomes), outcomes)
        self.assertEqual(calls, 1)

        async def recovery() -> int:
            nonlocal calls
            calls += 1
            return 9

        self.assertEqual(await cache.get_or_load("x", recovery), 9)
        self.assertEqual(calls, 2)
        self.assertEqual(
            await cache.snapshot_stats(),
            CacheStats(hits=0, misses=4, loads=2, coalesced=2, evictions=0),
        )

    async def test_cancelling_initial_waiter_does_not_cancel_shared_load(self) -> None:
        cache = AsyncTTLCache[str, str](2, 30.0)
        started = asyncio.Event()
        release = asyncio.Event()

        async def load() -> str:
            started.set()
            await release.wait()
            return "ok"

        initial = asyncio.create_task(cache.get_or_load("x", load))
        await asyncio.wait_for(started.wait(), 1)
        follower = asyncio.create_task(cache.get_or_load("x", load))

        async def joined() -> bool:
            return (await cache.snapshot_stats()).coalesced == 1

        await eventually_async(joined)
        initial.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await initial
        release.set()
        self.assertEqual(await follower, "ok")

        async def unexpected() -> str:
            raise AssertionError("completed flight was not cached")

        self.assertEqual(await cache.get_or_load("x", unexpected), "ok")

    async def test_all_waiters_cancel_but_success_still_populates(self) -> None:
        cache = AsyncTTLCache[str, str](2, 30.0)
        started = asyncio.Event()
        release = asyncio.Event()

        async def load() -> str:
            started.set()
            await release.wait()
            return "survived"

        first = asyncio.create_task(cache.get_or_load("x", load))
        second = asyncio.create_task(cache.get_or_load("x", load))
        await asyncio.wait_for(started.wait(), 1)
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        release.set()

        async def populated() -> bool:
            return await cache.__len__() == 1

        await eventually_async(populated)

        async def unexpected() -> str:
            raise AssertionError("orphaned successful flight was not cached")

        self.assertEqual(await cache.get_or_load("x", unexpected), "survived")

    async def test_unobserved_loader_failure_has_no_loop_warning(self) -> None:
        cache = AsyncTTLCache[str, str](2, 30.0)
        started = asyncio.Event()
        release = asyncio.Event()
        loop = asyncio.get_running_loop()
        contexts: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))
        try:
            async def load() -> str:
                started.set()
                await release.wait()
                raise RuntimeError("unobserved loader failure")

            waiter = asyncio.create_task(cache.get_or_load("x", load))
            await asyncio.wait_for(started.wait(), 1)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            release.set()

            async def flight_finished() -> bool:
                return (await cache.snapshot_stats()).loads == 1 and await cache.__len__() == 0

            await eventually_async(flight_finished)
            for _ in range(3):
                gc.collect()
                await asyncio.sleep(0)
            warnings = [
                context
                for context in contexts
                if "never retrieved" in str(context.get("message", "")).lower()
            ]
            self.assertEqual(warnings, [])
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_invalidate_detaches_old_flight_and_new_value_wins(self) -> None:
        cache = AsyncTTLCache[str, int](2, 30.0)
        old_started = asyncio.Event()
        old_release = asyncio.Event()

        async def old_load() -> int:
            old_started.set()
            await old_release.wait()
            return 1

        old_waiter = asyncio.create_task(cache.get_or_load("x", old_load))
        await asyncio.wait_for(old_started.wait(), 1)
        self.assertTrue(await cache.invalidate("x"))
        self.assertFalse(await cache.invalidate("x"))

        async def new_load() -> int:
            return 2

        self.assertEqual(await cache.get_or_load("x", new_load), 2)
        old_release.set()
        self.assertEqual(await old_waiter, 1)

        async def unexpected() -> int:
            raise AssertionError("detached old load replaced the new value")

        self.assertEqual(await cache.get_or_load("x", unexpected), 2)

    async def test_clear_detaches_all_flights(self) -> None:
        cache = AsyncTTLCache[str, str](3, 30.0)
        a_started = asyncio.Event()
        b_started = asyncio.Event()
        release = asyncio.Event()

        async def old_a() -> str:
            a_started.set()
            await release.wait()
            return "old-a"

        async def old_b() -> str:
            b_started.set()
            await release.wait()
            return "old-b"

        a_waiter = asyncio.create_task(cache.get_or_load("a", old_a))
        b_waiter = asyncio.create_task(cache.get_or_load("b", old_b))
        await asyncio.wait_for(asyncio.gather(a_started.wait(), b_started.wait()), 1)
        await cache.clear()

        async def new_a() -> str:
            return "new-a"

        self.assertEqual(await cache.get_or_load("a", new_a), "new-a")
        release.set()
        self.assertEqual(await asyncio.gather(a_waiter, b_waiter), ["old-a", "old-b"])
        self.assertEqual(await cache.__len__(), 1)

        async def new_b() -> str:
            return "new-b"

        self.assertEqual(await cache.get_or_load("b", new_b), "new-b")
        self.assertEqual((await cache.snapshot_stats()).evictions, 0)

    async def test_stats_snapshot_is_frozen_and_independent(self) -> None:
        cache = AsyncTTLCache[str, int](1, 10.0)

        async def load() -> int:
            return 1

        await cache.get_or_load("x", load)
        first = await cache.snapshot_stats()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.hits = 99
        await cache.get_or_load("x", load)
        second = await cache.snapshot_stats()
        self.assertEqual(first.hits, 0)
        self.assertEqual(second.hits, 1)


async def eventually_async(
    predicate: Callable[[], object],
    *,
    timeout: float = 1.0,
) -> object:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = await predicate()
        if value:
            return value
        if loop.time() >= deadline:
            raise AssertionError("async condition did not become true before timeout")
        await asyncio.sleep(0)


class CapturingResult(unittest.TextTestResult):
    pass


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CacheContractTests)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=CapturingResult)
    result = runner.run(suite)
    summary = {
        "candidate": CANDIDATE_PATH.name,
        "tests_run": result.testsRun,
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "skipped": len(result.skipped),
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
    }
    print("EVALUATION_JSON=" + json.dumps(summary, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
