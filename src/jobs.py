# src/jobs.py
# Deferred research jobs: JobManager persists job rows in the shared
# GrokSessionStore jobs table and runs each job through xAI's deferred
# completion service (chat.defer) on a background asyncio task.
#
# Durability model: the ROW is the durable record — job status/result survive
# queries across the server's lifetime. The in-flight asyncio task that owns a
# job does NOT survive a restart, so a queued/running row whose updated_at is
# older than the job timeout is reported as 'stale' instead of pretending the
# work is still in flight.

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .identity import (
    get_active_caller,
    get_active_principal,
    normalize_caller,
    normalize_principal,
    scoped_session,
)
from .utils import (
    AGENTIC_TOOLS_SCHEMA,
    _DISTILL_SYS_PROMPT,
    FactList,
    _bounded_redacted,
    _chat_create_supports,
    _env_timeout,
    _parse_structured,
    check_circuit_breaker,
    get_xai_client,
    record_xai_success,
    redact_secrets,
    resolve_model,
    run_blocking,
    store,
    sync_fact_to_collection,
)

logger = logging.getLogger("GrokMCP.Jobs")


def resolve_job_owner(caller: Optional[str] = None) -> Optional[str]:
    """Return the durable job owner for the current request.

    Authenticated HTTP always uses the server-bound stable principal. Caller
    labels remain attribution-only and cannot replace that owner. Unbound
    local/stdio callers retain their historical explicit-label behavior.
    """
    return get_active_principal() or normalize_caller(caller) or get_active_caller()


def _job_timeout_sec() -> float:
    """UNIGROK_JOB_TIMEOUT_SEC (default 1800) bounds chat.defer()'s blocking
    internal poll; the same window doubles as the staleness horizon for
    queued/running rows whose owning task is gone."""
    try:
        return max(1.0, float(os.environ.get("UNIGROK_JOB_TIMEOUT_SEC", "1800")))
    except ValueError:
        return 1800.0


def _max_concurrent_jobs() -> int:
    """UNIGROK_MAX_CONCURRENT_JOBS (default 4) bounds how many chat.defer()
    calls run at once. Each in-flight defer pins one dedicated timed
    run_blocking thread for up to the full job timeout, and those threads
    share the process-wide UNIGROK_MAX_TIMED_THREADS cap with every other SDK
    call — an unbounded job fan-out would exhaust the cap and starve all
    chat/agent traffic server-wide. Excess jobs stay 'queued' until a slot
    frees."""
    try:
        return max(1, int(os.environ.get("UNIGROK_MAX_CONCURRENT_JOBS", "4")))
    except ValueError:
        return 4


def _job_result_limit() -> int:
    """UNIGROK_JOB_RESULT_MAX_CHARS (default 20000) bounds a persisted job
    result — generous because the result IS the deliverable, but never
    unbounded (every other persisted surface is capped)."""
    try:
        return max(1000, int(os.environ.get("UNIGROK_JOB_RESULT_MAX_CHARS", "20000")))
    except ValueError:
        return 20000


