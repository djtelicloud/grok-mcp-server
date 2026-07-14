from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from src.provider_harvest import (
    DEFAULT_WORKER_EPISODE_COLLECTION,
    ProviderAttemptHarvester,
    _ProviderHarvestEffectAuthority,
    XAIWorkerEpisodeUploader,
    worker_episode_document,
    worker_episode_document_name,
)
from src import utils as provider_utils
from src.providers import (
    CredentialPlane,
    GrokSupervisorBinding,
    ProviderAttemptResult,
    ProviderAttemptStart,
    ProviderChannel,
    ProviderId,
    ProviderMessage,
    ProviderReceipt,
    ProviderRequest,
    ProviderResponse,
    ProviderTokenUsage,
    RouteClass,
)
from src.utils import GrokSessionStore


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _request(request_id: str) -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        supervision=GrokSupervisorBinding(
            session_id="grok-session-1",
            objective_id="objective-1",
            route_decision_id="route-1",
            ttl_expires_at=datetime(2035, 1, 1, tzinfo=UTC),
        ),
        route=RouteClass.PLANNING,
        model="gpt-5.1",
        messages=[ProviderMessage(role="user", content="Give Grok one observation.")],
    )


def _start(index: int) -> ProviderAttemptStart:
    return ProviderAttemptStart(
        attempt_id=f"attempt-{index}",
        delegation_id=f"delegation-{index}",
        attempt_ordinal=1,
        supervisor_plane="CLI",
        supervisor_model="grok-4.5",
        provider=ProviderId.OPENAI,
        channel=ProviderChannel.OPENAI_API,
        credential_plane=CredentialPlane.METERED_API,
        requested_model="gpt-5.1",
        request=_request(f"request-{index}"),
    )


def _result(start: ProviderAttemptStart, text: str = "Subordinate observation."):
    receipt = ProviderReceipt(
        request_id=start.request.request_id,
        supervision=start.request.supervision,
        provider=start.provider,
        channel=start.channel,
        credential_plane=start.credential_plane,
        route=start.request.route,
        requested_model=start.requested_model,
        resolved_model=start.requested_model,
        model_source="provider_reported",
        endpoint_host="api.openai.com",
        endpoint_kind="first_party_api",
        credential_kind="api_key",
        region="global",
        duration_ms=17,
        usage=ProviderTokenUsage(
            input_tokens=12,
            output_tokens=4,
            total_tokens=16,
            source="provider_exact",
        ),
    )
    return ProviderAttemptResult(
        status="returned",
        response=ProviderResponse(
            provider=start.provider,
            channel=start.channel,
            model=start.requested_model,
            text=text,
            finish_reason="stop",
            receipt=receipt,
        ),
    )


async def _completed(store: GrokSessionStore, index: int = 1) -> dict:
    start = _start(index)
    await store.begin_provider_attempt(start)
    await store.complete_provider_attempt(start.attempt_id, _result(start))
    return (await store.list_provider_attempts(delegation_id=start.delegation_id))[0]


class FakeCollections:
    def __init__(self, *, incompatible: bool = False, fail_mode: str | None = None):
        self.calls: list[tuple[str, object]] = []
        self.documents: dict[str, object] = {}
        self.fail_mode = fail_mode
        self.collection = None
        if incompatible:
            self.collection = SimpleNamespace(
                collection_id="collection-existing",
                collection_name=DEFAULT_WORKER_EPISODE_COLLECTION,
                field_definitions=[],
            )

    @staticmethod
    def _definition(key: str):
        return SimpleNamespace(
            key=key, required=True, unique=True, inject_into_chunk=False
        )

    def list(self, *, limit: int, filter: str):  # noqa: A002
        self.calls.append(("list", filter))
        collections = [self.collection] if self.collection is not None else []
        return SimpleNamespace(collections=collections)

    def create(self, *, name: str, field_definitions: list, description: str):
        self.calls.append(("create", (name, field_definitions, description)))
        self.collection = SimpleNamespace(
            collection_id="collection-created",
            collection_name=name,
            field_definitions=[
                self._definition("episode_id"),
                self._definition("document_digest"),
            ],
        )
        return self.collection

    def list_documents(
        self, collection_id: str, *, limit: int, filter: str  # noqa: A002
    ):
        self.calls.append(("list_documents", (collection_id, limit, filter)))
        episode_id = filter.split('"')[1]
        document = self.documents.get(episode_id)
        return SimpleNamespace(documents=[document] if document else [])

    def upload_document(
        self,
        collection_id: str,
        name: str,
        data: bytes,
        *,
        fields: dict[str, str],
    ):
        self.calls.append(("upload_document", (collection_id, name, data, fields)))
        episode_id = fields["episode_id"]
        document = SimpleNamespace(
            file_metadata=SimpleNamespace(
                file_id=f"file-{episode_id[:16]}",
                name=name,
            ),
            fields=dict(fields),
        )
        if self.fail_mode == "before_write":
            raise RuntimeError("upload failed")
        if self.fail_mode == "secret_before_write":
            raise RuntimeError("upload echoed xai-management-test-secret")
        self.documents[episode_id] = document
        if self.fail_mode == "after_write":
            raise TimeoutError("response lost after write")
        return document


