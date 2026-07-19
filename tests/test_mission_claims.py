from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from unigrok_public.mission.lease import lease_expiry_iso
from unigrok_public.state import PublicStateStore


async def _create_mission(
    store: PublicStateStore,
    mission_id: str,
    *,
    lease: str,
    generation: int,
    expires_at: str,
) -> str:
    continue_token = hashlib.sha256(mission_id.encode()).hexdigest()[:32]
    await store.create_mission(
        mission_id,
        job_id=f"job-{mission_id}",
        acceptance_hash=f"accept-{mission_id}",
        acceptance_text="Return a durable result.",
        continue_token=continue_token,
        package={"task": mission_id},
        lease_token=lease,
        lease_generation=generation,
        lease_expires_at=expires_at,
    )
    return continue_token


async def _initialized_stores(path: Path, count: int) -> list[PublicStateStore]:
    stores = [PublicStateStore(path) for _ in range(count)]
    for store in stores:
        await store.initialize()
    return stores


async def _barrier_claim(
    store: PublicStateStore,
    barrier: asyncio.Barrier,
    mission_id: str,
    lease: str,
    *,
    expect_generation: int | None = None,
) -> tuple[bool, int]:
    await barrier.wait()
    return await store.claim_mission(
        mission_id,
        lease_token=lease,
        ttl_seconds=120,
        expect_generation=expect_generation,
    )


@pytest.mark.asyncio
async def test_expired_claim_has_one_winner_across_state_stores(tmp_path: Path) -> None:
    path = tmp_path / "atomic-claim.db"
    setup = PublicStateStore(path)
    await setup.initialize()
    stores = await _initialized_stores(path, 32)

    # Multiple rounds make the test adversarial to the former SELECT-then-UPDATE
    # implementation, where several stores could all return the same winning fence.
    for round_index in range(4):
        mission_id = f"mission-race-{round_index}"
        initial_generation = 10 + round_index
        await _create_mission(
            setup,
            mission_id,
            lease="expired-owner",
            generation=initial_generation,
            expires_at="2000-01-01T00:00:00+00:00",
        )
        leases = [f"claimant-{round_index}-{index}" for index in range(len(stores))]
        barrier = asyncio.Barrier(len(stores))
        results = await asyncio.gather(
            *(
                _barrier_claim(store, barrier, mission_id, lease)
                for store, lease in zip(stores, leases, strict=True)
            )
        )

        winners = [
            lease for lease, (claimed, _generation) in zip(leases, results, strict=True) if claimed
        ]
        assert len(winners) == 1
        assert {generation for _claimed, generation in results} == {initial_generation + 1}
        mission = await setup.load_mission(mission_id)
        assert mission is not None
        assert mission["lease_token"] == winners[0]
        assert int(mission["lease_generation"]) == initial_generation + 1


@pytest.mark.asyncio
async def test_active_owner_renewal_blocks_competing_stores(tmp_path: Path) -> None:
    path = tmp_path / "active-owner.db"
    setup = PublicStateStore(path)
    await setup.initialize()
    owner_lease = "current-owner"
    generation = 7
    await _create_mission(
        setup,
        "mission-active",
        lease=owner_lease,
        generation=generation,
        expires_at=lease_expiry_iso(ttl_seconds=120),
    )
    stores = await _initialized_stores(path, 17)
    leases = [owner_lease, *(f"rival-{index}" for index in range(16))]
    expected = [generation, *(None for _index in range(16))]
    barrier = asyncio.Barrier(len(stores))

    results = await asyncio.gather(
        *(
            _barrier_claim(
                store,
                barrier,
                "mission-active",
                lease,
                expect_generation=expected_generation,
            )
            for store, lease, expected_generation in zip(stores, leases, expected, strict=True)
        )
    )

    assert results[0] == (True, generation)
    assert all(result == (False, generation) for result in results[1:])
    assert await stores[0].claim_mission("mission-active", lease_token=owner_lease) == (
        False,
        generation,
    )
    assert await stores[0].claim_mission(
        "mission-active",
        lease_token=owner_lease,
        expect_generation=generation - 1,
    ) == (False, generation)
    mission = await setup.load_mission("mission-active")
    assert mission is not None
    assert mission["lease_token"] == owner_lease
    assert int(mission["lease_generation"]) == generation


