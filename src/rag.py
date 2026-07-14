# src/rag.py
"""Semantic task-memory RAG (UNIGROK_TASK_RAG).

Mirrors LOCAL task_memory rows (already redacted/bounded at rest) into an
xAI Collection and turns semantic matches into ADVISORY routing evidence.
SQLite stays the sole source of truth; every remote failure fails open to
local behavior. Rollout ladder via UNIGROK_TASK_RAG:

  off    (default) — zero Collections calls, routing byte-identical
  mirror — sync task memories to the collection, no retrieval
  shadow — retrieve + compute the semantic verdict, NEVER apply it
  active — apply the verdict on the borderline routing path only

Import discipline: this module imports FROM src.utils; src.utils reaches
back only through function-level late imports (no cycle).

Explicit non-goal: the knowledge collections adapter in src/utils.py
(sync_fact_to_collection / search_knowledge_collection) is NOT refactored
onto this module — unifying the two adapters is a possible follow-up.
TaskMemoryMirror keeps its entity-specific bits (collection name, document
naming/rendering) in overridable methods so future OKF entities can
subclass it; no second subclass ships today.
"""

import asyncio
import bisect
import contextlib
import dataclasses
import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, TextIO

from .utils import (
    _advisor_margin,
    _bounded_redacted,
    _collections_capable,
    _env_timeout,
    _task_hash,
    get_xai_management_client,
    run_blocking,
    xai_management_key_configured,
)

_LOGGER = "GrokMCP"

# ─── Configuration (UNIGROK_TASK_RAG_*) ──────────────────────────────────────

_VALID_MODES = ("off", "mirror", "shadow", "active")
_MODE_WARNED = False
_COLLECTION_WARNED = False
_DEFAULT_TASK_RAG_COLLECTION = "unigrok-task-memories-v2"
_LEGACY_TASK_RAG_COLLECTIONS = frozenset({"unigrok-task-memories-v1"})


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def task_rag_mode() -> str:
    """The rollout mode, defaulting to 'off'. An unknown value warns ONCE
    and reads as 'off' — this repo has no fail-fast startup validator, so a
    loud log plus /metrics + `rag status` visibility is the consistent
    choice over aborting a shared local server."""
    global _MODE_WARNED
    raw = os.environ.get("UNIGROK_TASK_RAG", "").strip().lower() or "off"
    if raw in _VALID_MODES:
        return raw
    if not _MODE_WARNED:
        _MODE_WARNED = True
        logging.getLogger(_LOGGER).warning(
            f"unknown UNIGROK_TASK_RAG={raw!r}; treating as off "
            f"(valid: {', '.join(_VALID_MODES)})"
        )
    return "off"


def task_rag_collection_name() -> str:
    # Versioned default so a future payload/schema change can roll to a new
    # collection without breaking find-or-create against the old one.
    global _COLLECTION_WARNED
    configured = os.environ.get("UNIGROK_TASK_RAG_COLLECTION", "").strip()
    if configured in _LEGACY_TASK_RAG_COLLECTIONS:
        if not _COLLECTION_WARNED:
            _COLLECTION_WARNED = True
            logging.getLogger(_LOGGER).warning(
                "legacy task-RAG collection %r is incompatible with verified-only "
                "evidence; using %r instead",
                configured,
                _DEFAULT_TASK_RAG_COLLECTION,
            )
        return _DEFAULT_TASK_RAG_COLLECTION
    return configured or _DEFAULT_TASK_RAG_COLLECTION


def has_management_key() -> bool:
    """xAI Collections is a MANAGEMENT API: the inference key alone cannot
    create/upload/search collections, and xAI exposes no public embedding
    models to inference keys (/v1/embedding-models returns []). Most users
    therefore run WITHOUT either supported management-key alias — the
    semantic routing evidence works fully locally (task_memory_fts bm25 +
    recency + per-model success); the cloud mirror is an optional boost gated
    on this check so keyless setups never spawn doomed sync work or remote
    searches."""
    return xai_management_key_configured()


def task_rag_timeout() -> float:
    return _env_timeout("UNIGROK_TASK_RAG_TIMEOUT", 2.0)


def task_rag_top_k() -> int:
    return _env_int("UNIGROK_TASK_RAG_TOP_K", 5, 1, 10)