def _uploader(service: FakeCollections) -> XAIWorkerEpisodeUploader:
    client = SimpleNamespace(collections=service)
    return XAIWorkerEpisodeUploader(
        client_factory=lambda: client,
        unavailable_reason=lambda: None,
    )


def _effect_authority() -> _ProviderHarvestEffectAuthority:
    return _ProviderHarvestEffectAuthority.for_seconds(30)


def _downgrade_current_db_to_v16(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "ALTER TABLE provider_attempts DROP COLUMN harvest_document_json;"
        )
        connection.execute(
            "ALTER TABLE provider_attempts DROP COLUMN harvest_document_digest;"
        )
        connection.execute(
            "ALTER TABLE provider_attempts DROP COLUMN harvest_episode_id;"
        )
        connection.execute("PRAGMA user_version = 16;")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_terminal_outbox_leases_resumes_and_never_deadletters(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "lease.db", clock=clock)
    await store.begin_provider_attempt(_start(1))
    terminal = await _completed(store, 2)
    assert terminal["harvest_status"] == "pending"
    assert datetime.fromisoformat(terminal["harvest_next_at"]).tzinfo is not None
    first = await store.lease_provider_attempts_for_harvest("lease-1", 30, 25)
    assert [row["attempt_id"] for row in first] == [terminal["attempt_id"]]
    assert first[0]["harvest_attempts"] == 1
    clock.advance(1)
    assert await store.lease_provider_attempts_for_harvest("lease-other", 30, 25) == []

    clock.advance(30)
    resumed = await store.lease_provider_attempts_for_harvest("lease-2", 30, 25)
    assert resumed[0]["harvest_attempts"] == 2
    assert await store.mark_provider_attempt_harvest_retry(
        terminal["attempt_id"],
        "lease-2",
        "temporary",
        58,
    )
    clock.advance(57)
    assert await store.lease_provider_attempts_for_harvest("lease-early", 30, 25) == []

    await store._conn.execute(
        "UPDATE provider_attempts SET harvest_attempts = 1000000 "
        "WHERE attempt_id = ?",
        (terminal["attempt_id"],),
    )
    await store._conn.commit()
    clock.advance(1)
    final_lease = await store.lease_provider_attempts_for_harvest("lease-3", 30, 25)
    assert final_lease[0]["harvest_attempts"] == 1000001
    assert await store.mark_provider_attempt_harvest_synced(
        terminal["attempt_id"], "lease-3", "remote-file-1"
    )
    assert not await store.mark_provider_attempt_harvest_synced(
        terminal["attempt_id"], "old-lease", "remote-file-1"
    )
    with pytest.raises(ValueError, match="remote identity conflicts"):
        await store.mark_provider_attempt_harvest_synced(
            terminal["attempt_id"], "old-lease", "remote-file-2"
        )

    rows = await store.list_provider_attempts()
    assert rows[0]["transport_status"] == "started"
    assert rows[0]["harvest_status"] == "held"
    assert rows[1]["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_lost_batch_lease_does_not_abort_remaining_uploads(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "lease-loss.db", clock=clock)
    await _completed(store, 1)
    await _completed(store, 2)
    service = FakeCollections()
    class ReclaimingStore:
        def __init__(self):
            self.reclaimed = False

        async def lease_provider_attempts_for_harvest(self, *args):
            return await store.lease_provider_attempts_for_harvest(*args)

        async def provider_attempt_harvest_lease_is_fresh(self, *args):
            return await store.provider_attempt_harvest_lease_is_fresh(*args)

        async def mark_provider_attempt_harvest_synced(self, *args):
            if not self.reclaimed:
                self.reclaimed = True
                clock.advance(6)
                await store.lease_provider_attempts_for_harvest(
                    "new-owner", 30, 1
                )
            return await store.mark_provider_attempt_harvest_synced(*args)

        async def mark_provider_attempt_harvest_retry(self, *args):
            return await store.mark_provider_attempt_harvest_retry(*args)

    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=2,
        lease_seconds=5,
        lease_id_factory=lambda: "old-owner",
    ).run_once(ReclaimingStore())
    assert result.status == "partial"
    assert result.lease_lost == 1
    assert result.synced == 1
    assert [name for name, _ in service.calls].count("upload_document") == 2
    rows = await store.list_provider_attempts()
    assert rows[0]["harvest_lease_id"] == "new-owner"
    assert rows[1]["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_owner_without_enough_remaining_lease_cannot_start_remote_effect(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "stale-before-effect.db", clock=clock)
    row = await _completed(store)
    stale_rows = await store.lease_provider_attempts_for_harvest(
        "stale-owner", 5, 1
    )
    assert len(stale_rows) == 1
    # The owner still holds the row for one second, but a one-second blocking
    # call plus the safety margin no longer fits inside its authority window.
    clock.advance(4)

    class DelayedWorkerStore:
        def __init__(self):
            self.returned_stale_work = False

        async def lease_provider_attempts_for_harvest(self, *args):
            if self.returned_stale_work:
                return []
            self.returned_stale_work = True
            return stale_rows

        async def provider_attempt_harvest_lease_is_fresh(self, *args):
            return await store.provider_attempt_harvest_lease_is_fresh(*args)

        async def mark_provider_attempt_harvest_synced(self, *args):
            return await store.mark_provider_attempt_harvest_synced(*args)

        async def mark_provider_attempt_harvest_retry(self, *args):
            return await store.mark_provider_attempt_harvest_retry(*args)

    service = FakeCollections()
    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=1,
        lease_seconds=5,
        call_timeout_seconds=1,
        lease_id_factory=lambda: "stale-owner",
    ).run_once(DelayedWorkerStore())
    assert result.status == "partial"
    assert result.lease_lost == 1
    assert service.calls == []
    stored = (await store.list_provider_attempts())[0]
    assert stored["attempt_id"] == row["attempt_id"]
    assert stored["harvest_status"] == "leased"
    await store.close()


