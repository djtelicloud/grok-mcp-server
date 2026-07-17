from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?im)\b((?:xai|openai|anthropic|claude|gemini|google|github|gh|aws|"
            r"azure|npm|pypi)[A-Z0-9_ -]*(?:api[_ -]?key|token|secret|password))"
            r"\s*[:=]\s*([^\s,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"\b(?:xai-|sk-(?:proj-)?|gh[opusr]_)[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def redact_secrets(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def normalize_session(value: Any) -> str:
    session = str(value or "").strip()
    if not SESSION_PATTERN.fullmatch(session):
        raise ValueError(
            "session must be 1-128 characters using letters, numbers, '.', '_', ':', '/', or '-'"
        )
    return session


def normalize_scope(value: Any) -> str:
    scope = str(value or "global").strip() or "global"
    if not SCOPE_PATTERN.fullmatch(scope):
        raise ValueError(
            "scope must be 1-128 characters using letters, numbers, '.', '_', ':', '/', or '-'"
        )
    return scope


def _terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.lower() for match in TERM_PATTERN.findall(value)))


class PublicStateStore:
    """Small local SQLite store for public sessions and distilled facts.

    Connections are short-lived and every write is a single transaction. This
    keeps the public service resilient across container restarts without
    inheriting any IDE, Git, shell, or provider state.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = (
            path
            or os.environ.get("UNIGROK_STATE_PATH")
            or (Path.home() / ".local" / "share" / "unigrok" / "public-state.db")
        )
        self.path = Path(configured).expanduser()
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    model TEXT,
                    plane TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL REFERENCES sessions(name) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                );
                CREATE INDEX IF NOT EXISTS messages_session_id
                    ON messages(session_name, id);
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    fact TEXT NOT NULL,
                    terms TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    uses INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(scope, fact)
                );
                CREATE INDEX IF NOT EXISTS knowledge_scope_id
                    ON knowledge(scope, id DESC);
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    request_kind TEXT NOT NULL,
                    route TEXT,
                    requested_plane TEXT,
                    resolved_plane TEXT,
                    model TEXT,
                    success INTEGER,
                    verified INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    fallback_reason TEXT,
                    stop_reason TEXT,
                    metadata TEXT
                );
                CREATE INDEX IF NOT EXISTS telemetry_created_at
                    ON telemetry(created_at DESC);
                CREATE INDEX IF NOT EXISTS telemetry_caller_created
                    ON telemetry(caller, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT
                );
                """
            )

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def _read(self, operation: Callable[..., Any], *args: Any) -> Any:
        await self.initialize()
        return await asyncio.to_thread(operation, *args)

    async def _write(self, operation: Callable[..., Any], *args: Any) -> Any:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(operation, *args)

    def _health_sync(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    async def health(self) -> bool:
        try:
            return bool(await self._read(self._health_sync))
        except (OSError, sqlite3.Error):
            return False

    def _append_turn_sync(
        self,
        session: str,
        user_text: str,
        assistant_text: str,
        model: str | None,
        plane: str | None,
        metadata: dict[str, Any] | None,
    ) -> int:
        now = utc_now()
        safe_user = redact_secrets(user_text).strip()
        safe_assistant = redact_secrets(assistant_text).strip()
        safe_metadata = json.dumps(metadata, separators=(",", ":")) if metadata else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(name, created_at, updated_at, model, plane)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    model=COALESCE(excluded.model, sessions.model),
                    plane=COALESCE(excluded.plane, sessions.plane)
                """,
                (session, now, now, model, plane),
            )
            connection.executemany(
                """
                INSERT INTO messages(session_name, role, content, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (session, "user", safe_user, now, None),
                    (session, "assistant", safe_assistant, now, safe_metadata),
                ),
            )
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_name=?", (session,)
            ).fetchone()
            connection.commit()
        return int(row[0] if row else 0)

    async def append_turn(
        self,
        session: str,
        user_text: str,
        assistant_text: str,
        *,
        model: str | None = None,
        plane: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return int(
            await self._write(
                self._append_turn_sync,
                normalize_session(session),
                user_text,
                assistant_text,
                model,
                plane,
                metadata,
            )
        )

    def _load_messages_sync(self, session: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at, metadata FROM (
                    SELECT id, role, content, created_at, metadata
                    FROM messages WHERE session_name=? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata: dict[str, Any] | None = None
            if row["metadata"]:
                try:
                    parsed = json.loads(row["metadata"])
                    metadata = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    metadata = None
            result.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "metadata": metadata,
                }
            )
        return result

    async def load_messages(self, session: str, limit: int = 24) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit or 24), 100))
        return list(await self._read(self._load_messages_sync, normalize_session(session), bounded))

    def _list_sessions_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.name, s.created_at, s.updated_at, s.model, s.plane,
                       COUNT(m.id) AS message_count
                FROM sessions s LEFT JOIN messages m ON m.session_name=s.name
                GROUP BY s.name ORDER BY s.updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit or 50), 100))
        return list(await self._read(self._list_sessions_sync, bounded))

    def _delete_session_sync(self, session: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE name=?", (session,))
            connection.commit()
        return bool(cursor.rowcount)

    async def delete_session(self, session: str) -> bool:
        return bool(await self._write(self._delete_session_sync, normalize_session(session)))

    def _save_fact_sync(self, fact: str, scope: str, source: str) -> int:
        now = utc_now()
        terms = " ".join(_terms(fact))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO knowledge(scope, fact, terms, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, fact) DO UPDATE SET source=excluded.source
                """,
                (scope, fact, terms, source, now),
            )
            row = connection.execute(
                "SELECT id FROM knowledge WHERE scope=? AND fact=?", (scope, fact)
            ).fetchone()
            connection.commit()
        return int(row[0])

    async def save_fact(self, fact: str, *, scope: str = "global", source: str = "manual") -> int:
        safe_fact = redact_secrets(fact).strip()
        if not safe_fact:
            raise ValueError("fact must not be empty")
        if len(safe_fact) > 4_000:
            raise ValueError("fact exceeds the 4000 character limit")
        return int(
            await self._write(
                self._save_fact_sync,
                safe_fact,
                normalize_scope(scope),
                str(source or "manual")[:160],
            )
        )

    def _search_facts_sync(self, query: str, scope: str | None, limit: int) -> list[dict[str, Any]]:
        query_terms = set(_terms(query))
        if not query_terms:
            return []
        with self._connect() as connection:
            if scope and scope != "global":
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope IN ('global', ?) "
                    "ORDER BY id DESC LIMIT 400",
                    (scope,),
                ).fetchall()
            elif scope:
                rows = connection.execute(
                    "SELECT * FROM knowledge WHERE scope='global' ORDER BY id DESC LIMIT 400"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM knowledge ORDER BY id DESC LIMIT 400"
                ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            row_terms = set(str(row["terms"] or "").split())
            overlap = len(query_terms & row_terms)
            if overlap <= 0:
                continue
            item = dict(row)
            item["score"] = overlap / max(len(query_terms), 1)
            scored.append(item)
        scored.sort(key=lambda item: (float(item["score"]), int(item["id"])), reverse=True)
        return scored[:limit]

    async def search_facts(
        self, query: str, *, scope: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if not text:
            raise ValueError("query must not be empty")
        bounded = max(1, min(int(limit or 5), 25))
        scope_value = normalize_scope(scope) if scope else None
        return list(await self._read(self._search_facts_sync, text, scope_value, bounded))

    def _touch_facts_sync(self, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        now = utc_now()
        with self._connect() as connection:
            connection.executemany(
                "UPDATE knowledge SET last_used_at=?, uses=uses+1 WHERE id=?",
                ((now, fact_id) for fact_id in fact_ids),
            )
            connection.commit()

    async def touch_facts(self, fact_ids: list[int]) -> None:
        ids = list(dict.fromkeys(int(value) for value in fact_ids))
        await self._write(self._touch_facts_sync, ids)

    def _delete_fact_sync(self, fact_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM knowledge WHERE id=?", (fact_id,))
            connection.commit()
        return bool(cursor.rowcount)

    async def delete_fact(self, fact_id: int) -> bool:
        return bool(await self._write(self._delete_fact_sync, int(fact_id)))

    def _save_telemetry_sync(self, row: dict[str, Any]) -> int:
        safe_metadata = json.dumps(
            row.get("metadata") or {}, separators=(",", ":"), sort_keys=True
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO telemetry(
                    created_at, caller, request_kind, route, requested_plane,
                    resolved_plane, model, success, verified, latency_ms,
                    cost_usd, fallback_reason, stop_reason, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    str(row.get("caller") or "anonymous")[:160],
                    str(row.get("request_kind") or "agent")[:80],
                    str(row.get("route") or "")[:80] or None,
                    str(row.get("requested_plane") or "")[:24] or None,
                    str(row.get("resolved_plane") or "")[:24] or None,
                    str(row.get("model") or "")[:160] or None,
                    None if row.get("success") is None else int(bool(row.get("success"))),
                    int(bool(row.get("verified"))),
                    max(0, int(row.get("latency_ms") or 0)),
                    max(0.0, float(row.get("cost_usd") or 0.0)),
                    str(row.get("fallback_reason") or "")[:120] or None,
                    str(row.get("stop_reason") or "")[:80] or None,
                    safe_metadata,
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    async def save_telemetry(self, row: dict[str, Any]) -> int:
        return int(await self._write(self._save_telemetry_sync, dict(row)))

    def _save_agent_job_sync(self, job_id: str, status: str, payload: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_jobs (job_id, created_at, status, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
                "status=excluded.status, payload=excluded.payload",
                (job_id, utc_now(), status, payload),
            )
            connection.execute(
                "DELETE FROM agent_jobs WHERE created_at < datetime('now', '-1 day')"
            )
            connection.commit()

    async def save_agent_job(
        self, job_id: str, status: str, payload: dict[str, Any] | None = None
    ) -> None:
        encoded = (
            json.dumps(payload, separators=(",", ":"), default=str)
            if payload is not None
            else None
        )
        await self._write(self._save_agent_job_sync, job_id, status, encoded)

    def _load_agent_job_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, payload FROM agent_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        payload: dict[str, Any] | None = None
        if row["payload"]:
            try:
                decoded = json.loads(row["payload"])
                payload = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                payload = None
        return {"status": str(row["status"]), "payload": payload}

    async def load_agent_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._read(self._load_agent_job_sync, job_id)

    def _record_benchmark_result_sync(
        self, telemetry_id: int, success: bool, note: str
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata FROM telemetry WHERE id=?", (telemetry_id,)
            ).fetchone()
            if row is None:
                return False
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["benchmark_feedback"] = {
                "success": bool(success),
                "note": redact_secrets(note).strip()[:1000],
                "recorded_at": utc_now(),
            }
            connection.execute(
                "UPDATE telemetry SET success=?, verified=1, metadata=? WHERE id=?",
                (
                    int(bool(success)),
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    telemetry_id,
                ),
            )
            connection.commit()
        return True

    async def record_benchmark_result(
        self, telemetry_id: int, success: bool, note: str = ""
    ) -> bool:
        return bool(
            await self._write(
                self._record_benchmark_result_sync,
                int(telemetry_id),
                bool(success),
                note,
            )
        )

    def _telemetry_summary_sync(self, limit: int) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        items = [dict(row) for row in rows]
        latencies = sorted(int(item["latency_ms"] or 0) for item in items)

        def percentile(fraction: float) -> int:
            if not latencies:
                return 0
            index = min(len(latencies) - 1, round((len(latencies) - 1) * fraction))
            return latencies[index]

        def aggregate(field: str) -> list[dict[str, Any]]:
            grouped: dict[str, dict[str, Any]] = {}
            for item in items:
                key = str(item.get(field) or "unknown")
                bucket = grouped.setdefault(
                    key,
                    {"name": key, "calls": 0, "verified": 0, "successes": 0, "cost_usd": 0.0},
                )
                bucket["calls"] += 1
                bucket["cost_usd"] += float(item.get("cost_usd") or 0.0)
                if item.get("verified"):
                    bucket["verified"] += 1
                    bucket["successes"] += int(bool(item.get("success")))
            result = []
            for bucket in grouped.values():
                verified = int(bucket["verified"])
                bucket["success_rate"] = (
                    round(int(bucket["successes"]) / verified, 4) if verified else None
                )
                bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)
                result.append(bucket)
            return sorted(result, key=lambda value: int(value["calls"]), reverse=True)

        verified = [item for item in items if item.get("verified")]
        return {
            "sample_size": len(items),
            "verified_samples": len(verified),
            "verified_success_rate": (
                round(sum(int(bool(item.get("success"))) for item in verified) / len(verified), 4)
                if verified
                else None
            ),
            "latency_ms": {
                "average": round(sum(latencies) / len(latencies)) if latencies else 0,
                "p50": percentile(0.50),
                "p95": percentile(0.95),
            },
            "cost_usd": round(sum(float(item.get("cost_usd") or 0.0) for item in items), 6),
            "callers": aggregate("caller"),
            "models": aggregate("model"),
            "routes": aggregate("route"),
            "planes": aggregate("resolved_plane"),
            "fallbacks": aggregate("fallback_reason"),
            "recent": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "created_at",
                        "caller",
                        "request_kind",
                        "route",
                        "resolved_plane",
                        "model",
                        "success",
                        "verified",
                        "latency_ms",
                        "cost_usd",
                        "fallback_reason",
                        "stop_reason",
                    )
                }
                for item in items[:25]
            ],
        }

    async def telemetry_summary(self, limit: int = 1000) -> dict[str, Any]:
        bounded = max(1, min(int(limit or 1000), 10_000))
        return dict(await self._read(self._telemetry_summary_sync, bounded))