def task_rag_margin() -> float:
    return _env_float("UNIGROK_TASK_RAG_MARGIN", _advisor_margin(), 0.0, 1.0)


def task_rag_min_evidence() -> int:
    return _env_int("UNIGROK_TASK_RAG_MIN_EVIDENCE", 3, 1, 100)


def task_rag_half_life_hours() -> float:
    return _env_float("UNIGROK_TASK_RAG_HALF_LIFE_HOURS", 168.0, 1.0, 8760.0)


def task_rag_fusion_weights() -> tuple:
    local = _env_float("UNIGROK_TASK_RAG_FUSION_LOCAL_WEIGHT", 0.65, 0.0, 1.0)
    remote = _env_float("UNIGROK_TASK_RAG_FUSION_REMOTE_WEIGHT", 0.35, 0.0, 1.0)
    return local, remote


# ─── Bounded stats + fused-score histogram ───────────────────────────────────

# Prometheus-style cumulative buckets; the last slot is +Inf.
FUSED_SCORE_BUCKETS = (0.2, 0.4, 0.6, 0.8, 1.0)

_STATS_LOCK = threading.Lock()


def _fresh_stats() -> Dict[str, Any]:
    return {
        "queries": 0,
        "cache_hits": 0,
        "remote_calls": 0,
        "remote_failures": 0,
        "rate_limited": 0,
        "timeouts": 0,
        "uploads": 0,
        "upload_failures": 0,
        "shadow_flips": 0,
        "applied_flips": 0,
        "fused_score_buckets": [0] * (len(FUSED_SCORE_BUCKETS) + 1),
        "fused_score_sum": 0.0,
        "fused_score_count": 0,
    }


_STATS = _fresh_stats()


def record_stat(name: str, inc: int = 1) -> None:
    with _STATS_LOCK:
        if name in _STATS and isinstance(_STATS[name], int):
            _STATS[name] += inc


def record_fused_score(score: float) -> None:
    value = max(0.0, float(score))
    with _STATS_LOCK:
        _STATS["fused_score_buckets"][bisect.bisect_left(FUSED_SCORE_BUCKETS, value)] += 1
        _STATS["fused_score_sum"] += value
        _STATS["fused_score_count"] += 1


def get_task_rag_stats() -> Dict[str, Any]:
    with _STATS_LOCK:
        snapshot = dict(_STATS)
        snapshot["fused_score_buckets"] = list(_STATS["fused_score_buckets"])
        return snapshot


# ─── TaskMemoryMirror: the xAI Collections boundary for task memory ─────────