@pytest.mark.asyncio
async def test_missing_credentials_or_client_leave_rows_pending_without_cloud_calls(
    tmp_path, monkeypatch
):
    store = GrokSessionStore(tmp_path / "unavailable.db")
    await _completed(store)
    factory_calls = 0

    def forbidden_factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("client must not be constructed without management auth")

    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    monkeypatch.setattr(provider_utils, "XAI_API_KEY", "xai-inference-test-value")
    missing_management = XAIWorkerEpisodeUploader(client_factory=forbidden_factory)
    result = await ProviderAttemptHarvester(
        uploader=missing_management
    ).run_once(store)
    assert result.status == "unavailable"
    assert result.reason == "management_key_missing"
    assert factory_calls == 0
    row = (await store.list_provider_attempts())[0]
    assert row["harvest_status"] == "pending"
    assert row["harvest_attempts"] == 0

    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "management-test-value")
    monkeypatch.setattr(provider_utils, "XAI_API_KEY", "")
    missing_inference = XAIWorkerEpisodeUploader(client_factory=forbidden_factory)
    result = await ProviderAttemptHarvester(uploader=missing_inference).run_once(store)
    assert result.status == "unavailable"
    assert result.reason == "inference_client_missing"
    assert factory_calls == 0
    row = (await store.list_provider_attempts())[0]
    assert row["harvest_status"] == "pending"
    assert row["harvest_attempts"] == 0

    def missing_client():
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("no inference client")

    absent_client = XAIWorkerEpisodeUploader(
        client_factory=missing_client,
        unavailable_reason=lambda: None,
    )
    result = await ProviderAttemptHarvester(uploader=absent_client).run_once(store)
    assert result.status == "unavailable"
    assert result.reason == "inference_client_unavailable"
    assert factory_calls == 1
    row = (await store.list_provider_attempts())[0]
    assert row["harvest_status"] == "pending"
    assert row["harvest_attempts"] == 0
    await store.close()


