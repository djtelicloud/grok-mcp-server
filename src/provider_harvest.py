"""Durable xAI Collections mirror for subordinate provider episodes.

This module is intentionally one-way.  It uploads Grok-owned, redacted
transport evidence into a collection that is separate from verified task
memory.  It grants no routing, retrieval, verification, or final-answer
authority and has no public MCP surface.

This module is deliberately inert: it registers no scheduler or request-path
hook. A later Grok-owned worker broker must explicitly call
``ProviderAttemptHarvester.run_once`` to drain terminal provider attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any, Callable
import uuid

from . import utils as _utils

DEFAULT_WORKER_EPISODE_COLLECTION = "unigrok-worker-episodes-v2"
_TASK_MEMORY_COLLECTION_PREFIX = "unigrok-task-memories-"
_EPISODE_FIELD = "episode_id"
_DOCUMENT_DIGEST_FIELD = "document_digest"
_MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_SAFE_COLLECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validate_worker_episode_collection_name(name: str) -> str:
    """Reject unsafe, secret-bearing, or shared collection identities."""

    if _SAFE_COLLECTION_RE.fullmatch(name) is None:
        raise ValueError("worker episode collection name is invalid")
    safe_name, redaction = _utils._redact_provider_episode_text(name)
    if redaction != "clean" or safe_name != name:
        raise ValueError("worker episode collection name is not secret-safe")

    # Compare the effective configured names, not just the default prefix. A
    # user may rename either existing mirror, and creation order must not make
    # two logically separate stores alias the same physical collection.
    from .rag import task_rag_collection_name

    reserved = {
        task_rag_collection_name().strip().casefold(),
        _utils._knowledge_collection_name().strip().casefold(),
    }
    folded = name.casefold()
    if folded in reserved or folded.startswith(_TASK_MEMORY_COLLECTION_PREFIX):
        raise ValueError(
            "worker episodes cannot share task-memory or knowledge collections"
        )
    return name


def worker_episode_collection_name() -> str:
    """Return a safe collection name that cannot alias verified task memory."""

    name = (
        os.environ.get("UNIGROK_WORKER_EPISODE_COLLECTION", "").strip()
        or DEFAULT_WORKER_EPISODE_COLLECTION
    )
    return _validate_worker_episode_collection_name(name)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _episode_identity(row: dict[str, Any], document_digest: str | None = None) -> str:
    digest = str(document_digest or row.get("harvest_document_digest") or "")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("worker episode is missing its canonical document digest")
    return _utils._provider_episode_identity(
        str(row["attempt_id"]),
        str(row["start_digest"]),
        str(row["completion_digest"]),
        digest,
    )


def worker_episode_document_name(row: dict[str, Any]) -> str:
    """Content-addressed logical name reused by every retry."""

    document_digest = str(row.get("harvest_document_digest") or "")
    digest_hex = document_digest.removeprefix("sha256:")
    return f"worker-episode-{_episode_identity(row)}-{digest_hex}.json"


def _redact_cloud_text(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    text, redaction = _utils._redact_provider_episode_text(value)
    return text, redaction


def _cloud_json_value(value: Any) -> tuple[Any, str | None]:
    if value is None:
        return None, None
    raw = _canonical_json(value)
    safe, redaction = _utils._redact_provider_episode_text(raw)
    try:
        return json.loads(safe), redaction
    except (TypeError, ValueError) as exc:
        raise ValueError("worker episode receipt redaction produced invalid JSON") from exc


def _optional_content_digest(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else _canonical_json(value)
    return "sha256:" + hashlib.sha256(
        text.encode("utf-8", errors="strict")
    ).hexdigest()


def _build_worker_episode_document(row: dict[str, Any]) -> bytes:
    """Build final redacted bytes before their identity is assigned."""

    prompt, prompt_cloud_redaction = _redact_cloud_text(row.get("prompt_text"))
    output, output_cloud_redaction = _redact_cloud_text(row.get("output_text"))
    receipt, receipt_cloud_redaction = _cloud_json_value(row.get("receipt"))
    payload: dict[str, Any] = {
        "version": "unigrok-worker-episode/v2",
        "authority": {
            "role": "subordinate_worker_evidence",
            "supervisor": "grok",
            "semantic_outcome": "unverified",
            "may_route": False,
            "may_verify": False,
            "may_harvest": False,
            "may_finalize": False,
        },
        "grok_supervisor": {
            "session_id": row["supervisor_session_id"],
            "objective_id": row["objective_id"],
            "route_decision_id": row["route_decision_id"],
            "plane": row["supervisor_plane"],
            "model": row["supervisor_model"],
            "ttl_expires_at": row["ttl_expires_at"],
        },
        "worker": {
            "provider": row["provider"],
            "channel": row["channel"],
            "credential_plane": row["credential_plane"],
            "route": row["route"],
            "requested_model": row["requested_model"],
            "resolved_model": row.get("resolved_model"),
            "model_source": row.get("model_source"),
        },
        "transport": {
            "status": row["transport_status"],
            "finish_reason": row.get("finish_reason"),
            "duration_ms": row.get("duration_ms"),
            "error": (
                {
                    "kind": row.get("error_kind"),
                    "code": row.get("error_code"),
                }
                if row.get("error_kind") or row.get("error_code")
                else None
            ),
        },
        "receipt": receipt,
        "receipt_redaction": receipt_cloud_redaction,
        "content": {
            "prompt": prompt,
            "prompt_redaction": row["prompt_redaction"],
            "cloud_prompt_redaction": prompt_cloud_redaction,
            "output": output,
            "output_redaction": row.get("output_redaction"),
            "cloud_output_redaction": output_cloud_redaction,
        },
        "digests": {
            "ledger": {
                "start": row["start_digest"],
                "completion": row["completion_digest"],
                "prompt": row["prompt_digest"],
                "output": row.get("output_digest"),
                "receipt": row.get("receipt_digest"),
            },
            "cloud": {
                "prompt": _optional_content_digest(prompt),
                "output": _optional_content_digest(output),
                "receipt": _optional_content_digest(receipt),
            },
        },
        "usage": {
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "total_tokens": row.get("total_tokens"),
            "source": row["usage_source"],
        },
        "cost": {
            "usd": row.get("cost_usd"),
            "source": row["cost_source"],
        },
        "lifecycle": {
            "attempt_id": row["attempt_id"],
            "delegation_id": row["delegation_id"],
            "attempt_ordinal": row["attempt_ordinal"],
            "request_id": row["request_id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        },
    }
    body = _canonical_json(payload)
    safe_body, final_redaction = _utils._redact_provider_episode_text(body)
    try:
        final_payload = json.loads(safe_body)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker episode redaction produced invalid JSON") from exc
    if final_redaction != "clean":
        # Every content-bearing field was redacted before its cloud digest was
        # computed. A later mutation would make the artifact's digest claims
        # false, so fail closed instead of uploading an internally inconsistent
        # document.
        raise ValueError("worker episode contains an unbound secret-bearing field")
    body = _canonical_json(final_payload)
    encoded = body.encode("utf-8", errors="strict")
    if len(encoded) > _MAX_DOCUMENT_BYTES:
        raise ValueError("worker episode document exceeds the upload bound")
    return encoded


@dataclass(frozen=True)
class FrozenWorkerEpisode:
    document: bytes
    document_digest: str
    episode_id: str
    document_name: str


@dataclass(frozen=True)
class _ProviderHarvestEffectAuthority:
    """Revocable, bounded local authority checked at each cloud-call edge.

    A timed-out SDK thread cannot be stopped by asyncio. Revoking this token
    prevents that thread from launching a later cloud call after its owning
    lease has been released. A cloud call already in flight may still finish;
    its content-addressed remote identity is recovered by the next owner.
    """

    expires_monotonic: float
    _revoked: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def for_seconds(cls, seconds: float) -> "_ProviderHarvestEffectAuthority":
        duration = float(seconds)
        if not 0 < duration <= 300.0:
            raise ValueError("provider harvest effect authority is out of bounds")
        return cls(expires_monotonic=time.monotonic() + duration)

    @classmethod
    def until(cls, deadline_monotonic: float) -> "_ProviderHarvestEffectAuthority":
        deadline = float(deadline_monotonic)
        remaining = deadline - time.monotonic()
        if not math.isfinite(deadline) or not 0 < remaining <= 300.0:
            raise ValueError("provider harvest effect deadline is out of bounds")
        return cls(expires_monotonic=deadline)

    def revoke(self) -> None:
        self._revoked.set()

    def require_active(self) -> None:
        if self._revoked.is_set() or time.monotonic() >= self.expires_monotonic:
            raise PermissionError("provider harvest effect authority expired")


def freeze_worker_episode_document(row: dict[str, Any]) -> FrozenWorkerEpisode:
    """Create the one immutable cloud artifact for a terminal ledger row."""

    document = _build_worker_episode_document(row)
    document_digest = "sha256:" + hashlib.sha256(document).hexdigest()
    identity_row = dict(row)
    identity_row["harvest_document_digest"] = document_digest
    episode_id = _episode_identity(identity_row, document_digest)
    document_name = worker_episode_document_name(identity_row)
    return FrozenWorkerEpisode(
        document=document,
        document_digest=document_digest,
        episode_id=episode_id,
        document_name=document_name,
    )


def worker_episode_document(row: dict[str, Any]) -> bytes:
    """Return and verify the exact document frozen at terminal transition."""

    raw = row.get("harvest_document_json")
    digest = str(row.get("harvest_document_digest") or "")
    episode_id = str(row.get("harvest_episode_id") or "")
    if raw is None and not digest and not episode_id:
        # Used only while v17 backfills a v16 terminal row. Normal terminal
        # reads require all frozen fields through the store decoder.
        return freeze_worker_episode_document(row).document
    if not isinstance(raw, str) or not raw or not digest or not episode_id:
        raise ValueError("worker episode frozen artifact is incomplete")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker episode frozen document is malformed") from exc
    canonical = _canonical_json(parsed)
    if canonical != raw:
        raise ValueError("worker episode frozen document is not canonical")
    document = raw.encode("utf-8", errors="strict")
    if len(document) > _MAX_DOCUMENT_BYTES:
        raise ValueError("worker episode frozen document exceeds the upload bound")
    if "sha256:" + hashlib.sha256(document).hexdigest() != digest:
        raise ValueError("worker episode frozen document digest mismatch")
    if _episode_identity(row, digest) != episode_id:
        raise ValueError("worker episode frozen identity mismatch")
    return document


def _field_definition_is_compatible(definition: Any, key: str) -> bool:
    return (
        str(getattr(definition, "key", "") or "") == key
        and bool(getattr(definition, "required", False))
        and bool(getattr(definition, "unique", False))
        and not bool(getattr(definition, "inject_into_chunk", False))
    )


def _document_file_id(document: Any) -> str:
    metadata = getattr(document, "file_metadata", None)
    value = (
        getattr(metadata, "file_id", None)
        or getattr(document, "file_id", None)
        or getattr(document, "id", None)
        or ""
    )
    file_id = str(value).strip()
    safe, redaction = _utils._redact_provider_episode_text(file_id)
    if not file_id or len(file_id) > 256 or redaction != "clean" or safe != file_id:
        raise ValueError("xAI collection returned an invalid file identity")
    return file_id


def _document_name(document: Any) -> str:
    metadata = getattr(document, "file_metadata", None)
    return str(
        getattr(metadata, "name", None)
        or getattr(document, "name", None)
        or ""
    )


class XAIWorkerEpisodeUploader:
    """Synchronous, idempotent xAI Collections boundary.

    The xAI service chooses physical file IDs. UniGrok supplies a stable
    content-addressed name plus unique ``episode_id`` and ``document_digest``
    collection fields, and checks both before every upload. A timeout after a
    successful remote write is therefore recovered by lookup rather than a
    second logical row.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] = _utils.get_xai_management_client,
        unavailable_reason: Callable[[], str | None] | None = None,
        collection_name: str | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._unavailable_reason = unavailable_reason or self._default_unavailable_reason
        configured_name = str(collection_name or "").strip()
        self._collection_name = _validate_worker_episode_collection_name(
            configured_name or worker_episode_collection_name()
        )
        self._collection_id: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _default_unavailable_reason() -> str | None:
        if not _utils.xai_management_key_configured():
            return "management_key_missing"
        if not str(_utils.XAI_API_KEY or "").strip():
            return "inference_client_missing"
        return None

    @property
    def collection_name(self) -> str:
        # The logical name is frozen with this uploader's cached collection ID,
        # but current reserved-name and secret policy is re-applied before use.
        return _validate_worker_episode_collection_name(self._collection_name)

    def prepare_client(self) -> tuple[Any | None, str | None]:
        """Resolve local capability before any row is leased or cloud call occurs."""

        reason = self._unavailable_reason()
        if reason:
            return None, reason
        try:
            client = self._client_factory()
        except Exception:
            return None, "management_client_unavailable"
        service = getattr(client, "collections", None)
        if service is None or not all(
            callable(getattr(service, name, None))
            for name in ("list", "create", "list_documents", "upload_document")
        ):
            return None, "collections_client_unavailable"
        return client, None

    def _validate_collection(self, metadata: Any) -> str:
        collection_id = str(getattr(metadata, "collection_id", "") or "").strip()
        if not collection_id or len(collection_id) > 256:
            raise ValueError("xAI collection returned an invalid collection identity")
        if str(getattr(metadata, "collection_name", "") or "") != self.collection_name:
            raise ValueError("xAI collection identity does not match requested name")
        definitions = tuple(getattr(metadata, "field_definitions", ()) or ())
        if not all(
            any(_field_definition_is_compatible(item, key) for item in definitions)
            for key in (_EPISODE_FIELD, _DOCUMENT_DIGEST_FIELD)
        ):
            raise ValueError("worker episode collection lacks its unique identity fields")
        return collection_id

    def _resolve_collection_id(
        self, service: Any, authority: _ProviderHarvestEffectAuthority
    ) -> str:
        collection_name = self.collection_name
        if self._collection_id:
            return self._collection_id
        authority.require_active()
        listing = service.list(
            limit=100,
            filter=f'collection_name:"{collection_name}"',
        )
        matches = [
            item
            for item in (getattr(listing, "collections", None) or ())
            if str(getattr(item, "collection_name", "") or "") == collection_name
        ]
        if len(matches) > 1:
            raise ValueError("xAI returned ambiguous worker episode collections")
        if matches:
            self._collection_id = self._validate_collection(matches[0])
            return self._collection_id

        field_definitions = [
            {
                "key": _EPISODE_FIELD,
                "required": True,
                "inject_into_chunk": False,
                "unique": True,
                "description": "Deterministic UniGrok subordinate episode identity",
            },
            {
                "key": _DOCUMENT_DIGEST_FIELD,
                "required": True,
                "inject_into_chunk": False,
                "unique": True,
                "description": "SHA-256 of the exact frozen episode document bytes",
            },
        ]
        try:
            authority.require_active()
            created = service.create(
                name=collection_name,
                field_definitions=field_definitions,
                description=(
                    "Unverified subordinate worker evidence owned and supervised by Grok; "
                    "not verified task memory."
                ),
            )
        except Exception:
            # One bounded re-list handles a concurrent creator.  If no exact
            # compatible collection appears, the original failure remains a
            # retryable outbox condition.
            authority.require_active()
            relisted = service.list(
                limit=100,
                filter=f'collection_name:"{self.collection_name}"',
            )
            matches = [
                item
                for item in (getattr(relisted, "collections", None) or ())
                if str(getattr(item, "collection_name", "") or "")
                == collection_name
            ]
            if len(matches) != 1:
                raise
            created = matches[0]
        self._collection_id = self._validate_collection(created)
        return self._collection_id

    @staticmethod
    def _find_episode(
        service: Any,
        collection_id: str,
        episode_id: str,
        document_digest: str,
        document_name: str,
        authority: _ProviderHarvestEffectAuthority,
    ) -> str | None:
        authority.require_active()
        listing = service.list_documents(
            collection_id,
            limit=2,
            filter=f'fields.{_EPISODE_FIELD}:"{episode_id}"',
        )
        matches = []
        for document in getattr(listing, "documents", None) or ():
            fields = getattr(document, "fields", None) or {}
            if str(fields.get(_EPISODE_FIELD, "")) == episode_id:
                matches.append(document)
        if len(matches) > 1:
            raise ValueError("xAI returned duplicate unique worker episode identities")
        if not matches:
            return None
        if _document_name(matches[0]) != document_name:
            raise ValueError("xAI worker episode identity points to a different document")
        fields = getattr(matches[0], "fields", None) or {}
        if str(fields.get(_DOCUMENT_DIGEST_FIELD, "")) != document_digest:
            raise ValueError("xAI worker episode identity has a different document digest")
        return _document_file_id(matches[0])

    def upload(
        self,
        client: Any,
        row: dict[str, Any],
        authority: _ProviderHarvestEffectAuthority,
    ) -> str:
        """Upload or recover one logical episode and return its xAI file ID."""

        service = client.collections
        episode_id = str(row.get("harvest_episode_id") or "")
        document_digest = str(row.get("harvest_document_digest") or "")
        if _episode_identity(row, document_digest) != episode_id:
            raise ValueError("worker episode row identity is not bound to its document")
        document_name = worker_episode_document_name(row)
        document = worker_episode_document(row)
        safe_document, current_redaction = _utils._redact_provider_episode_text(
            document.decode("utf-8", errors="strict")
        )
        if current_redaction != "clean" or safe_document.encode("utf-8") != document:
            # Secret-policy changes never rewrite a frozen artifact under its
            # established remote identity. They defer it without disclosure.
            raise ValueError("frozen worker episode conflicts with current secret policy")
        authority.require_active()
        with self._lock:
            authority.require_active()
            collection_id = self._resolve_collection_id(service, authority)
            existing = self._find_episode(
                service,
                collection_id,
                episode_id,
                document_digest,
                document_name,
                authority,
            )
            if existing:
                return existing
            try:
                authority.require_active()
                uploaded = service.upload_document(
                    collection_id,
                    document_name,
                    document,
                    fields={
                        _EPISODE_FIELD: episode_id,
                        _DOCUMENT_DIGEST_FIELD: document_digest,
                    },
                )
            except Exception:
                recovered = self._find_episode(
                    service,
                    collection_id,
                    episode_id,
                    document_digest,
                    document_name,
                    authority,
                )
                if recovered:
                    return recovered
                raise
            fields = getattr(uploaded, "fields", None) or {}
            if str(fields.get(_EPISODE_FIELD, "")) != episode_id:
                raise ValueError("xAI upload response omitted the worker episode identity")
            if str(fields.get(_DOCUMENT_DIGEST_FIELD, "")) != document_digest:
                raise ValueError("xAI upload response changed the worker document digest")
            if _document_name(uploaded) != document_name:
                raise ValueError("xAI upload response changed the worker episode name")
            return _document_file_id(uploaded)