class TaskMemoryMirror:
    """Best-effort cloud mirror for task_memory rows.

    Modeled on the knowledge collections adapter (find-or-create by name,
    role-separated xAI management client, run_blocking offload, warn-once) but with
    instance state instead of module globals, soft-disable with exponential
    backoff instead of unbounded retries, and a token bucket bounding
    remote searches under bursty borderline traffic. Never raises."""

    _SOFT_DISABLE_AFTER = 5
    _BACKOFF_BASE_SEC = 30.0
    _BACKOFF_MAX_SEC = 300.0
    _SEARCH_BUCKET_CAPACITY = 10.0
    _SEARCH_BUCKET_REFILL_PER_SEC = 10.0 / 60.0  # ~10 searches/minute sustained

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._collection_id: Optional[str] = None
        self._consecutive_failures = 0
        self._trips = 0
        self._disabled_until = 0.0
        self._warned = False
        self._last_error = ""
        self.last_known_ready: Optional[bool] = None
        self._bucket_tokens = self._SEARCH_BUCKET_CAPACITY
        self._bucket_refreshed = time.monotonic()

    # ── Entity-specific surface (overridable for future OKF entities) ──────
    def collection_name(self) -> str:
        return task_rag_collection_name()

    def document_name(self, row: Dict[str, Any]) -> str:
        return f"taskmem-{int(row['id'])}-{str(row.get('task_hash') or '')[:8]}.txt"

    def document_body(self, row: Dict[str, Any]) -> str:
        """One-line JSON header (ultra-reliable fallback identity parse)
        followed by a search-friendly prose summary. Every field is already
        redacted/bounded at rest by save_task_memory."""
        header = json.dumps(
            {"memory_id": int(row["id"]), "task_hash": str(row.get("task_hash") or "")},
            separators=(",", ":"),
        )
        raw_outcome = row.get("success")
        outcome = (
            "succeeded" if raw_outcome == 1
            else "failed" if raw_outcome == 0
            else "has an unverified outcome"
        )
        prose = (
            f"UniGrok task memory {int(row['id'])}: model "
            f"{row.get('model') or 'unknown'} on the {row.get('plane') or 'unknown'} "
            f"plane {outcome} at {row.get('created_at') or 'unknown time'}.\n"
            f"Task: {row.get('prompt_excerpt') or ''}\n"
            f"Outcome: {row.get('outcome_summary') or ''}"
        )
        return header + "\n" + prose

    # ── Availability / failure bookkeeping ─────────────────────────────────
    def _available(self) -> bool:
        return task_rag_mode() != "off" and time.monotonic() >= self._disabled_until

    def cooldown_remaining_sec(self) -> float:
        return max(0.0, self._disabled_until - time.monotonic())

    def _warn_once(self, exc: Exception) -> None:
        with self._lock:
            if self._warned:
                return
            self._warned = True
        logging.getLogger(_LOGGER).warning(
            f"Task-memory collection mirror unavailable (logged once): {exc}"
        )

    def _record_failure(self, exc: Exception) -> None:
        with self._lock:
            self._last_error = str(exc)
            self.last_known_ready = False
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._SOFT_DISABLE_AFTER:
                self._trips += 1
                cooldown = min(
                    self._BACKOFF_BASE_SEC * (2 ** (self._trips - 1)),
                    self._BACKOFF_MAX_SEC,
                )
                self._disabled_until = time.monotonic() + cooldown
                self._consecutive_failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._trips = 0
            self._last_error = ""
            self.last_known_ready = True

    def _take_search_token(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._bucket_refreshed)
            self._bucket_refreshed = now
            self._bucket_tokens = min(
                self._SEARCH_BUCKET_CAPACITY,
                self._bucket_tokens + elapsed * self._SEARCH_BUCKET_REFILL_PER_SEC,
            )
            if self._bucket_tokens < 1.0:
                return False
            self._bucket_tokens -= 1.0
            return True

    def _resolve_collection_id(self, client: Any) -> Optional[str]:
        """Find-or-create the named collection; id cached per instance.
        Runs on an executor thread (SDK calls are sync)."""
        with self._lock:
            if self._collection_id:
                return self._collection_id
            name = self.collection_name()
            listing = client.collections.list(limit=100)
            for meta in getattr(listing, "collections", None) or []:
                if str(getattr(meta, "collection_name", "") or "") == name:
                    self._collection_id = str(meta.collection_id)
                    return self._collection_id
            created = client.collections.create(name=name)
            collection_id = str(getattr(created, "collection_id", "") or "")
            self._collection_id = collection_id or None
            return self._collection_id

    # ── Public async surface (never raises) ────────────────────────────────
    async def ready(self) -> bool:
        """Real readiness probe: mode enabled + capable SDK + resolvable
        collection. Updates last_known_ready (which /metrics reads without
        any network call)."""
        if task_rag_mode() == "off":
            self.last_known_ready = False
            return False
        if not has_management_key():
            # Not an error: the mirror is an optional boost. Report the
            # reason without burning a probe or tripping failure backoff.
            self.last_known_ready = False
            self._last_error = "xAI management key not set (cloud mirror is optional)"
            return False

        def _probe():
            client = get_xai_management_client()
            if not _collections_capable(client):
                raise RuntimeError("installed xai_sdk lacks the collections service surface")
            if not self._resolve_collection_id(client):
                raise RuntimeError("task-memory collection could not be resolved")
            return True

        try:
            await run_blocking(_probe, timeout=task_rag_timeout())
            self._record_success()
            return True
        except Exception as exc:
            self._warn_once(exc)
            self._record_failure(exc)
            return False

    async def upload_memory(self, row: Dict[str, Any]) -> Optional[str]:
        """Upload ONE task-memory row; returns the remote file id (the
        deterministic document name when the SDK returns no id)."""
        if not self._available():
            return None

        def _upload():
            client = get_xai_management_client()
            if not _collections_capable(client):
                raise RuntimeError("installed xai_sdk lacks the collections service surface")
            collection_id = self._resolve_collection_id(client)
            if not collection_id:
                raise RuntimeError("task-memory collection could not be resolved")
            doc_name = self.document_name(row)
            response = client.collections.upload_document(
                collection_id, doc_name, self.document_body(row).encode("utf-8")
            )
            file_id = str(
                getattr(response, "file_id", "") or getattr(response, "id", "") or ""
            )
            return file_id or doc_name

        try:
            file_id = await run_blocking(_upload, timeout=task_rag_timeout())
            record_stat("uploads")
            self._record_success()
            return file_id
        except Exception as exc:
            record_stat("upload_failures")
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                record_stat("timeouts")
            self._warn_once(exc)
            self._record_failure(exc)
            return None

    async def search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """AT MOST one bounded semantic search; [] on any failure, cooldown,
        or empty token bucket (fail open)."""
        if not self._available():
            return []
        if not self._take_search_token():
            record_stat("rate_limited")
            return []

        def _search():
            client = get_xai_management_client()
            if not _collections_capable(client):
                raise RuntimeError("installed xai_sdk lacks the collections service surface")
            collection_id = self._resolve_collection_id(client)
            if not collection_id:
                return []
            response = client.collections.search(
                _bounded_redacted(str(query or ""), 500),
                [collection_id],
                limit=max(1, min(int(limit or 5), 10)),
            )
            results = []
            for match in getattr(response, "matches", None) or []:
                content = str(getattr(match, "chunk_content", "") or "").strip()
                if not content:
                    continue
                results.append({
                    "content": content[:1000],
                    "score": float(getattr(match, "score", 0.0) or 0.0),
                    "file_id": str(getattr(match, "file_id", "") or ""),
                })
            return results

        record_stat("remote_calls")
        try:
            results = await run_blocking(_search, timeout=task_rag_timeout())
            self._record_success()
            return results
        except Exception as exc:
            record_stat("remote_failures")
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                record_stat("timeouts")
            self._warn_once(exc)
            self._record_failure(exc)
            return []

    async def sync_pending(
        self,
        store: Any,
        limit: int = 8,
        max_attempts: Optional[int] = 5,
    ) -> Dict[str, int]:
        """Drain the outbox oldest-first: upload each unsynced row and mark
        it synced/failed in place. Sequential (one row at a time) so the
        mirror occupies at most one run_blocking timed thread; stops early
        when the mirror soft-disables."""
        summary = {"synced": 0, "failed": 0}
        try:
            rows = await store.list_unsynced_task_memories(
                limit=limit, max_attempts=max_attempts, verified_only=True
            )
        except Exception as exc:
            logging.getLogger(_LOGGER).warning(f"Task-RAG outbox read failed: {exc}")
            return summary
        for row in rows:
            if not self._available():
                break
            file_id = await self.upload_memory(row)
            try:
                if file_id:
                    await store.mark_task_memory_synced(row["id"], file_id)
                    summary["synced"] += 1
                else:
                    await store.mark_task_memory_sync_failed(
                        row["id"], self._last_error or "upload failed"
                    )
                    summary["failed"] += 1
            except Exception as exc:
                logging.getLogger(_LOGGER).warning(f"Task-RAG outbox mark failed: {exc}")
                summary["failed"] += 1
        return summary