@pytest.mark.asyncio
async def test_document_is_deterministic_explicitly_subordinate_and_unverified(tmp_path):
    store = GrokSessionStore(tmp_path / "document.db")
    row = await _completed(store)
    first = worker_episode_document(row)
    second = worker_episode_document(row)
    assert first == second
    assert worker_episode_document_name(row) == worker_episode_document_name(row)
    payload = json.loads(first)
    assert payload["version"] == "unigrok-worker-episode/v2"
    assert "episode_id" not in payload
    assert (
        row["harvest_document_digest"].removeprefix("sha256:")
        in worker_episode_document_name(row)
    )
    assert payload["authority"] == {
        "role": "subordinate_worker_evidence",
        "supervisor": "grok",
        "semantic_outcome": "unverified",
        "may_route": False,
        "may_verify": False,
        "may_harvest": False,
        "may_finalize": False,
    }
    assert payload["grok_supervisor"] == {
        "session_id": "grok-session-1",
        "objective_id": "objective-1",
        "route_decision_id": "route-1",
        "plane": "CLI",
        "model": "grok-4.5",
        "ttl_expires_at": "2035-01-01T00:00:00+00:00",
    }
    assert payload["worker"]["provider"] == "openai"
    assert payload["worker"]["channel"] == "openai_api"
    assert payload["transport"]["status"] == "returned"
    assert payload["content"]["prompt"] == row["prompt_text"]
    assert payload["content"]["output"] == row["output_text"]
    assert payload["receipt"] == row["receipt"]
    assert payload["receipt_redaction"] == "clean"
    assert payload["digests"]["ledger"]["receipt"] == row["receipt_digest"]
    assert payload["digests"]["cloud"]["receipt"] == (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                row["receipt"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    assert payload["usage"]["source"] == "provider_exact"
    assert payload["cost"]["source"] == "unavailable"
    await store.close()


@pytest.mark.asyncio
async def test_upload_uses_unique_identity_and_retry_is_idempotent(tmp_path):
    store = GrokSessionStore(tmp_path / "upload.db")
    row = await _completed(store)
    service = FakeCollections()
    uploader = _uploader(service)
    client, reason = uploader.prepare_client()
    assert reason is None

    first = uploader.upload(client, row, _effect_authority())
    second = uploader.upload(client, row, _effect_authority())
    assert first == second
    assert [name for name, _ in service.calls].count("upload_document") == 1
    create_call = next(value for name, value in service.calls if name == "create")
    assert create_call[0] == DEFAULT_WORKER_EPISODE_COLLECTION
    assert create_call[1] == [
        {
            "key": "episode_id",
            "required": True,
            "inject_into_chunk": False,
            "unique": True,
            "description": "Deterministic UniGrok subordinate episode identity",
        },
        {
            "key": "document_digest",
            "required": True,
            "inject_into_chunk": False,
            "unique": True,
            "description": "SHA-256 of the exact frozen episode document bytes",
        },
    ]
    upload_call = next(value for name, value in service.calls if name == "upload_document")
    assert upload_call[3] == {
        "episode_id": row["harvest_episode_id"],
        "document_digest": row["harvest_document_digest"],
    }
    assert upload_call[2] == row["harvest_document_json"].encode()
    assert await store.get_task_memory_count() == 0
    await store.close()


@pytest.mark.asyncio
async def test_timeout_after_remote_write_recovers_same_episode(tmp_path):
    store = GrokSessionStore(tmp_path / "uncertain.db")
    await _completed(store)
    service = FakeCollections(fail_mode="after_write")
    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        lease_id_factory=lambda: "lease-uncertain",
    ).run_once(store)
    assert result.status == "complete"
    assert result.synced == 1
    assert [name for name, _ in service.calls].count("upload_document") == 1
    row = (await store.list_provider_attempts())[0]
    assert row["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_timed_out_background_write_is_recovered_without_duplicate_upload(
    tmp_path,
):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "late-background-write.db", clock=clock)
    await _completed(store)

    class LateWriteCollections(FakeCollections):
        def __init__(self):
            super().__init__()
            self.upload_started = threading.Event()
            self.release_upload = threading.Event()

        def upload_document(self, *args, **kwargs):
            self.upload_started.set()
            if not self.release_upload.wait(timeout=5):
                raise RuntimeError("test upload was never released")
            return super().upload_document(*args, **kwargs)

    service = LateWriteCollections()
    harvester = ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=1,
        lease_seconds=5,
        call_timeout_seconds=1,
        backoff_base_seconds=1,
        backoff_max_seconds=1,
        lease_id_factory=lambda: "timed-out-owner",
    )
    first = await harvester.run_once(store)
    assert service.upload_started.is_set()
    assert first.status == "partial"
    assert first.retry_wait == 1
    assert (await store.list_provider_attempts())[0]["harvest_status"] == "retry_wait"

    service.release_upload.set()
    for _ in range(100):
        if service.documents:
            break
        await asyncio.sleep(0.01)
    assert service.documents

    clock.advance(1)
    harvester._lease_id_factory = lambda: "recovery-owner"
    second = await harvester.run_once(store)
    assert second.status == "complete"
    assert second.synced == 1
    assert [name for name, _ in service.calls].count("upload_document") == 1
    assert (await store.list_provider_attempts())[0]["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_timed_out_thread_cannot_launch_a_later_cloud_effect(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "revoked-background.db", clock=clock)
    await _completed(store)

    class BlockedListCollections(FakeCollections):
        def __init__(self):
            super().__init__()
            self.list_started = threading.Event()
            self.release_list = threading.Event()

        def list(self, *, limit: int, filter: str):  # noqa: A002
            self.list_started.set()
            if not self.release_list.wait(timeout=5):
                raise RuntimeError("test list was never released")
            return super().list(limit=limit, filter=filter)

    service = BlockedListCollections()
    in_flight_before = provider_utils.get_runtime_stats()["timed_threads_in_flight"]
    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=1,
        lease_seconds=5,
        call_timeout_seconds=1,
        backoff_base_seconds=1,
        backoff_max_seconds=1,
        lease_id_factory=lambda: "revoked-owner",
    ).run_once(store)
    assert service.list_started.is_set()
    assert result.status == "partial"
    assert result.retry_wait == 1

    # The list call was already authorized and cannot be killed. Once it
    # returns, the revoked token must stop the same thread before create or
    # upload_document can become a second cloud effect.
    service.release_list.set()
    for _ in range(100):
        if (
            provider_utils.get_runtime_stats()["timed_threads_in_flight"]
            <= in_flight_before
        ):
            break
        await asyncio.sleep(0.01)
    assert (
        provider_utils.get_runtime_stats()["timed_threads_in_flight"]
        <= in_flight_before
    )
    assert [name for name, _ in service.calls] == ["list"]
    assert (await store.list_provider_attempts())[0]["harvest_status"] == "retry_wait"
    await store.close()


