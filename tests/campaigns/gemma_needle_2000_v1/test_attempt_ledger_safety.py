from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evals.campaigns.gemma_needle_2000_v1.attempt_ledger import (
    AttemptConflictError,
    AttemptLedger,
    AttemptLimitExceededError,
    AttemptStatus,
    InvalidTerminalTransitionError,
    RunContractConflictError,
    RunLeaseConflictError,
    RunLeaseExpiredError,
)
from evals.campaigns.gemma_needle_2000_v1.stage1_artifacts import (
    ArtifactRef,
    PrivateArtifactStore,
)


RUN_A = "stage1-mock-run-a"
RUN_B = "stage1-mock-run-b"
OWNER_A = "owner-token-process-a-0001"
OWNER_B = "owner-token-process-b-0002"


@dataclass
class MutableClock:
    value: datetime = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _private_ledger_path(tmp_path: Path, name: str = "attempts.db") -> Path:
    parent = tmp_path / "private-ledger"
    parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    return parent / name


def _acquire(
    ledger: AttemptLedger,
    clock: MutableClock,
    *,
    run_id: str = RUN_A,
    owner_token: str = OWNER_A,
    manifest_label: str = "stage1-manifest",
    config_label: str = "stage1-config",
    minutes: int = 60,
):
    return ledger.acquire_run_lease(
        run_id=run_id,
        owner_token=owner_token,
        campaign_id="gemma-needle-2000-v1",
        schema_version="stage1-v2",
        manifest_digest=_digest(manifest_label),
        config_digest=_digest(config_label),
        lease_deadline=clock() + timedelta(minutes=minutes),
    )


def _claim_kwargs(
    index: int = 0,
    *,
    role: str = "seed_author",
    run_id: str = RUN_A,
    owner_token: str = OWNER_A,
) -> dict[str, str]:
    logical_key = AttemptLedger.make_logical_work_key(
        role,
        "tool_selection",
        f"root-{index}",
    )
    return {
        "role": role,
        "logical_work_key": logical_key,
        "run_id": run_id,
        "owner_token": owner_token,
        "campaign_id": "gemma-needle-2000-v1",
        "schema_version": "stage1-v2",
        "manifest_digest": _digest("stage1-manifest"),
        "config_digest": _digest("stage1-config"),
        "template_digest": _digest(f"template-{role}"),
        "provider": "mock",
        "model": "deterministic-fixture",
        "request_digest": _digest(f"request-{role}-{index}"),
        "cache_digest": _digest(f"cache-{role}-{index}"),
        "root_reference": f"root-{index}",
    }


def _output(store: PrivateArtifactStore, label: str = "output") -> ArtifactRef:
    return store.write_content_addressed(
        ("stage1-mock-run",),
        "output",
        {"label": label, "verified": True},
    )


