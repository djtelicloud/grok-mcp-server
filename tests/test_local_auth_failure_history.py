from __future__ import annotations

from unigrok_public import local_auth


def test_failure_limiter_caps_events_per_peer() -> None:
    limiter = local_auth._FailureLimiter(failure_limit=2)

    assert limiter.record("peer-a", 1.0) is False
    assert limiter.record("peer-a", 2.0) is False
    assert limiter.record("peer-a", 3.0) is True
    for _ in range(1_000):
        assert limiter.record("peer-a", 3.0) is True

    assert list(limiter._failures["peer-a"]) == [1.0, 2.0, 3.0]