@pytest.mark.asyncio
async def test_remote_episode_recovery_rejects_a_different_document_digest(tmp_path):
    store = GrokSessionStore(tmp_path / "remote-digest-mismatch.db")
    row = await _completed(store)
    service = FakeCollections()
    uploader = _uploader(service)
    client, reason = uploader.prepare_client()
    assert reason is None
    uploader.upload(client, row, _effect_authority())

    remote = service.documents[row["harvest_episode_id"]]
    remote.fields["document_digest"] = "sha256:" + ("0" * 64)
    with pytest.raises(ValueError, match="different document digest"):
        uploader.upload(client, row, _effect_authority())
    assert [name for name, _ in service.calls].count("upload_document") == 1
    await store.close()


@pytest.mark.asyncio
async def test_upload_failure_is_secret_safe_and_retries_with_bounded_backoff(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "xai-management-test-secret")
    store = GrokSessionStore(tmp_path / "retry.db")
    await _completed(store)
    service = FakeCollections(fail_mode="secret_before_write")
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    await store.close()
    store = GrokSessionStore(tmp_path / "retry.db", clock=clock)
    harvester = ProviderAttemptHarvester(
        uploader=_uploader(service),
        lease_id_factory=lambda: "lease-secret",
        backoff_base_seconds=7,
        backoff_max_seconds=20,
    )
    result = await harvester.run_once(store)
    assert result.status == "partial"
    assert result.retry_wait == 1
    row = (await store.list_provider_attempts())[0]
    assert row["harvest_status"] == "retry_wait"
    assert "xai-management-test-secret" not in row["harvest_error"]
    assert "REDACTED" in row["harvest_error"]
    assert datetime.fromisoformat(row["harvest_next_at"]) == clock.value + timedelta(seconds=7)

    service.fail_mode = None
    clock.advance(7)
    harvester._lease_id_factory = lambda: "lease-retry"
    result = await harvester.run_once(store)
    assert result.status == "complete"
    assert result.synced == 1
    assert (await store.list_provider_attempts())[0]["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_batch_is_bounded_and_incompatible_collection_never_uploads(tmp_path):
    store = GrokSessionStore(tmp_path / "batch.db")
    for index in range(1, 4):
        await _completed(store, index)
    service = FakeCollections()
    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=2,
        lease_id_factory=lambda: "lease-batch",
    ).run_once(store)
    assert (result.leased, result.synced) == (2, 2)
    rows = await store.list_provider_attempts()
    assert [row["harvest_status"] for row in rows] == ["synced", "synced", "pending"]
    await store.close()

    store = GrokSessionStore(tmp_path / "incompatible.db")
    await _completed(store)
    incompatible = FakeCollections(incompatible=True)
    result = await ProviderAttemptHarvester(
        uploader=_uploader(incompatible),
        lease_id_factory=lambda: "lease-incompatible",
    ).run_once(store)
    assert result.status == "partial"
    assert [name for name, _ in incompatible.calls].count("upload_document") == 0
    assert (await store.list_provider_attempts())[0]["harvest_status"] == "retry_wait"
    await store.close()


