"""Mission status machine and transition rules."""

from __future__ import annotations

from enum import StrEnum


class MissionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    WAITING_EVENT = "waiting_event"
    WAITING_TIMER = "waiting_timer"
    DORMANT = "dormant"
    ESCALATED = "escalated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[MissionStatus] = frozenset(
    {
        MissionStatus.COMPLETE,
        MissionStatus.FAILED,
        MissionStatus.BUDGET_EXHAUSTED,
        MissionStatus.CANCELLED,
    }
)

_ALLOWED: frozenset[tuple[MissionStatus, MissionStatus]] = frozenset(
    {
        (MissionStatus.QUEUED, MissionStatus.RUNNING),
        (MissionStatus.RUNNING, MissionStatus.VERIFYING),
        (MissionStatus.RUNNING, MissionStatus.WAITING_EVENT),
        (MissionStatus.RUNNING, MissionStatus.WAITING_TIMER),
        (MissionStatus.RUNNING, MissionStatus.DORMANT),
        (MissionStatus.RUNNING, MissionStatus.ESCALATED),
        (MissionStatus.RUNNING, MissionStatus.FAILED),
        (MissionStatus.RUNNING, MissionStatus.BUDGET_EXHAUSTED),
        (MissionStatus.RUNNING, MissionStatus.CANCELLED),
        (MissionStatus.VERIFYING, MissionStatus.COMPLETE),
        (MissionStatus.VERIFYING, MissionStatus.RUNNING),
        (MissionStatus.VERIFYING, MissionStatus.FAILED),
        (MissionStatus.VERIFYING, MissionStatus.ESCALATED),
        (MissionStatus.WAITING_EVENT, MissionStatus.QUEUED),
        (MissionStatus.WAITING_TIMER, MissionStatus.QUEUED),
        (MissionStatus.DORMANT, MissionStatus.QUEUED),
        (MissionStatus.ESCALATED, MissionStatus.QUEUED),
        (MissionStatus.ESCALATED, MissionStatus.CANCELLED),
        # Sweeper: expired active lease returns work to queued.
        (MissionStatus.RUNNING, MissionStatus.QUEUED),
        (MissionStatus.VERIFYING, MissionStatus.QUEUED),
    }
)


def legal_transition(current: str | MissionStatus, nxt: str | MissionStatus) -> bool:
    try:
        cur = MissionStatus(str(current))
        nxt_s = MissionStatus(str(nxt))
    except ValueError:
        return False
    if cur in TERMINAL_STATUSES:
        return False
    return (cur, nxt_s) in _ALLOWED