class JobManager:
    """Long-running deferred research jobs over the jobs table.

    submit() persists a 'queued' row and launches a background asyncio task
    that makes one chat.defer() call — defer blocks with internal polling, so
    it runs on a dedicated timed thread via run_blocking, bounded by
    UNIGROK_JOB_TIMEOUT_SEC. At most UNIGROK_MAX_CONCURRENT_JOBS defer calls
    run at once (a semaphore; excess jobs wait 'queued') so long-running jobs
    cannot exhaust the shared timed-thread cap. Server-side tools (web/X
    search + code execution) are attached so the deferred completion can
    research inside xAI infrastructure without a client-side tool loop.
    Results and errors are persisted on the row redacted and bounded (like
    the prompt at create time); get()/list() views mark abandoned rows
    'stale'.
    """

    def __init__(self, job_store: Any = None):
        self._store = job_store if job_store is not None else store
        # In-flight task handles: process-local only (see module docstring).
        self._tasks: Dict[str, asyncio.Task] = {}
        # Concurrency gate for the defer calls (see _max_concurrent_jobs).
        self._defer_slots = asyncio.Semaphore(_max_concurrent_jobs())

    async def submit(
        self,
        prompt: str,
        model: Optional[str] = None,
        agent_count: Optional[int] = None,
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a job row and launch its background defer task.

        The server-bound authenticated principal is persisted when present;
        otherwise the explicit local/stdio caller label is used, followed by
        the transport's reporting identity. None stays None."""
        job_id = uuid.uuid4().hex
        resolved = (model or "").strip() or await resolve_model("planning")
        caller = resolve_job_owner(caller)
        await self._store.create_job(job_id, prompt=prompt, model=resolved, caller=caller)
        task = asyncio.create_task(self._run_job(job_id, prompt, resolved, agent_count))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))
        return {"job_id": job_id, "status": "queued", "model": resolved}

    async def _run_job(
        self,
        job_id: str,
        prompt: str,
        model: str,
        agent_count: Optional[int],
    ) -> None:
        timeout = _job_timeout_sec()
        try:
            # The slot gates the defer call: the row stays 'queued' while
            # waiting, so a burst of submissions never pins more than
            # UNIGROK_MAX_CONCURRENT_JOBS timed threads at once.
            async with self._defer_slots:
                await self._store.update_job(job_id, status="running")

                def _defer_call():
                    from xai_sdk.chat import user

                    client = get_xai_client()
                    chat_params: Dict[str, Any] = {"model": model}
                    if AGENTIC_TOOLS_SCHEMA:
                        # Tier-1 server-side tools run inside xAI infra — safe
                        # with defer's single-response contract (no client loop).
                        chat_params["tools"] = list(AGENTIC_TOOLS_SCHEMA)
                    if agent_count is not None and _chat_create_supports("agent_count"):
                        chat_params["agent_count"] = agent_count
                    chat = client.chat.create(**chat_params)
                    chat.append(user(prompt))
                    return chat.defer(timeout=timedelta(seconds=timeout))

                # Headroom over defer's own timeout so the SDK's timeout error
                # is the one that surfaces, not run_blocking's.
                response = await run_blocking(_defer_call, timeout=timeout + 30.0)
            cost = 0.0
            if getattr(response, "cost_usd", None):
                cost = float(response.cost_usd)
            await self._store.update_job(
                job_id,
                status="done",
                # Redacted and bounded at rest, like create_job's prompt — a
                # deferred answer can echo secrets from its prompt, and the
                # raw column would otherwise defeat that redaction.
                result=_bounded_redacted(
                    str(getattr(response, "content", "") or ""), _job_result_limit()
                ),
                cost=cost,
            )
        except Exception as exc:
            logger.warning(f"Research job {job_id} failed: {exc}")
            try:
                await self._store.update_job(
                    job_id, status="error", result=_bounded_redacted(str(exc), 2000)
                )
            except Exception as persist_err:
                logger.error(f"Research job {job_id}: could not persist error: {persist_err}")

    # ── Knowledge distillation jobs (job type 'distill') ─────────────────────
    async def submit_distill(
        self, session: str, caller: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a 'distill' job row and launch its background task.

        The job summarizes the session's STORED history into 3-8 durable
        facts on the cheap coding model via the shared tool-free
        structured-parse machinery (FactList), redacts every fact, and saves
        them to the knowledge table (scope='global',
        source='session:<name>'). Rides the same jobs-table lifecycle and
        defer-slot semaphore as research jobs — a distill run pins one timed
        thread for the parse call.

        Owner attribution matches submit(): a bound authenticated principal
        wins; unbound local/stdio keeps its explicit/reporting caller label.
        """
        session_name = str(session or "").strip()
        if not session_name:
            return {"error": "Input Validation Error: session must not be empty."}
        # Defense in depth with distill_session: always namespace under the
        # bound principal/client before loading history or persisting source.
        session_name = scoped_session(session_name) or session_name
        job_id = uuid.uuid4().hex
        model = await resolve_model("coding")
        caller = resolve_job_owner(caller)
        await self._store.create_job(
            job_id,
            prompt=f"[distill] session:{session_name}",
            model=model,
            caller=caller,
        )
        task = asyncio.create_task(self._run_distill_job(job_id, session_name, model))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))
        return {"job_id": job_id, "status": "queued", "model": model, "kind": "distill"}

    async def _run_distill_job(self, job_id: str, session: str, model: str) -> None:
        try:
            async with self._defer_slots:
                await self._store.update_job(job_id, status="running")
                messages = await self._store.load_messages(session)
                if not messages:
                    await self._store.update_job(
                        job_id,
                        status="error",
                        result=f"Session '{session}' not found or has no stored history.",
                    )
                    return
                transcript = "\n".join(
                    f"{msg.get('role')}: {str(msg.get('content') or '')[:2000]}"
                    for msg in messages[-80:]
                )
                # The parse is a real paid model call: honor the per-model
                # circuit breaker (an open breaker fails the job cleanly).
                # Parse failures are NOT recorded as breaker failures —
                # _parse_structured folds capability-missing and upstream
                # errors into one None, and ticking the breaker on a missing
                # SDK capability would poison the model for real traffic.
                check_circuit_breaker(model)
                parsed, _tokens, cost = await _parse_structured(
                    FactList,
                    _DISTILL_SYS_PROMPT,
                    transcript[:60000],
                    model,
                    timeout=_env_timeout("UNIGROK_DISTILL_TIMEOUT", 120.0),
                    logger=logger,
                )
                if parsed is None:
                    await self._store.update_job(
                        job_id,
                        status="error",
                        result="Distillation unavailable: structured parse failed or is unsupported by the installed SDK.",
                    )
                    return
                record_xai_success(model)
                source = f"session:{session}"
                saved_ids = []
                for fact in parsed.facts:
                    # Redacted here AND in save_fact — a distilled fact can
                    # echo secrets from the transcript.
                    clean = redact_secrets(str(fact or "")).strip()
                    if not clean:
                        continue
                    fact_id = await self._store.save_fact(
                        clean, scope="global", source=source
                    )
                    if fact_id is None:
                        continue
                    saved_ids.append(fact_id)
                    # Best-effort cloud mirror (no-op unless
                    # UNIGROK_COLLECTIONS=1 and the SDK is capable).
                    await sync_fact_to_collection(fact_id, clean, scope="global", source=source)
                await self._store.update_job(
                    job_id,
                    status="done",
                    result=f"Distilled {len(saved_ids)} facts from session '{session}'.",
                    cost=cost,
                )
        except Exception as exc:
            logger.warning(f"Distill job {job_id} failed: {exc}")
            try:
                await self._store.update_job(
                    job_id, status="error", result=_bounded_redacted(str(exc), 2000)
                )
            except Exception as persist_err:
                logger.error(f"Distill job {job_id}: could not persist error: {persist_err}")

    @staticmethod
    def _is_stale(row: Dict[str, Any]) -> bool:
        """A queued/running row not touched within the job timeout: the task
        that owned it did not survive (restart/crash), so the row can never
        finish on its own."""
        if str(row.get("status")) not in ("queued", "running"):
            return False
        try:
            updated = datetime.fromisoformat(str(row.get("updated_at")))
        except (TypeError, ValueError):
            return True
        return (datetime.now() - updated).total_seconds() > _job_timeout_sec()

    @classmethod
    def describe(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        """Public view of a job row (what get_research_job returns)."""
        status = str(row.get("status") or "unknown")
        if cls._is_stale(row):
            status = "stale"
        view: Dict[str, Any] = {
            "job_id": row.get("id"),
            "status": status,
            "model": row.get("model"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        if row.get("caller"):
            # Submitting agent's identity (v8 column) — absent on old rows
            # and anonymous submissions.
            view["caller"] = row.get("caller")
        if row.get("request_id"):
            # Correlation id bound when the job was submitted (v9 column) —
            # ties the row back to the gateway request / log lines that
            # spawned it. Absent on old rows and unattributed submissions.
            view["request_id"] = row.get("request_id")
        if status == "done":
            view["result"] = row.get("result")
            view["cost_usd"] = float(row.get("cost") or 0.0)
        elif status == "error":
            view["error"] = row.get("result")
        return view

    @staticmethod
    def _caller_may_view(
        row_caller: Optional[str], requester: Optional[str]
    ) -> bool:
        """When an owner identity is bound, only that owner's rows are visible.
        Unbound requesters keep the historical open local view."""
        req = normalize_principal(requester)
        if not req:
            return True
        return normalize_principal(row_caller) == req

    async def get(
        self, job_id: str, caller: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        row = await self._store.get_job(job_id)
        if row is None:
            return None
        if not self._caller_may_view(row.get("caller"), caller):
            return None
        return self.describe(row)

    async def list(
        self, limit: int = 20, caller: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        requester = normalize_principal(caller)
        rows = await self._store.list_jobs(limit, caller=requester)
        return [self.describe(row) for row in rows]

    async def wait(self, job_id: str) -> None:
        """Await a job's in-flight task if this process owns one (used by
        tests and graceful shutdown; a no-op for finished/foreign jobs)."""
        task = self._tasks.get(job_id)
        if task is not None:
            await task


_JOB_MANAGER = JobManager()


def get_job_manager() -> JobManager:
    return _JOB_MANAGER