@pytest.mark.asyncio
async def test_corrupt_due_row_is_deferred_without_starving_healthy_row(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "corrupt-first.db", clock=clock)
    corrupt = await _completed(store, 1)
    healthy = await _completed(store, 2)
    await store._conn.execute(
        "UPDATE provider_attempts SET output_text = 'tampered' WHERE attempt_id = ?",
        (corrupt["attempt_id"],),
    )
    await store._conn.commit()

    class CountingStore:
        def __init__(self):
            self.lease_calls = 0

        async def lease_provider_attempts_for_harvest(self, *args):
            self.lease_calls += 1
            return await store.lease_provider_attempts_for_harvest(*args)

        async def provider_attempt_harvest_lease_is_fresh(self, *args):
            return await store.provider_attempt_harvest_lease_is_fresh(*args)

        async def mark_provider_attempt_harvest_synced(self, *args):
            return await store.mark_provider_attempt_harvest_synced(*args)

        async def mark_provider_attempt_harvest_retry(self, *args):
            return await store.mark_provider_attempt_harvest_retry(*args)

    wrapped = CountingStore()
    service = FakeCollections()
    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=2,
        lease_id_factory=lambda: "corrupt-scan-owner",
    ).run_once(wrapped)
    assert result.status == "complete"
    assert (result.leased, result.synced) == (1, 1)
    assert wrapped.lease_calls == 2
    upload = next(value for name, value in service.calls if name == "upload_document")
    assert upload[3]["episode_id"] == healthy["harvest_episode_id"]
    assert [name for name, _ in service.calls].count("upload_document") == 1

    async with store._conn.execute(
        "SELECT attempt_id, harvest_status, harvest_attempts, harvest_next_at, "
        "harvest_error FROM provider_attempts ORDER BY id"
    ) as cursor:
        rows = [dict(row) for row in await cursor.fetchall()]
    assert rows[0] == {
        "attempt_id": corrupt["attempt_id"],
        "harvest_status": "retry_wait",
        "harvest_attempts": 1,
        "harvest_next_at": (
            clock.value + timedelta(seconds=86_400)
        ).isoformat(),
        "harvest_error": "integrity:provider_attempt_decode_failed",
    }
    assert rows[1]["harvest_status"] == "synced"
    await store.close()


@pytest.mark.asyncio
async def test_schema_corruption_is_not_quarantined_as_a_row_failure(tmp_path):
    store = GrokSessionStore(tmp_path / "live-schema-corruption.db")
    row = await _completed(store)
    await store._conn.execute(
        "ALTER TABLE provider_attempts ADD COLUMN foreign_data TEXT;"
    )
    await store._conn.commit()

    with pytest.raises(RuntimeError, match="row schema"):
        await store.lease_provider_attempts_for_harvest("schema-owner", 30, 1)
    async with store._conn.execute(
        "SELECT harvest_status, harvest_attempts, harvest_error "
        "FROM provider_attempts WHERE attempt_id = ?",
        (row["attempt_id"],),
    ) as cursor:
        stored = dict(await cursor.fetchone())
    assert stored == {
        "harvest_status": "pending",
        "harvest_attempts": 0,
        "harvest_error": None,
    }
    await store.close()


def test_worker_collection_cannot_alias_verified_task_memory(monkeypatch):
    monkeypatch.setenv(
        "UNIGROK_WORKER_EPISODE_COLLECTION", "unigrok-task-memories-v2"
    )
    with pytest.raises(ValueError, match="cannot share"):
        XAIWorkerEpisodeUploader(
            client_factory=lambda: SimpleNamespace(collections=FakeCollections()),
            unavailable_reason=lambda: None,
        )


@pytest.mark.parametrize(
    ("reserved_env", "reserved_name"),
    [
        ("UNIGROK_TASK_RAG_COLLECTION", "shared-task-collection"),
        ("UNIGROK_COLLECTION_NAME", "shared-knowledge-collection"),
    ],
)
def test_worker_collection_cannot_alias_effective_reserved_names(
    monkeypatch, reserved_env, reserved_name
):
    monkeypatch.setenv(reserved_env, reserved_name)
    monkeypatch.setenv("UNIGROK_WORKER_EPISODE_COLLECTION", reserved_name)
    with pytest.raises(ValueError, match="cannot share"):
        XAIWorkerEpisodeUploader(
            client_factory=lambda: SimpleNamespace(collections=FakeCollections()),
            unavailable_reason=lambda: None,
        ).collection_name


def test_worker_collection_name_rejects_exact_configured_secret(monkeypatch):
    secret = "management-secret-safe-shape"
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", secret)
    monkeypatch.setenv("UNIGROK_WORKER_EPISODE_COLLECTION", secret)
    with pytest.raises(ValueError, match="secret-safe"):
        XAIWorkerEpisodeUploader(
            client_factory=lambda: SimpleNamespace(collections=FakeCollections()),
            unavailable_reason=lambda: None,
        ).collection_name