@pytest.mark.parametrize(
    "terminal_status",
    ["complete", "failed", "budget_exhausted", "cancelled"],
)
@pytest.mark.asyncio
async def test_terminal_missions_cannot_be_claimed(tmp_path: Path, terminal_status: str) -> None:
    path = tmp_path / f"terminal-{terminal_status}.db"
    owner = PublicStateStore(path)
    contender = PublicStateStore(path)
    await owner.initialize()
    await contender.initialize()
    owner_lease = "terminal-owner"
    generation = 3
    await _create_mission(
        owner,
        "mission-terminal",
        lease=owner_lease,
        generation=generation,
        expires_at="2000-01-01T00:00:00+00:00",
    )

    status = "running"
    version = 0
    if terminal_status == "complete":
        assert await owner.cas_mission_status(
            "mission-terminal",
            expect_status=status,
            expect_version=version,
            expect_lease_generation=generation,
            expect_lease_token=owner_lease,
            new_status="verifying",
        )
        status = "verifying"
        version = 1
    assert await owner.cas_mission_status(
        "mission-terminal",
        expect_status=status,
        expect_version=version,
        expect_lease_generation=generation,
        expect_lease_token=owner_lease,
        new_status=terminal_status,
        clear_lease=True,
    )

    assert await contender.claim_mission(
        "mission-terminal",
        lease_token="new-owner",  # noqa: S106
    ) == (False, generation + 1)
    mission = await owner.load_mission("mission-terminal")
    assert mission is not None
    assert mission["status"] == terminal_status
    assert mission["lease_token"] is None
    assert int(mission["lease_generation"]) == generation + 1


@pytest.mark.parametrize("paused_status", ["waiting_timer", "dormant", "escalated"])
@pytest.mark.asyncio
async def test_paused_or_escalated_missions_require_explicit_requeue(
    tmp_path: Path, paused_status: str
) -> None:
    store = PublicStateStore(tmp_path / f"paused-{paused_status}.db")
    await store.initialize()
    lease = "paused-owner"  # noqa: S105
    await _create_mission(
        store,
        "mission-paused",
        lease=lease,
        generation=2,
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert await store.cas_mission_status(
        "mission-paused",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=2,
        expect_lease_token=lease,
        new_status=paused_status,
        clear_lease=True,
    )

    assert await store.claim_mission(
        "mission-paused", lease_token="reattach-worker"  # noqa: S106
    ) == (False, 3)
    mission = await store.load_mission("mission-paused")
    assert mission is not None
    assert mission["status"] == paused_status
    assert mission["lease_token"] is None


@pytest.mark.asyncio
async def test_status_cas_can_fence_on_lease_token(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "cas-owner.db")
    await store.initialize()
    owner_lease = "cas-owner"
    wrong_lease = "wrong-owner"
    await _create_mission(
        store,
        "mission-cas",
        lease=owner_lease,
        generation=4,
        expires_at=lease_expiry_iso(ttl_seconds=120),
    )

    assert not await store.cas_mission_status(
        "mission-cas",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=4,
        expect_lease_token=wrong_lease,
        new_status="verifying",
    )
    assert await store.cas_mission_status(
        "mission-cas",
        expect_status="running",
        expect_version=0,
        expect_lease_generation=4,
        expect_lease_token=owner_lease,
        new_status="verifying",
    )
    assert not await store.cas_mission_status(
        "mission-cas",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=4,
        expect_lease_token=wrong_lease,
        new_status="complete",
        clear_lease=True,
    )
    assert await store.cas_mission_status(
        "mission-cas",
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=4,
        expect_lease_token=owner_lease,
        new_status="complete",
        clear_lease=True,
    )


@pytest.mark.asyncio
async def test_envelope_binding_is_fenced_by_exact_owner(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "envelope-owner.db")
    await store.initialize()
    await _create_mission(
        store,
        "mission-envelope",
        lease="envelope-owner",
        generation=4,
        expires_at=lease_expiry_iso(ttl_seconds=120),
    )

    assert not await store.touch_mission_envelope(
        "mission-envelope",
        envelope_version=7,
        lease_token="stale-owner",  # noqa: S106
        lease_generation=4,
    )
    untouched = await store.load_mission("mission-envelope")
    assert untouched is not None
    assert untouched["envelope_version"] == 1
    assert "bound_envelope_version" not in untouched["package"]

    assert await store.touch_mission_envelope(
        "mission-envelope",
        envelope_version=7,
        lease_token="envelope-owner",  # noqa: S106
        lease_generation=4,
    )
    bound = await store.load_mission("mission-envelope")
    assert bound is not None
    assert bound["envelope_version"] == 7
    assert bound["package"]["bound_envelope_version"] == 7


@pytest.mark.asyncio
async def test_poll_mirror_rejects_repeated_status_from_newer_generation(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "mirror-aba.db")
    await store.initialize()
    mission_id = "mission-mirror"
    first_owner = "first-owner"  # noqa: S105
    second_owner = "second-owner"  # noqa: S105
    await _create_mission(
        store,
        mission_id,
        lease=first_owner,
        generation=1,
        expires_at=lease_expiry_iso(ttl_seconds=120),
    )
    assert await store.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=0,
        expect_lease_generation=1,
        expect_lease_token=first_owner,
        new_status="verifying",
    )
    assert await store.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=1,
        expect_lease_generation=1,
        expect_lease_token=first_owner,
        new_status="waiting_event",
        clear_lease=True,
    )
    stale = await store.load_mission(mission_id)
    assert stale is not None

    claimed, generation = await store.claim_mission(
        mission_id,
        lease_token=second_owner,
    )
    assert claimed
    assert await store.cas_mission_status(
        mission_id,
        expect_status="running",
        expect_version=int(stale["checkpoint_version"]),
        expect_lease_generation=generation,
        expect_lease_token=second_owner,
        new_status="verifying",
    )
    current = await store.load_mission(mission_id)
    assert current is not None
    assert await store.cas_mission_status(
        mission_id,
        expect_status="verifying",
        expect_version=int(current["checkpoint_version"]),
        expect_lease_generation=generation,
        expect_lease_token=second_owner,
        new_status="waiting_event",
        clear_lease=True,
    )

    assert not await store.mirror_mission_result(
        mission_id,
        expect_status="waiting_event",
        expect_checkpoint_version=int(stale["checkpoint_version"]),
        expect_lease_generation=int(stale["lease_generation"]),
        job_id=f"job-{mission_id}",
        job_status="needs_continuation",
        autonomy_status="needs_continuation",
        payload={"status": "continue", "text": "stale"},
    )
    assert await store.load_agent_job(f"job-{mission_id}") is None