def test_claim_is_deterministic_idempotent_and_terminal_work_is_not_retried(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        first = ledger.claim(**_claim_kwargs())
        repeated = ledger.claim(**_claim_kwargs())

        assert first.claimed is True
        assert repeated.claimed is False
        assert repeated.work_item_id == first.work_item_id
        assert repeated.status is AttemptStatus.STARTED
        assert ledger.get_total_attempts(RUN_A) == 1

        ledger.complete(
            first.work_item_id,
            owner_token=OWNER_A,
            response_digest=_digest("response"),
            receipt_digest=_digest("receipt"),
            output_artifact=_output(store),
            artifact_verifier=store.read,
        )
        completed = ledger.claim(**_claim_kwargs())
        assert completed.claimed is False
        assert completed.status is AttemptStatus.COMPLETED
        assert ledger.get_total_attempts(RUN_A) == 1


@pytest.mark.parametrize("field", ["request_digest", "template_digest", "provider"])
def test_same_logical_key_rejects_changed_work_provenance(
    tmp_path: Path, field: str
) -> None:
    clock = MutableClock()
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        ledger.claim(**_claim_kwargs())
        contradictory = _claim_kwargs()
        contradictory[field] = (
            _digest(f"different-{field}")
            if field.endswith("digest")
            else "other-provider"
        )
        with pytest.raises(AttemptConflictError, match=field):
            ledger.claim(**contradictory)


def test_persisted_contract_rejects_reopened_limit_and_config_drift(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    path = _private_ledger_path(tmp_path)
    with AttemptLedger(
        path,
        total_limit=1,
        role_limits={"seed_author": 1},
        clock=clock,
    ) as ledger:
        _acquire(ledger, clock)
        ledger.claim(**_claim_kwargs())

    with AttemptLedger(
        path,
        total_limit=2,
        role_limits={"seed_author": 2},
        clock=clock,
    ) as ledger:
        with pytest.raises(
            RunContractConflictError, match="total_limit|role_limits_json"
        ):
            _acquire(ledger, clock)

    with AttemptLedger(
        path,
        total_limit=1,
        role_limits={"seed_author": 1},
        clock=clock,
    ) as ledger:
        with pytest.raises(RunContractConflictError, match="config_digest"):
            _acquire(ledger, clock, config_label="changed-config")
        assert ledger.get_total_attempts(RUN_A) == 1


def test_atomic_claim_enforces_persisted_role_and_total_limits_under_concurrency(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    ledger = AttemptLedger(
        _private_ledger_path(tmp_path),
        total_limit=10,
        role_limits={"seed_author": 7, "critic": 10},
        clock=clock,
    )
    _acquire(ledger, clock)

    def claim_seed(index: int) -> bool:
        try:
            return ledger.claim(**_claim_kwargs(index)).claimed
        except AttemptLimitExceededError:
            return False

    with ThreadPoolExecutor(max_workers=20) as executor:
        seed_results = list(executor.map(claim_seed, range(40)))

    assert sum(seed_results) == 7
    assert ledger.get_role_attempts(RUN_A, "seed_author") == 7
    critic_results = []
    for index in range(10):
        try:
            critic_results.append(
                ledger.claim(**_claim_kwargs(index, role="critic")).claimed
            )
        except AttemptLimitExceededError:
            critic_results.append(False)
    assert sum(critic_results) == 3
    assert ledger.get_total_attempts(RUN_A) == 10
    ledger.close()


def test_active_lease_blocks_second_owner_and_reconciliation(tmp_path: Path) -> None:
    clock = MutableClock()
    path = _private_ledger_path(tmp_path)
    first = AttemptLedger(path, clock=clock)
    second = AttemptLedger(path, clock=clock)
    _acquire(first, clock)
    claim = first.claim(**_claim_kwargs())

    with pytest.raises(RunLeaseConflictError, match="another owner"):
        _acquire(second, clock, owner_token=OWNER_B)
    with pytest.raises(RunLeaseConflictError, match="active run lease"):
        first.reconcile_open_attempts(run_id=RUN_A, owner_token=OWNER_A)
    assert first.get_attempt(claim.work_item_id)["status"] == "started"
    first.close()
    second.close()


def test_expired_takeover_reconciles_once_and_cannot_be_completed_by_old_owner(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    path = _private_ledger_path(tmp_path)
    first = AttemptLedger(path, clock=clock)
    second = AttemptLedger(path, clock=clock)
    store = PrivateArtifactStore(tmp_path / "artifacts")
    _acquire(first, clock, minutes=1)
    claim = first.claim(**_claim_kwargs())
    clock.advance(minutes=2)

    takeover = _acquire(second, clock, owner_token=OWNER_B)
    assert takeover.taken_over is True
    assert takeover.reconciled_work_item_ids == (claim.work_item_id,)
    assert second.get_attempt(claim.work_item_id)["status"] == "indeterminate"
    with pytest.raises(RunLeaseConflictError, match="different run lease"):
        first.complete(
            claim.work_item_id,
            owner_token=OWNER_A,
            response_digest=_digest("late-response"),
            receipt_digest=_digest("late-receipt"),
            output_artifact=_output(store, "late"),
            artifact_verifier=store.read,
        )

    assert _acquire(second, clock, owner_token=OWNER_B).reconciled_work_item_ids == ()
    first.close()
    second.close()


def test_expired_lease_cannot_claim_until_safely_reacquired(tmp_path: Path) -> None:
    clock = MutableClock()
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock, minutes=1)
        clock.advance(minutes=2)
        with pytest.raises(RunLeaseExpiredError):
            ledger.claim(**_claim_kwargs())
        _acquire(ledger, clock)
        assert ledger.claim(**_claim_kwargs()).claimed is True


def test_terminal_result_at_exact_lease_deadline_fails_closed(tmp_path: Path) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock, minutes=1)
        claim = ledger.claim(**_claim_kwargs())
        output = _output(store, "arrived-at-deadline")
        clock.advance(minutes=1)

        with pytest.raises(RunLeaseExpiredError, match="terminal result"):
            ledger.complete(
                claim.work_item_id,
                owner_token=OWNER_A,
                response_digest=_digest("late-response"),
                receipt_digest=_digest("late-receipt"),
                output_artifact=output,
                artifact_verifier=store.read,
            )

        assert ledger.get_attempt(claim.work_item_id)["status"] == "started"
        assert ledger.reconcile_open_attempts(
            run_id=RUN_A,
            owner_token=OWNER_A,
        ) == [claim.work_item_id]
        assert ledger.get_attempt(claim.work_item_id)["status"] == "indeterminate"


def test_completion_requires_artifact_existence_and_digest_verification(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    missing = ArtifactRef(
        digest=hashlib.sha256(b"{}").hexdigest(),
        relative_path="stage1-mock-run/missing.json",
        size_bytes=2,
    )
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        claim = ledger.claim(**_claim_kwargs())

        with pytest.raises(ValueError, match="artifact existence and digest verifier"):
            ledger.complete(
                claim.work_item_id,
                owner_token=OWNER_A,
                response_digest=_digest("response"),
                receipt_digest=_digest("receipt"),
                output_artifact=missing,
                artifact_verifier=None,  # type: ignore[arg-type]
            )
        with pytest.raises(FileNotFoundError):
            ledger.complete(
                claim.work_item_id,
                owner_token=OWNER_A,
                response_digest=_digest("response"),
                receipt_digest=_digest("receipt"),
                output_artifact=missing,
                artifact_verifier=store.read,
            )
        assert ledger.get_attempt(claim.work_item_id)["status"] == "started"


def test_run_rotation_has_independent_identity_and_scoped_counts(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock, run_id=RUN_A)
        first = ledger.claim(**_claim_kwargs(run_id=RUN_A))
        _acquire(ledger, clock, run_id=RUN_B)
        second = ledger.claim(**_claim_kwargs(run_id=RUN_B))

        assert first.work_item_id != second.work_item_id
        assert ledger.get_total_attempts(RUN_A) == 1
        assert ledger.get_total_attempts(RUN_B) == 1


def test_only_owner_can_make_one_terminal_transition(tmp_path: Path) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        claim = ledger.claim(**_claim_kwargs())
        with pytest.raises(RunLeaseConflictError):
            ledger.fail(
                claim.work_item_id,
                owner_token=OWNER_B,
                terminal_code="foreign_owner",
            )
        ledger.complete(
            claim.work_item_id,
            owner_token=OWNER_A,
            response_digest=_digest("response"),
            receipt_digest=_digest("receipt"),
            output_artifact=_output(store),
            artifact_verifier=store.read,
        )
        with pytest.raises(InvalidTerminalTransitionError, match="already terminal"):
            ledger.fail(
                claim.work_item_id,
                owner_token=OWNER_A,
                terminal_code="contradictory_failure",
            )


def test_terminal_transition_race_has_one_winner(tmp_path: Path) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    ledger = AttemptLedger(_private_ledger_path(tmp_path), clock=clock)
    _acquire(ledger, clock)
    claim = ledger.claim(**_claim_kwargs())
    output = _output(store)

    def complete_once(_: int) -> bool:
        try:
            ledger.complete(
                claim.work_item_id,
                owner_token=OWNER_A,
                response_digest=_digest("response"),
                receipt_digest=_digest("receipt"),
                output_artifact=output,
                artifact_verifier=store.read,
            )
            return True
        except InvalidTerminalTransitionError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(complete_once, range(8)))
    assert sum(results) == 1
    ledger.close()


def test_restart_reconstructs_artifacts_and_deterministic_report_views(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    path = _private_ledger_path(tmp_path)
    store = PrivateArtifactStore(tmp_path / "artifacts")
    output = _output(store, "restart-safe")
    with AttemptLedger(path, clock=clock) as ledger:
        _acquire(ledger, clock)
        claim = ledger.claim(**_claim_kwargs())
        ledger.complete(
            claim.work_item_id,
            owner_token=OWNER_A,
            response_digest=_digest("response"),
            receipt_digest=_digest("receipt"),
            output_artifact=output,
            artifact_verifier=store.read,
        )
        first_digest = ledger.snapshot_digest(RUN_A)

    with AttemptLedger(path, clock=clock) as resumed:
        discovered = resumed.get_output_artifact(claim.work_item_id)
        assert discovered == output
        assert store.read(discovered)["label"] == "restart-safe"
        assert resumed.snapshot_digest(RUN_A) == first_digest
        assert [row["work_item_id"] for row in resumed.list_attempts(RUN_A)] == [
            claim.work_item_id
        ]
        summary = resumed.summarize_run(RUN_A)
        assert summary["total_attempts"] == 1
        assert summary["status_counts"]["completed"] == 1
        assert summary["role_counts"]["seed_author"] == 1


def test_provenance_contract_and_artifact_reference_are_recorded(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = PrivateArtifactStore(tmp_path / "artifacts")
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        kwargs = _claim_kwargs()
        claim = ledger.claim(**kwargs)
        output = _output(store)
        ledger.complete(
            claim.work_item_id,
            owner_token=OWNER_A,
            response_digest=_digest("response"),
            receipt_digest=_digest("receipt"),
            output_artifact=output,
            artifact_verifier=store.read,
        )
        row = ledger.get_attempt(claim.work_item_id)
        snapshot = ledger.snapshot_run(RUN_A)

    for field in (
        "run_id",
        "campaign_id",
        "schema_version",
        "manifest_digest",
        "config_digest",
        "template_digest",
        "provider",
        "model",
        "request_digest",
        "cache_digest",
    ):
        assert row[field] == kwargs[field]
    assert row["output_digest"] == f"sha256:{output.digest}"
    assert row["output_relative_path"] == output.relative_path
    assert row["output_size_bytes"] == output.size_bytes
    assert "lease_owner_digest" not in snapshot["contract"]
    assert snapshot["contract"]["total_limit"] == 120
    assert snapshot["contract"]["role_limits"]["seed_author"] == 30


def test_untrusted_metadata_and_human_reason_text_are_rejected(tmp_path: Path) -> None:
    clock = MutableClock()
    with AttemptLedger(_private_ledger_path(tmp_path), clock=clock) as ledger:
        _acquire(ledger, clock)
        unsafe_reference = _claim_kwargs()
        unsafe_reference["root_reference"] = "user@example.com"
        with pytest.raises(ValueError, match="opaque identifier"):
            ledger.claim(**unsafe_reference)

        secret_reference = _claim_kwargs()
        secret_reference["root_reference"] = "xai-ABCDEFGHIJKLMNOPQRSTUVWX"
        with pytest.raises(ValueError, match="secret-like"):
            ledger.claim(**secret_reference)

        claim = ledger.claim(**_claim_kwargs())
        with pytest.raises(ValueError, match="machine-readable"):
            ledger.fail(
                claim.work_item_id,
                owner_token=OWNER_A,
                terminal_code="Human readable provider error",
            )


def test_private_permissions_wal_mode_and_owner_token_non_persistence(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    path = _private_ledger_path(tmp_path)
    ledger = AttemptLedger(path, clock=clock)
    _acquire(ledger, clock)
    ledger.claim(**_claim_kwargs())

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
            assert OWNER_A.encode() not in sidecar.read_bytes()
    assert OWNER_A.encode() not in path.read_bytes()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.close()
    ledger.close()


def test_rejects_non_private_directory_database_symlinks_and_repo_paths(
    tmp_path: Path,
) -> None:
    public_parent = tmp_path / "public-parent"
    public_parent.mkdir(mode=0o755)
    os.chmod(public_parent, 0o755)
    with pytest.raises(ValueError, match="0700"):
        AttemptLedger(public_parent / "attempts.db")

    path = _private_ledger_path(tmp_path, "public.db")
    path.touch(mode=0o600)
    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="0600"):
        AttemptLedger(path)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        AttemptLedger(linked_parent / "attempts.db")
    with pytest.raises(ValueError, match="outside every repository"):
        AttemptLedger(Path.cwd() / ".unsafe-attempt-ledger.db")


def test_schema_fingerprint_rejects_version_spoofed_database(tmp_path: Path) -> None:
    path = _private_ledger_path(tmp_path, "spoofed.db")
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE attempts(work_item_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(RuntimeError, match="schema objects"):
        AttemptLedger(path)


def test_invalid_digest_unknown_role_and_closed_ledger_fail_closed(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    ledger = AttemptLedger(_private_ledger_path(tmp_path), clock=clock)
    _acquire(ledger, clock)
    invalid_digest = _claim_kwargs()
    invalid_digest["request_digest"] = "not-a-digest"
    with pytest.raises(ValueError, match="SHA-256"):
        ledger.claim(**invalid_digest)

    unknown_role = _claim_kwargs()
    unknown_role["role"] = "unbudgeted-role"
    with pytest.raises(ValueError, match="No provider-attempt budget"):
        ledger.claim(**unknown_role)
    assert ledger.get_total_attempts(RUN_A) == 0

    ledger.close()
    with pytest.raises(RuntimeError, match="closed"):
        ledger.get_total_attempts(RUN_A)