@pytest.mark.asyncio
async def test_collection_name_is_frozen_with_cached_identity(tmp_path, monkeypatch):
    store = GrokSessionStore(tmp_path / "collection-name-drift.db")
    row = await _completed(store)
    service = FakeCollections()
    monkeypatch.setenv("UNIGROK_WORKER_EPISODE_COLLECTION", "worker-episodes-a")
    uploader = _uploader(service)
    client, reason = uploader.prepare_client()
    assert reason is None

    first = uploader.upload(client, row, _effect_authority())
    monkeypatch.setenv("UNIGROK_WORKER_EPISODE_COLLECTION", "worker-episodes-b")
    second = uploader.upload(client, row, _effect_authority())

    assert first == second
    assert uploader.collection_name == "worker-episodes-a"
    create_names = [
        value[0] for name, value in service.calls if name == "create"
    ]
    assert create_names == ["worker-episodes-a"]
    assert "worker-episodes-b" not in repr(service.calls)
    assert [name for name, _ in service.calls].count("upload_document") == 1
    await store.close()


@pytest.mark.asyncio
async def test_cached_collection_is_rechecked_against_reserved_name_drift(
    tmp_path, monkeypatch
):
    store = GrokSessionStore(tmp_path / "collection-reserved-drift.db")
    row = await _completed(store)
    service = FakeCollections()
    monkeypatch.setenv("UNIGROK_WORKER_EPISODE_COLLECTION", "worker-episodes-safe")
    uploader = _uploader(service)
    client, reason = uploader.prepare_client()
    assert reason is None
    uploader.upload(client, row, _effect_authority())
    calls_before_drift = len(service.calls)

    monkeypatch.setenv("UNIGROK_TASK_RAG_COLLECTION", "worker-episodes-safe")
    with pytest.raises(ValueError, match="cannot share"):
        uploader.upload(client, row, _effect_authority())
    assert len(service.calls) == calls_before_drift
    await store.close()


@pytest.mark.asyncio
async def test_expired_lease_owner_cannot_commit_or_schedule_retry(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "expired-cas.db", clock=clock)
    row = await _completed(store)
    leased = await store.lease_provider_attempts_for_harvest("expired-owner", 5, 1)
    assert len(leased) == 1

    # The lease is invalid at the exact boundary, not one tick later.
    clock.advance(5)
    assert not await store.mark_provider_attempt_harvest_synced(
        row["attempt_id"], "expired-owner", "remote-expired"
    )
    assert not await store.mark_provider_attempt_harvest_retry(
        row["attempt_id"],
        "expired-owner",
        "late failure",
        10,
    )
    pending = (await store.list_provider_attempts())[0]
    assert pending["harvest_status"] == "leased"
    await store.close()


@pytest.mark.asyncio
async def test_trusted_time_is_sampled_after_lock_waits(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "lock-wait-clock.db", clock=clock)
    row = await _completed(store)

    # A lease request queued behind the store lock starts its authority from
    # the trusted time after the wait, not from a timestamp captured earlier.
    await store._lock.acquire()
    lease_task = asyncio.create_task(
        store.lease_provider_attempts_for_harvest("queued-owner", 5, 1)
    )
    await asyncio.sleep(0)
    clock.advance(100)
    store._lock.release()
    leased = await lease_task
    assert datetime.fromisoformat(leased[0]["harvest_lease_expires_at"]) == (
        clock.value + timedelta(seconds=5)
    )

    # The same rule applies to CAS completion: a worker that was current when
    # it queued but expired while waiting cannot commit with the earlier time.
    await store._lock.acquire()
    sync_task = asyncio.create_task(
        store.mark_provider_attempt_harvest_synced(
            row["attempt_id"], "queued-owner", "remote-after-wait"
        )
    )
    await asyncio.sleep(0)
    clock.advance(5)
    store._lock.release()
    assert not await sync_task

    reclaimed = await store.lease_provider_attempts_for_harvest(
        "queued-retry-owner", 5, 1
    )
    assert len(reclaimed) == 1
    await store._lock.acquire()
    retry_task = asyncio.create_task(
        store.mark_provider_attempt_harvest_retry(
            row["attempt_id"], "queued-retry-owner", "late failure", 1
        )
    )
    await asyncio.sleep(0)
    clock.advance(5)
    store._lock.release()
    assert not await retry_task
    await store.close()


@pytest.mark.asyncio
async def test_storage_enforces_lease_and_backoff_bounds(tmp_path):
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    store = GrokSessionStore(tmp_path / "bounded-time.db", clock=clock)
    row = await _completed(store)
    with pytest.raises(ValueError, match="lease exceeds"):
        await store.lease_provider_attempts_for_harvest("too-long", 301, 1)

    leased = await store.lease_provider_attempts_for_harvest("bounded-owner", 300, 1)
    assert len(leased) == 1
    with pytest.raises(ValueError, match="bounded backoff"):
        await store.mark_provider_attempt_harvest_retry(
            row["attempt_id"],
            "bounded-owner",
            "temporary",
            86_401,
        )
    await store.close()


