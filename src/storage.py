# src/storage.py
"""Storage backend seam for UniGrok.

SessionStoreProtocol is the structural contract every persistence backend
must satisfy: it enumerates the public async surface of GrokSessionStore
(src/utils.py), which is the reference — and currently only — implementation
(WAL SQLite, versioned migrations, write lock + read-connection pool).
get_store() is the factory the `store` singleton in src/utils.py is created
through; UNIGROK_STORAGE_BACKEND selects the backend ('sqlite' is the
default and the only implemented value).

Adding a backend (e.g. Postgres) means:
  1. Implementing every method of SessionStoreProtocol with the same
     semantics — notably: all methods must be safe to call before any
     explicit initialization (GrokSessionStore lazily initializes on first
     use), close() must be idempotent and reopenable (the next call
     re-initializes), save_telemetry/create_job read the ambient
     caller/request-id contextvars when their optional params are None, and
     persisted free-text (prompts, results, facts) must go through the same
     redaction/bounding helpers the SQLite backend uses.
  2. Registering it in get_store() under a new UNIGROK_STORAGE_BACKEND value.
  3. Extending tests/test_observability.py's conformance test to instantiate
     the new backend against SessionStoreProtocol.

The protocol is deliberately storage-agnostic: no db_path, connections,
locks, or SQLite pragmas — those are implementation details of the SQLite
backend. There is intentionally NO untested Postgres driver here; unknown
backend names fail fast with NotImplementedError.
"""

import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

SUPPORTED_STORAGE_BACKENDS = ("sqlite",)