_MIRROR = TaskMemoryMirror()


def get_task_memory_mirror() -> TaskMemoryMirror:
    return _MIRROR


# ─── Fusion + decision signal (pure functions) ───────────────────────────────

@dataclass(frozen=True)
class SemanticVerdict:
    """Outcome of the semantic evidence pass. prefers_planning=None means
    undecidable — the advisor falls through to telemetry/static."""

    prefers_planning: Optional[bool]
    planning_signal: float
    coding_signal: float
    evidence_count: int
    confidence: float = 0.0
    remote_count: int = 0
    local_count: int = 0


def _recency_weight(created_at: Any, half_life_hours: float, now: datetime) -> float:
    """2^(-age_hours/half_life). Naive-vs-naive local time (consistent with
    how the store writes created_at); unparsable/absent or negative age
    degrades to 1.0/clamped — this is an advisory layer, never penalize."""
    try:
        stamp = datetime.fromisoformat(str(created_at))
    except (TypeError, ValueError):
        return 1.0
    age_hours = max(0.0, (now - stamp).total_seconds() / 3600.0)
    return 2.0 ** (-age_hours / max(half_life_hours, 1.0))


def fuse_task_evidence(
    local_rows: List[Dict[str, Any]],
    remote_rows: List[Dict[str, Any]],
    *,
    top_k: int,
    half_life_hours: float,
    local_weight: float,
    remote_weight: float,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Fuse local FTS candidates with remote semantic hits into one ranked
    list, deduped by memory id (each memory contributes exactly once).

    local_rows: get_similar_task_memories output (score = 0..1 band plus
    bonuses; batch-normalized here so bonused rows correctly dominate).
    remote_rows: LOCAL rows mapped from collection hits, each carrying the
    raw remote score under 'remote_score' (batch-normalized here).
    fused = local_weight*norm_local + remote_weight*norm_remote*recency."""
    now = now or datetime.now()
    max_local = max((float(r.get("score") or 0.0) for r in local_rows), default=0.0)
    max_remote = max(
        (max(0.0, float(r.get("remote_score") or 0.0)) for r in remote_rows),
        default=0.0,
    )

    fused: Dict[int, Dict[str, Any]] = {}
    for row in local_rows:
        entry = dict(row)
        entry["_norm_local"] = (
            float(row.get("score") or 0.0) / max_local if max_local > 0 else 0.0
        )
        entry["_norm_remote"] = 0.0
        fused[int(row["id"])] = entry
    for row in remote_rows:
        norm_remote = (
            max(0.0, float(row.get("remote_score") or 0.0)) / max_remote
            if max_remote > 0
            else 0.0
        )
        rid = int(row["id"])
        if rid in fused:
            fused[rid]["_norm_remote"] = max(fused[rid]["_norm_remote"], norm_remote)
        else:
            entry = dict(row)
            entry["_norm_local"] = 0.0
            entry["_norm_remote"] = norm_remote
            fused[rid] = entry

    ranked = []
    for entry in fused.values():
        recency = _recency_weight(entry.get("created_at"), half_life_hours, now)
        entry["fused_score"] = (
            local_weight * entry.pop("_norm_local")
            + remote_weight * entry.pop("_norm_remote") * recency
        )
        ranked.append(entry)
    ranked.sort(key=lambda e: (float(e["fused_score"]), int(e["id"])), reverse=True)
    return ranked[: max(1, int(top_k))]


def semantic_route_signal(
    fused: List[Dict[str, Any]],
    planning_model: str,
    coding_model: str,
    *,
    margin: float,
    min_evidence: int,
) -> SemanticVerdict:
    """Fused-score-weighted success comparison of memories that ran on the
    planning vs the coding model. Decidable only with >= min_evidence
    matched memories AND both sides represented; a decidable verdict flips
    to planning iff (planning_signal - coding_signal) >= margin."""
    p_weight = p_success = 0.0
    c_weight = c_success = 0.0
    matched = 0
    for row in fused:
        weight = float(row.get("fused_score") or 0.0)
        if weight <= 0:
            continue
        if row.get("success") not in (0, 1):
            # A provider stop is not success evidence. Unknown outcomes stay
            # retrievable as context but never calibrate semantic routing.
            continue
        model = str(row.get("model") or "")
        success = 1.0 if row.get("success") else 0.0
        if model == planning_model:
            p_weight += weight
            p_success += weight * success
            matched += 1
        elif model == coding_model:
            c_weight += weight
            c_success += weight * success
            matched += 1

    planning_signal = p_success / p_weight if p_weight > 0 else 0.0
    coding_signal = c_success / c_weight if c_weight > 0 else 0.0

    if matched < max(1, int(min_evidence)) or p_weight <= 0 or c_weight <= 0:
        return SemanticVerdict(
            prefers_planning=None,
            planning_signal=planning_signal,
            coding_signal=coding_signal,
            evidence_count=matched,
        )

    diff = planning_signal - coding_signal
    confidence = abs(diff) * min(1.0, matched / (2.0 * max(1, int(min_evidence))))
    return SemanticVerdict(
        prefers_planning=diff >= margin,
        planning_signal=planning_signal,
        coding_signal=coding_signal,
        evidence_count=matched,
        confidence=confidence,
    )


# ─── Evidence gathering (30s cache, at most ONE remote search) ───────────────

_EVIDENCE_TTL_SEC = 30.0
_EVIDENCE_CACHE_MAX = 128
_EVIDENCE_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _parse_header_memory_id(content: str) -> Optional[int]:
    """Recover the memory id from the JSON header line of a chunk whose
    file_id could not be mapped locally (e.g. chunked mid-document)."""
    try:
        header = json.loads(str(content or "").splitlines()[0])
        return int(header["memory_id"])
    except (ValueError, TypeError, KeyError, IndexError):
        return None


async def _map_remote_hits(
    store: Any,
    hits: List[Dict[str, Any]],
    local_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map collection hits back to LOCAL rows: primarily via the
    remote_file_id column, falling back to the JSON header against the
    already-fetched local candidates. Unmappable hits are DROPPED — raw
    remote chunks never become evidence (or prompt content)."""
    file_ids = [str(h.get("file_id") or "") for h in hits if h.get("file_id")]
    by_file_id: Dict[str, Dict[str, Any]] = {}
    if file_ids:
        rows = await store.get_task_memories_by_remote_ids(file_ids)
        by_file_id = {str(r.get("remote_file_id") or ""): r for r in rows}
    local_by_id = {int(r["id"]): r for r in local_rows}

    mapped = []
    for hit in hits:
        row = by_file_id.get(str(hit.get("file_id") or ""))
        if row is None:
            memory_id = _parse_header_memory_id(hit.get("content") or "")
            row = local_by_id.get(memory_id) if memory_id is not None else None
        if row is None:
            continue
        entry = dict(row)
        entry["remote_score"] = float(hit.get("score") or 0.0)
        mapped.append(entry)
    return mapped


async def gather_semantic_evidence(
    store: Any,
    prompt: str,
    context_id: Optional[str],
    planning_model: str,
    coding_model: str,
) -> Optional[SemanticVerdict]:
    """Local candidates + at most ONE remote search, fused into a
    SemanticVerdict. 30s TTL cache keyed by task hash + context (verdicts
    AND misses are cached). Every failure path returns None — fail open."""
    record_stat("queries")
    key = f"{_task_hash(prompt)}:{context_id or ''}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _EVIDENCE_CACHE.get(key)
        if cached is not None and now - cached[0] < _EVIDENCE_TTL_SEC:
            record_stat("cache_hits")
            return cached[1]

    verdict: Optional[SemanticVerdict] = None
    try:
        local = await store.get_similar_task_memories(
            prompt, context_id=context_id, limit=10, verified_only=True
        )
        local = [row for row in local if row.get("success") in (0, 1)]
        # Keyless setups (no xAI management key — the common case) run
        # pure local evidence: fusion and the decision signal work the same
        # with an empty remote component.
        remote_hits: List[Dict[str, Any]] = []
        if has_management_key():
            remote_hits = await get_task_memory_mirror().search(prompt, task_rag_top_k())
        mapped = await _map_remote_hits(store, remote_hits, local)
        mapped = [row for row in mapped if row.get("success") in (0, 1)]
        local_weight, remote_weight = task_rag_fusion_weights()
        fused = fuse_task_evidence(
            local,
            mapped,
            top_k=task_rag_top_k(),
            half_life_hours=task_rag_half_life_hours(),
            local_weight=local_weight,
            remote_weight=remote_weight,
        )
        if fused:
            record_fused_score(float(fused[0].get("fused_score") or 0.0))
        verdict = semantic_route_signal(
            fused,
            planning_model,
            coding_model,
            margin=task_rag_margin(),
            min_evidence=task_rag_min_evidence(),
        )
        verdict = dataclasses.replace(
            verdict, remote_count=len(mapped), local_count=len(local)
        )
    except Exception as exc:
        logging.getLogger(_LOGGER).warning(
            f"Task-RAG evidence gathering failed (fail-open): {exc}"
        )
        verdict = None

    with _CACHE_LOCK:
        _EVIDENCE_CACHE[key] = (now, verdict)
        _EVIDENCE_CACHE.move_to_end(key)
        while len(_EVIDENCE_CACHE) > _EVIDENCE_CACHE_MAX:
            _EVIDENCE_CACHE.popitem(last=False)
    return verdict


# ─── Fire-and-forget sync trigger (wired into _save_task_memory_safe) ────────

# Strong refs so fire-and-forget drains aren't garbage-collected mid-flight;
# the done callback discards them.
_BG_TASKS: set = set()
# Single-flight guard: bursty saves must not pile up run_blocking timed
# threads (shared UNIGROK_MAX_TIMED_THREADS cap) — one drain at a time, and
# a drain covers every pending row anyway.
_SYNC_INFLIGHT = threading.Event()


def spawn_sync_task(store: Any) -> Optional["asyncio.Task"]:
    """Best-effort background outbox drain; returns the task or None when
    skipped (mode off, drain already in flight, or no running loop). Never
    raises and never blocks the caller."""
    if task_rag_mode() == "off":
        return None
    if not has_management_key():
        # Keyless (the common case): the cloud mirror can never accept the
        # upload, so don't queue work that is doomed to fail — local
        # retrieval and routing evidence are unaffected.
        return None
    if _SYNC_INFLIGHT.is_set():
        return None
    _SYNC_INFLIGHT.set()
    try:
        task = asyncio.create_task(
            get_task_memory_mirror().sync_pending(store, limit=4, max_attempts=5)
        )
    except RuntimeError:
        _SYNC_INFLIGHT.clear()
        return None
    _BG_TASKS.add(task)

    def _done(finished: "asyncio.Task") -> None:
        _BG_TASKS.discard(finished)
        _SYNC_INFLIGHT.clear()
        if not finished.cancelled():
            with contextlib.suppress(Exception):
                finished.result()  # surface nothing; sync_pending never raises

    task.add_done_callback(_done)
    return task


# ─── `rag` CLI (dispatched from src/cli.py, no argparse by convention) ───────

_RAG_USAGE = """usage: unigrok-mcp rag <subcommand>

subcommands:
  status                     mode, collection, readiness, outbox depth, stats
  backfill [--dry-run] [--limit N] [--retry-failed] [--force-reupload]
                             drain the sync outbox oldest-first (resumable)
"""


async def _rag_status(store: Any, out: TextIO) -> int:
    mode = task_rag_mode()
    mirror = get_task_memory_mirror()
    print(f"mode: {mode}", file=out)
    try:
        memories = await store.get_task_memory_count()
        print(
            f"local evidence: {memories} task memories "
            "(semantic routing evidence works locally — no cloud needed)",
            file=out,
        )
    except Exception as exc:
        print(f"local evidence: unavailable ({exc})", file=out)
    print(f"collection: {task_rag_collection_name()}", file=out)
    if mode == "off":
        print("cloud mirror: off (UNIGROK_TASK_RAG=off)", file=out)
        print("ready: no (UNIGROK_TASK_RAG=off)", file=out)
    elif not has_management_key():
        print(
            "cloud mirror: disabled — optional; set XAI_MANAGEMENT_API_KEY "
            "(or SDK alias XAI_MANAGEMENT_KEY) to sync/search collections",
            file=out,
        )
        print(
            "ready: no (xAI management key not set; cloud mirror is optional)",
            file=out,
        )
    elif await mirror.ready():
        print("cloud mirror: enabled", file=out)
        print("ready: yes", file=out)
    else:
        reason = mirror._last_error or "unavailable"
        print("cloud mirror: enabled but unreachable", file=out)
        print(f"ready: no ({reason})", file=out)
    try:
        unsynced = await store.count_unsynced_task_memories(verified_only=True)
        print(f"unsynced: {unsynced}", file=out)
    except Exception as exc:
        print(f"unsynced: unavailable ({exc})", file=out)
    stats = get_task_rag_stats()
    for key in (
        "queries", "cache_hits", "remote_calls", "remote_failures",
        "rate_limited", "timeouts", "uploads", "upload_failures",
        "shadow_flips", "applied_flips",
    ):
        print(f"{key}: {stats[key]}", file=out)
    return 0


async def _rag_backfill(
    store: Any,
    out: TextIO,
    dry_run: bool,
    limit: Optional[int],
    retry_failed: bool,
    force_reupload: bool,
) -> int:
    mode = task_rag_mode()
    if mode == "off":
        print("UNIGROK_TASK_RAG=off — nothing to backfill.", file=out)
        return 1
    if not has_management_key() and not dry_run:
        # --dry-run stays available keyless (purely local outbox inspection).
        print(
            "The cloud mirror needs XAI_MANAGEMENT_API_KEY (or SDK alias "
            "XAI_MANAGEMENT_KEY), separate from the inference key. It is "
            "OPTIONAL: local semantic routing evidence already works without it.",
            file=out,
        )
        return 1
    mirror = get_task_memory_mirror()

    if force_reupload and not dry_run:
        requeued = await store.reset_task_memory_sync()
        print(f"force-reupload: re-queued {requeued} rows", file=out)

    max_attempts = None if retry_failed else 5
    total = len(
        await store.list_unsynced_task_memories(
            limit=200, max_attempts=max_attempts, verified_only=True
        )
    )
    if dry_run:
        preview = await store.list_unsynced_task_memories(
            limit=5, max_attempts=max_attempts, verified_only=True
        )
        print(f"dry-run: {total} row(s) pending (window of 200)", file=out)
        for row in preview:
            print(f"  would upload {mirror.document_name(row)}", file=out)
        return 0

    if not await mirror.ready():
        print(f"mirror unavailable: {mirror._last_error or 'not ready'}", file=out)
        return 1

    synced = failed = processed = 0
    while True:
        batch_limit = 25 if limit is None else min(25, limit - processed)
        if batch_limit <= 0:
            break
        summary = await mirror.sync_pending(
            store, limit=batch_limit, max_attempts=max_attempts
        )
        batch_total = summary["synced"] + summary["failed"]
        if batch_total == 0:
            break
        synced += summary["synced"]
        failed += summary["failed"]
        processed += batch_total
        print(f"synced {synced}/{max(total, processed)} (failed {failed})", file=out)
        if summary["synced"] == 0:
            # A whole batch failed: stop instead of hammering the same rows
            # (with --retry-failed they would never leave the window).
            break
    remaining = await store.count_unsynced_task_memories(verified_only=True)
    print(f"done: {synced} synced, {failed} failed, {remaining} remaining", file=out)
    return 0


def rag_cli(args: List[str], stream: Optional[TextIO] = None, store: Any = None) -> int:
    """Hand-rolled `rag` subcommand dispatcher (matching src/cli.py's init
    pattern — no argparse). Runs against the shared store singleton unless
    a store is injected (tests)."""
    out = stream or sys.stdout
    args = list(args or [])
    command = args[0] if args else ""
    if command not in ("status", "backfill"):
        print(_RAG_USAGE, file=out)
        return 2

    if store is None:
        from .utils import store as shared_store

        store = shared_store

    async def _run(coro):
        # Close the store before the loop ends: aiosqlite's connection
        # thread is non-daemon, so an open store keeps the CLI process
        # alive after the output is printed. close() is idempotent and
        # reopenable (the src/storage.py contract), so injected stores
        # are safe too.
        try:
            return await coro
        finally:
            with contextlib.suppress(Exception):
                await store.close()

    if command == "status":
        return asyncio.run(_run(_rag_status(store, out)))

    dry_run = "--dry-run" in args
    retry_failed = "--retry-failed" in args
    force_reupload = "--force-reupload" in args
    limit: Optional[int] = None
    if "--limit" in args:
        try:
            limit = max(1, int(args[args.index("--limit") + 1]))
        except (IndexError, ValueError):
            print("--limit needs a positive integer", file=out)
            return 2
    return asyncio.run(
        _run(_rag_backfill(store, out, dry_run, limit, retry_failed, force_reupload))
    )


# ─── Test hook ───────────────────────────────────────────────────────────────

def reset_task_rag_state() -> None:
    """Fresh mirror/stats/caches/warn-flags — mirrors the knowledge tests'
    reset_collections_state fixture so process globals never leak between
    tests."""
    global _MIRROR, _MODE_WARNED, _STATS
    _MIRROR = TaskMemoryMirror()
    _MODE_WARNED = False
    _SYNC_INFLIGHT.clear()
    with _STATS_LOCK:
        _STATS = _fresh_stats()
    with _CACHE_LOCK:
        _EVIDENCE_CACHE.clear()