@pytest.mark.asyncio
async def test_cloud_redaction_has_exact_cloud_digests(tmp_path, monkeypatch):
    store = GrokSessionStore(tmp_path / "cloud-digest.db")
    row = await _completed(store)
    frozen_before = worker_episode_document(row)
    monkeypatch.setenv("OPENAI_API_KEY", row["output_text"])

    payload = json.loads(worker_episode_document(row))
    assert payload["content"]["output"] == row["output_text"]
    assert payload["content"]["cloud_output_redaction"] == "clean"
    assert payload["digests"]["ledger"]["output"] == row["output_digest"]
    assert payload["digests"]["cloud"]["output"] == (
        "sha256:" + hashlib.sha256(row["output_text"].encode()).hexdigest()
    )
    assert worker_episode_document(row) == frozen_before
    uploader = _uploader(FakeCollections())
    client, reason = uploader.prepare_client()
    assert reason is None
    with pytest.raises(ValueError, match="current secret policy"):
        uploader.upload(client, row, _effect_authority())
    await store.close()


@pytest.mark.asyncio
async def test_frozen_document_tampering_fails_closed_on_read(tmp_path):
    store = GrokSessionStore(tmp_path / "frozen-tamper.db")
    row = await _completed(store)
    await store._conn.execute(
        "UPDATE provider_attempts SET harvest_document_digest = ? WHERE attempt_id = ?",
        ("sha256:" + ("0" * 64), row["attempt_id"]),
    )
    await store._conn.commit()

    with pytest.raises(ValueError, match="frozen cloud document digest mismatch"):
        await store.list_provider_attempts()
    await store.close()


@pytest.mark.asyncio
async def test_v16_upgrades_to_v17_and_backfills_exact_terminal_artifact(tmp_path):
    db_path = tmp_path / "v16-terminal-backfill.db"
    store = GrokSessionStore(db_path)
    before = await _completed(store)
    expected_document = worker_episode_document(before)
    expected_digest = before["harvest_document_digest"]
    expected_episode_id = before["harvest_episode_id"]
    await store.close()

    _downgrade_current_db_to_v16(db_path)
    store = GrokSessionStore(db_path)
    after = (await store.list_provider_attempts())[0]
    async with store._conn.execute("PRAGMA user_version;") as cursor:
        assert (await cursor.fetchone())[0] == 17
    assert worker_episode_document(after) == expected_document
    assert after["harvest_document_digest"] == expected_digest
    assert after["harvest_episode_id"] == expected_episode_id
    await store.close()


@pytest.mark.asyncio
async def test_v17_refuses_unknown_preexisting_frozen_columns(tmp_path):
    db_path = tmp_path / "foreign-v16-frozen.db"
    store = GrokSessionStore(db_path)
    await store._ensure_initialized()
    await store.close()
    _downgrade_current_db_to_v16(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "ALTER TABLE provider_attempts ADD COLUMN harvest_document_json TEXT;"
        )
        connection.commit()
    finally:
        connection.close()

    store = GrokSessionStore(db_path)
    with pytest.raises(RuntimeError, match="predate v17"):
        await store._ensure_initialized()
    await store.close()
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version;").fetchone()[0] == 16
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_state_transition_failure_does_not_abort_remaining_batch(tmp_path):
    store = GrokSessionStore(tmp_path / "state-error.db")
    await _completed(store, 1)
    await _completed(store, 2)
    service = FakeCollections()

    class OneBrokenTransition:
        def __init__(self):
            self.failed = False

        async def lease_provider_attempts_for_harvest(self, *args):
            return await store.lease_provider_attempts_for_harvest(*args)

        async def provider_attempt_harvest_lease_is_fresh(self, *args):
            return await store.provider_attempt_harvest_lease_is_fresh(*args)

        async def mark_provider_attempt_harvest_synced(self, *args):
            if not self.failed:
                self.failed = True
                raise RuntimeError("one local transition failed")
            return await store.mark_provider_attempt_harvest_synced(*args)

        async def mark_provider_attempt_harvest_retry(self, *args):
            return await store.mark_provider_attempt_harvest_retry(*args)

    result = await ProviderAttemptHarvester(
        uploader=_uploader(service),
        batch_size=2,
        lease_id_factory=lambda: "state-owner",
    ).run_once(OneBrokenTransition())
    assert result.status == "partial"
    assert result.state_errors == 1
    assert result.synced == 1
    assert [name for name, _ in service.calls].count("upload_document") == 2
    rows = await store.list_provider_attempts()
    assert rows[0]["harvest_status"] == "leased"
    assert rows[1]["harvest_status"] == "synced"
    await store.close()