@runtime_checkable
class SessionStoreProtocol(Protocol):
    """Public async surface of a UniGrok session/telemetry store.

    Structural (duck-typed) protocol: GrokSessionStore satisfies it without
    inheriting from it, and tests assert conformance via isinstance (the
    @runtime_checkable check verifies member presence, not signatures — the
    signatures below are the documented contract).
    """

    # ── Lifecycle ────────────────────────────────────────────────────────────
    async def close(self) -> None: ...
    async def vacuum_db(self) -> None: ...

    # ── Telemetry ────────────────────────────────────────────────────────────
    async def save_telemetry(
        self,
        intent: str,
        chosen_plane: str,
        success: Optional[int],
        latency: float,
        cost: float,
        context_id: Optional[str] = None,
        caller: Optional[str] = None,
        request_id: Optional[str] = None,
        model: Optional[str] = None,
        tokens: Optional[int] = None,
        token_kind: Optional[str] = None,
        billing_source: Optional[str] = None,
        routing: Optional[Dict[str, Any]] = None,
        folded: Optional[bool] = None,
    ) -> None: ...
    async def get_telemetry_stats(self) -> List[Dict[str, Any]]: ...
    async def attach_semantic_scores(
        self, request_id: str, semantic: Dict[str, Any], *, scan_limit: int = 200
    ) -> bool: ...
    async def get_semantic_judge_cost_today(self) -> float: ...
    async def get_caller_cost_today(self, caller_principal: str) -> float: ...
    async def get_caller_stats_today(self, limit: int = 10) -> List[Dict[str, Any]]: ...
    async def get_recent_model_stats(self, limit: int = 200) -> List[Dict[str, Any]]: ...

    # ── Routing calibration (evals → router closed loop) ────────────────────
    async def upsert_routing_calibration(
        self,
        category: str,
        route: str,
        model: str,
        success_rate: float,
        avg_cost_usd: float,
        n: int,
    ) -> None: ...
    async def get_routing_calibration(
        self, max_age_hours: Optional[float] = None
    ) -> List[Dict[str, Any]]: ...

    # ── Task memory ──────────────────────────────────────────────────────────
    async def save_task_memory(
        self,
        prompt: str,
        outcome_summary: str,
        plane: str,
        model: str,
        profile: str,
        success: Optional[int],
        latency: float,
        cost: float,
        context_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[int]: ...
    async def get_similar_task_memories(
        self,
        prompt: str,
        context_id: Optional[str] = None,
        limit: int = 3,
        verified_only: bool = False,
    ) -> List[Dict[str, Any]]: ...
    async def get_task_memory_count(self) -> int: ...

    # ── Task-memory cloud-mirror outbox (UNIGROK_TASK_RAG) ──────────────────
    # `synced_at IS NULL` is the durable outbox; rows are marked in place.
    async def list_unsynced_task_memories(
        self,
        limit: int = 50,
        max_attempts: Optional[int] = None,
        verified_only: bool = False,
    ) -> List[Dict[str, Any]]: ...
    async def mark_task_memory_synced(
        self, memory_id: int, remote_file_id: str
    ) -> None: ...
    async def mark_task_memory_sync_failed(
        self, memory_id: int, error: str
    ) -> None: ...
    async def get_task_memories_by_remote_ids(
        self, file_ids: List[str]
    ) -> List[Dict[str, Any]]: ...
    async def count_unsynced_task_memories(self, verified_only: bool = False) -> int: ...
    async def reset_task_memory_sync(self) -> int: ...

    # ── Grok-owned subordinate provider attempts / xAI outbox ──────────────
    async def begin_provider_attempt(self, start: Any) -> bool: ...
    def canonical_provider_attempt_result(
        self,
        attempt_id: str,
        result: Any,
        redaction_snapshot: Any = None,
    ) -> Any: ...
    async def revoke_provider_attempt_projection(self, attempt_id: str) -> None: ...
    async def complete_provider_attempt(
        self,
        attempt_id: str,
        result: Any,
    ) -> bool: ...
    async def complete_projected_provider_attempt(
        self,
        attempt_id: str,
        projection: Any,
    ) -> bool: ...
    async def mark_stale_provider_attempts_indeterminate(
        self, stale_before: Any
    ) -> int: ...
    async def list_provider_attempts(
        self,
        supervisor_session_id: Optional[str] = None,
        delegation_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]: ...
    async def lease_provider_attempts_for_harvest(
        self,
        lease_id: str,
        lease_seconds: float = 60.0,
        limit: int = 25,
    ) -> List[Dict[str, Any]]: ...
    async def provider_attempt_harvest_lease_is_fresh(
        self,
        attempt_id: str,
        lease_id: str,
        minimum_remaining_seconds: float = 0.0,
    ) -> bool: ...
    async def mark_provider_attempt_harvest_synced(
        self,
        attempt_id: str,
        lease_id: str,
        remote_file_id: str,
    ) -> bool: ...
    async def mark_provider_attempt_harvest_retry(
        self,
        attempt_id: str,
        lease_id: str,
        error: str,
        backoff_seconds: float,
    ) -> bool: ...
    # ── Commit-anchored workspace evidence ─────────────────────────────────
    async def save_workspace_evidence(self, payload: Dict[str, Any]) -> int: ...
    async def get_workspace_evidence(
        self, evidence_id: str, repo_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...
    async def list_workspace_evidence(
        self, repo_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]: ...
    async def list_unsynced_workspace_evidence(
        self,
        limit: int = 50,
        max_attempts: Optional[int] = None,
        repo_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
    async def mark_workspace_evidence_synced(
        self, evidence_id: str, note_ref: str
    ) -> None: ...
    async def mark_workspace_evidence_sync_failed(
        self, evidence_id: str, error: str
    ) -> None: ...
    async def count_workspace_evidence(self, repo_id: Optional[str] = None) -> int: ...
    async def count_unsynced_workspace_evidence(
        self, repo_id: Optional[str] = None
    ) -> int: ...

    # ── Knowledge facts ──────────────────────────────────────────────────────
    async def save_fact(
        self, fact: str, scope: str = "global", source: str = ""
    ) -> Optional[int]: ...
    async def search_facts(
        self, query: str, scope: Optional[str] = None, limit: int = 5
    ) -> List[Dict[str, Any]]: ...
    async def touch_facts(self, fact_ids: List[int]) -> None: ...
    async def delete_fact(self, fact_id: int) -> bool: ...
    async def count_facts(self) -> int: ...
    async def list_facts(
        self, limit: int = 20, scope: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    # ── Swarm optimizer state (v13, src/swarm/) ──────────────────────────────
    async def create_swarm_task(
        self,
        task_id: str,
        target_path: str,
        focus_node: str,
        base_file_hash: str,
        test_target: str,
        bench_command: str,
        budget_usd: float,
        seed: int,
        caller: Optional[str] = None,
        request_id: Optional[str] = None,
        search_strategy: str = "baseline_batch",
        primary_goal: str = "balanced",
        input_kind: str = "workspace",
        analytics_json: Optional[str] = None,
    ) -> None: ...
    async def update_swarm_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        spent_usd: Optional[float] = None,
        generation: Optional[int] = None,
        baseline_json: Optional[str] = None,
        oracle_json: Optional[str] = None,
        folded_state: Optional[str] = None,
        analytics_json: Optional[str] = None,
        champion_id: Optional[str] = None,
    ) -> None: ...
    async def get_swarm_task(self, task_id: str) -> Optional[Dict[str, Any]]: ...
    async def list_swarm_tasks(self, limit: int = 20) -> List[Dict[str, Any]]: ...
    async def insert_swarm_candidate(self, candidate: Dict[str, Any]) -> bool: ...
    async def list_swarm_candidates(
        self,
        task_id: str,
        feasible_only: bool = False,
        generation: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]: ...

    # ── Sessions & messages ──────────────────────────────────────────────────
    async def get_session(self, session_name: str) -> Optional[Dict[str, Any]]: ...
    async def save_session(
        self,
        session_name: str,
        cli_session_id: Optional[str] = None,
        api_thread_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None: ...
    async def delete_session(self, session_name: str) -> None: ...
    async def list_sessions(self) -> List[Dict[str, Any]]: ...
    async def save_message(
        self,
        session_name: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None: ...
    async def replace_messages(
        self, session_name: str, messages: List[Dict[str, Any]]
    ) -> None: ...
    async def load_messages(self, session_name: str) -> List[Dict[str, Any]]: ...

    # ── Deferred jobs ────────────────────────────────────────────────────────
    async def create_job(
        self,
        job_id: str,
        prompt: str,
        model: str,
        caller: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None: ...
    async def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        result: Optional[str] = None,
        cost: Optional[float] = None,
    ) -> None: ...
    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]: ...
    async def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]: ...


def get_store(db_path: Any = None) -> SessionStoreProtocol:
    """Build a session store for the configured backend.

    UNIGROK_STORAGE_BACKEND selects it ('sqlite' default; blank/unset reads
    as sqlite). Unknown values fail fast with NotImplementedError naming the
    supported set — a typo must not silently fall back to SQLite. db_path is
    backend-specific (the SQLite file path; tests use per-test temp paths).
    """
    backend = os.environ.get("UNIGROK_STORAGE_BACKEND", "sqlite").strip().lower() or "sqlite"
    if backend == "sqlite":
        # Late import: src/utils.py imports this factory at module load and
        # calls it for the `store` singleton while still initializing;
        # GrokSessionStore is defined above that call site, so this lookup
        # resolves against the partially-initialized module.
        from .utils import GrokSessionStore

        return GrokSessionStore(db_path)
    raise NotImplementedError(
        f"UNIGROK_STORAGE_BACKEND='{backend}' is not implemented; "
        f"supported backends: {', '.join(SUPPORTED_STORAGE_BACKENDS)}. "
        "See src/storage.py for what a new backend must implement."
    )
