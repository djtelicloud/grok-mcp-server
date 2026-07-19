"""Lease expiry recovery only — never runs cognition or tools."""

from __future__ import annotations

from typing import Any, Protocol

from .types import MissionStatus, legal_transition


class MissionSweepStore(Protocol):
    async def list_expired_mission_leases(self, *, limit: int = 50) -> list[dict[str, Any]]:
        ...

    async def cas_mission_status(
        self,
        mission_id: str,
        *,
        expect_status: str,
        expect_version: int,
        expect_lease_generation: int,
        new_status: str,
        clear_lease: bool = False,
    ) -> bool:
        ...


async def sweep_expired_leases(store: MissionSweepStore, *, limit: int = 50) -> int:
    """Requeue missions whose lease expired while non-terminal. Returns count.

    Never executes cognition/tools. clear_lease bumps lease_generation so a
    stale verifying worker cannot CommitDone after reclaim.
    """
    rows = await store.list_expired_mission_leases(limit=limit)
    moved = 0
    for row in rows:
        status = str(row.get("status") or "")
        # Do not yank mid-verify via sweeper; only running/waiting/dormant.
        if status == MissionStatus.VERIFYING.value:
            continue
        if not legal_transition(status, MissionStatus.QUEUED):
            continue
        ok = await store.cas_mission_status(
            str(row["mission_id"]),
            expect_status=status,
            expect_version=int(row.get("checkpoint_version") or 0),
            expect_lease_generation=int(row.get("lease_generation") or 0),
            new_status=MissionStatus.QUEUED.value,
            clear_lease=True,
        )
        if ok:
            moved += 1
    return moved