@pytest.mark.asyncio
async def test_stale_sweeper_snapshot_cannot_revoke_renewed_lease(
    tmp_path: Path,
) -> None:
    store = PublicStateStore(tmp_path / "sweeper-heartbeat.db")
    await store.initialize()
    mission_id = "mission-heartbeat-race"
    owner = "heartbeat-owner"  # noqa: S105
    await _create_mission(
        store,
        mission_id,
        lease=owner,
        generation=5,
        expires_at="2000-01-01T00:00:00+00:00",
    )
    expired = await store.list_expired_mission_leases()
    snapshot = next(row for row in expired if row["mission_id"] == mission_id)

    assert await store.heartbeat_mission(
        mission_id,
        lease_token=owner,
        lease_generation=5,
        ttl_seconds=120,
    )
    assert not await store.requeue_expired_mission(
        mission_id,
        expect_status=str(snapshot["status"]),
        expect_version=int(snapshot["checkpoint_version"]),
        expect_lease_generation=int(snapshot["lease_generation"]),
        expect_lease_expires_at=str(snapshot["lease_expires_at"]),
    )
    mission = await store.load_mission(mission_id)
    assert mission is not None
    assert mission["status"] == "running"
    assert mission["lease_token"] == owner
    assert mission["lease_generation"] == 5
    assert mission["lease_expires_at"] != snapshot["lease_expires_at"]


@pytest.mark.asyncio
async def test_mission_token_and_artifact_read_helpers(tmp_path: Path) -> None:
    store = PublicStateStore(tmp_path / "mission-reads.db")
    await store.initialize()
    continue_token = await _create_mission(
        store,
        "mission-reads",
        lease="read-owner",
        generation=1,
        expires_at=lease_expiry_iso(ttl_seconds=120),
    )
    await store.put_mission_artifact(
        "mission-reads",
        "artifact-digest",
        kind="result",
        sealed="sealed body",
        projection="public projection",
    )

    mission = await store.load_mission_by_token(continue_token.upper())
    assert mission is not None
    assert mission["mission_id"] == "mission-reads"
    assert mission["package"] == {"task": "mission-reads"}
    assert await store.load_mission_by_token("f" * 32) is None
    with pytest.raises(ValueError, match="32-character hex"):
        await store.load_mission_by_token("not-a-token")

    artifact = await store.get_mission_artifact("artifact-digest")
    assert artifact is not None
    assert artifact["mission_id"] == "mission-reads"
    assert artifact["kind"] == "result"
    assert artifact["sealed"] == "sealed body"
    assert artifact["projection"] == "public projection"
    assert await store.get_mission_artifact("missing") is None