@dataclass(frozen=True)
class ProviderHarvestRun:
    status: str
    reason: str | None = None
    leased: int = 0
    synced: int = 0
    retry_wait: int = 0
    lease_lost: int = 0
    state_errors: int = 0


class ProviderAttemptHarvester:
    """One explicitly invoked bounded pass over the provider-attempt outbox.

    Constructing this worker performs no effect, and this module does not
    schedule it. The future Grok-owned broker is responsible for invocation.
    """

    def __init__(
        self,
        *,
        uploader: XAIWorkerEpisodeUploader | None = None,
        batch_size: int = 10,
        lease_seconds: float = 60.0,
        call_timeout_seconds: float = 10.0,
        backoff_base_seconds: float = 15.0,
        backoff_max_seconds: float = 900.0,
        lease_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.uploader = uploader or XAIWorkerEpisodeUploader()
        self.batch_size = max(1, min(int(batch_size), 25))
        self.lease_seconds = max(5.0, min(float(lease_seconds), 300.0))
        # One row is leased at a time below. Keep its bounded blocking wait
        # strictly inside that lease; an SDK thread may outlive cancellation,
        # but the expired owner can no longer commit and the unique episode ID
        # makes the eventual remote write recoverable.
        self.call_timeout_seconds = max(
            1.0, min(float(call_timeout_seconds), 120.0, self.lease_seconds - 1.0)
        )
        self.backoff_base_seconds = max(
            1.0, min(float(backoff_base_seconds), 300.0)
        )
        self.backoff_max_seconds = max(
            self.backoff_base_seconds,
            min(float(backoff_max_seconds), 86_400.0),
        )
        self._lease_id_factory = lease_id_factory or (
            lambda: f"xai-harvest-{uuid.uuid4().hex}"
        )

    def _retry_delay(self, attempts: int) -> float:
        exponent = max(0, min(int(attempts) - 1, 20))
        return min(
            self.backoff_base_seconds * (2**exponent),
            self.backoff_max_seconds,
        )

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        detail, _ = _utils._redact_provider_episode_text(str(exc))
        kind = re.sub(r"[^A-Za-z0-9_.:-]", "_", type(exc).__name__)[:80]
        return _utils._bounded_redacted(
            f"{kind}:{detail or 'provider_harvest_failed'}", 500
        )

    async def run_once(
        self,
        store: Any,
        *,
        deadline_monotonic: float | None = None,
    ) -> ProviderHarvestRun:
        """Run one bounded batch inside an optional caller-owned deadline.

        The caller deadline is absolute so cancellation cannot accidentally
        grant a background SDK thread a fresh per-row lease. Every row token
        is bounded by both that deadline and the local outbox lease, and is
        revoked in ``finally`` even when this coroutine is cancelled.
        """

        now_monotonic = time.monotonic()
        effective_deadline = (
            math.inf if deadline_monotonic is None else float(deadline_monotonic)
        )
        if deadline_monotonic is not None:
            if effective_deadline <= now_monotonic:
                raise TimeoutError("provider harvest deadline expired")
            if not math.isfinite(effective_deadline):
                raise ValueError("provider harvest deadline is out of bounds")

        client, reason = self.uploader.prepare_client()
        if client is None:
            return ProviderHarvestRun(status="unavailable", reason=reason)

        lease_id = self._lease_id_factory()
        leased = 0
        synced = 0
        retry_wait = 0
        lease_lost = 0
        state_errors = 0
        for _ in range(self.batch_size):
            remaining = effective_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("provider harvest deadline expired")
            authority = _ProviderHarvestEffectAuthority.until(
                min(
                    effective_deadline,
                    time.monotonic() + self.lease_seconds,
                )
            )
            try:
                rows = await store.lease_provider_attempts_for_harvest(
                    lease_id,
                    self.lease_seconds,
                    1,
                )
                if not rows:
                    break
                row = rows[0]
                leased += 1
                try:
                    authority.require_active()
                except PermissionError as exc:
                    raise TimeoutError("provider harvest deadline expired") from exc
                required_lease_remaining = min(
                    self.call_timeout_seconds + 0.5,
                    authority.expires_monotonic - time.monotonic(),
                )
                try:
                    fresh = await store.provider_attempt_harvest_lease_is_fresh(
                        row["attempt_id"],
                        lease_id,
                        max(0.0, required_lease_remaining),
                    )
                except Exception:
                    state_errors += 1
                    continue
                if not fresh:
                    lease_lost += 1
                    continue
                call_remaining = authority.expires_monotonic - time.monotonic()
                if call_remaining <= 0:
                    raise TimeoutError("provider harvest deadline expired")
                effective_call_timeout = min(
                    self.call_timeout_seconds,
                    call_remaining,
                )
                try:
                    authority.require_active()
                except PermissionError as exc:
                    raise TimeoutError("provider harvest deadline expired") from exc
                try:
                    remote_file_id = await _utils.run_blocking(
                        self.uploader.upload,
                        client,
                        row,
                        authority,
                        timeout=effective_call_timeout,
                    )
                except Exception as exc:
                    # Revoke before any local retry bookkeeping. A timed-out
                    # thread may still return from its current SDK call, but
                    # it cannot start a follow-up list/create/upload effect.
                    authority.revoke()
                    try:
                        marked = await store.mark_provider_attempt_harvest_retry(
                            row["attempt_id"],
                            lease_id,
                            self._safe_error(exc),
                            self._retry_delay(int(row["harvest_attempts"])),
                        )
                    except Exception:
                        # The row remains leased and becomes retryable on expiry.
                        # Do not let one state-transition failure suppress the rest
                        # of this bounded batch or expose its exception text.
                        state_errors += 1
                        continue
                    if marked:
                        retry_wait += 1
                    else:
                        lease_lost += 1
                    continue

                authority.revoke()
                try:
                    marked = await store.mark_provider_attempt_harvest_synced(
                        row["attempt_id"],
                        lease_id,
                        remote_file_id,
                    )
                except Exception:
                    # The deterministic remote episode can be recovered after the
                    # lease expires. Continue without claiming local completion.
                    state_errors += 1
                    continue
                if marked:
                    synced += 1
                else:
                    # Another process reclaimed the expired lease. It will find
                    # the same unique episode_id and recover this remote write.
                    lease_lost += 1
            finally:
                authority.revoke()
        if leased == 0:
            return ProviderHarvestRun(status="idle")
        return ProviderHarvestRun(
            status=(
                "complete"
                if retry_wait == 0 and lease_lost == 0 and state_errors == 0
                else "partial"
            ),
            leased=leased,
            synced=synced,
            retry_wait=retry_wait,
            lease_lost=lease_lost,
            state_errors=state_errors,
        )
