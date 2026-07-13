"""Private, crash-safe accounting for bounded Stage 1 provider attempts.

The ledger persists the run contract and owns provider-call authority. Claims
are run-scoped, atomic, and require the active lease owner's capability. An
expired lease may be taken over, but takeover first seals every unfinished call
as indeterminate so uncertain provider work is never retried.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .provider_adapters import (
    _is_in_git_workspace,
    _is_within,
    _private_directory,
    _reject_secret_like_payload,
    _reject_symlink_components,
    _repo_root,
)
from .stage1_artifacts import ArtifactRef, MAX_ARTIFACT_BYTES


DEFAULT_CAMPAIGN_ID = "gemma-needle-2000-v1"
DEFAULT_ROLE_LIMITS = {
    "seed_author": 30,
    "mutator": 30,
    "critic": 30,
    "adjudicator": 30,
}
_DIGEST = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MODEL_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_WORK_KEY = re.compile(r"^lwk:[0-9a-f]{64}$")
_ARTIFACT_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")
_MAX_LOGICAL_KEY_LENGTH = 512
_MAX_ARTIFACT_PATH_LENGTH = 1_024
_SCHEMA_VERSION = 2


_LEDGER_META_DDL = """
CREATE TABLE ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""
_RUN_CONTRACTS_DDL = """
CREATE TABLE run_contracts (
    run_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    config_digest TEXT NOT NULL,
    total_limit INTEGER NOT NULL CHECK (total_limit > 0),
    role_limits_json TEXT NOT NULL,
    lease_owner_digest TEXT NOT NULL,
    lease_deadline TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, schema_version, run_id)
)
"""
_ATTEMPTS_DDL = """
CREATE TABLE attempts (
    work_item_id TEXT PRIMARY KEY,
    logical_work_key TEXT NOT NULL,
    role TEXT NOT NULL,
    root_reference TEXT,
    variant_key TEXT,
    run_id TEXT NOT NULL REFERENCES run_contracts(run_id) ON DELETE RESTRICT,
    campaign_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    config_digest TEXT NOT NULL,
    template_digest TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    cache_digest TEXT NOT NULL,
    response_digest TEXT,
    receipt_digest TEXT,
    output_digest TEXT,
    output_relative_path TEXT,
    output_size_bytes INTEGER,
    status TEXT NOT NULL CHECK (
        status IN ('started', 'completed', 'failed', 'indeterminate')
    ),
    terminal_code TEXT,
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    UNIQUE (run_id, logical_work_key),
    CHECK (
        (status = 'started' AND terminal_at IS NULL)
        OR (status <> 'started' AND terminal_at IS NOT NULL)
    ),
    CHECK (
        (output_digest IS NULL AND output_relative_path IS NULL AND output_size_bytes IS NULL)
        OR (
            output_digest IS NOT NULL
            AND output_relative_path IS NOT NULL
            AND output_size_bytes IS NOT NULL
            AND output_size_bytes > 0
        )
    ),
    CHECK (
        (status = 'started' AND terminal_code IS NULL)
        OR (
            status = 'completed'
            AND terminal_code IS NULL
            AND response_digest IS NOT NULL
            AND receipt_digest IS NOT NULL
            AND output_digest IS NOT NULL
        )
        OR (
            status IN ('failed', 'indeterminate')
            AND terminal_code IS NOT NULL
        )
    )
)
"""
_ROLE_INDEX_DDL = "CREATE INDEX idx_attempt_run_role ON attempts(run_id, role)"
_STATUS_INDEX_DDL = "CREATE INDEX idx_attempt_run_status ON attempts(run_id, status)"
_EXPECTED_SCHEMA = {
    "attempts": ("table", _ATTEMPTS_DDL),
    "idx_attempt_run_role": ("index", _ROLE_INDEX_DDL),
    "idx_attempt_run_status": ("index", _STATUS_INDEX_DDL),
    "ledger_meta": ("table", _LEDGER_META_DDL),
    "run_contracts": ("table", _RUN_CONTRACTS_DDL),
}


class AttemptStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class AttemptLimitExceededError(RuntimeError):
    """A persisted total or role-specific provider-call ceiling was reached."""


class AttemptConflictError(RuntimeError):
    """A logical work key was reused with contradictory immutable metadata."""


class RunContractConflictError(RuntimeError):
    """A persisted run was reopened with a different immutable contract."""


class RunLeaseConflictError(RuntimeError):
    """A run lease is active for another owner, or the owner is no longer current."""


class RunLeaseExpiredError(RuntimeError):
    """The current owner must renew or safely take over the expired run lease."""


class InvalidTerminalTransitionError(RuntimeError):
    """An unknown, foreign-owned, or already-terminal work item was transitioned."""


@dataclass(frozen=True)
class ClaimResult:
    """Result of an atomic claim; only ``claimed=True`` grants call authority."""

    work_item_id: str
    logical_work_key: str
    role: str
    status: AttemptStatus
    claimed: bool


@dataclass(frozen=True)
class LeaseResult:
    """Result of creating, renewing, or taking over one persisted run lease."""

    run_id: str
    lease_deadline: str
    taken_over: bool
    reconciled_work_item_ids: tuple[str, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_datetime(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _clock_now(clock: Callable[[], datetime]) -> datetime:
    return _aware_datetime(clock(), "Attempt ledger clock")


def _timestamp(value: datetime) -> str:
    return _aware_datetime(value, "timestamp").isoformat()


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Persisted {field} is not a valid timestamp.") from exc
    return _aware_datetime(parsed, field)


def _identity(value: str, field: str) -> str:
    cleaned = str(value).strip()
    if not _IDENTITY.fullmatch(cleaned):
        raise ValueError(f"{field} must be a bounded opaque identifier.")
    _reject_secret_like_payload(cleaned)
    return cleaned


def _model_identity(value: str) -> str:
    cleaned = str(value).strip()
    if not _MODEL_IDENTITY.fullmatch(cleaned) or cleaned.startswith("/"):
        raise ValueError("model must be a bounded opaque model identifier.")
    _reject_secret_like_payload(cleaned)
    return cleaned


def _optional_identity(value: str | None, field: str) -> str | None:
    return None if value is None else _identity(value, field)


def _terminal_code(value: str) -> str:
    cleaned = str(value).strip()
    if _ERROR_CODE.fullmatch(cleaned) is None:
        raise ValueError("terminal code must be a bounded machine-readable error code.")
    return cleaned


def _logical_key(value: str) -> str:
    cleaned = str(value).strip()
    if (
        not cleaned
        or len(cleaned) > _MAX_LOGICAL_KEY_LENGTH
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise ValueError(
            "logical work components must be non-empty, bounded, and printable."
        )
    return cleaned


def _generated_logical_key(value: str) -> str:
    cleaned = str(value).strip().lower()
    if _WORK_KEY.fullmatch(cleaned) is None:
        raise ValueError(
            "logical_work_key must come from AttemptLedger.make_logical_work_key()."
        )
    return cleaned


def _digest(value: str, field: str) -> str:
    match = _DIGEST.fullmatch(str(value).strip().lower())
    if match is None:
        raise ValueError(f"{field} must be a SHA-256 digest.")
    return f"sha256:{match.group(1)}"


def _optional_digest(value: str | None, field: str) -> str | None:
    return None if value is None else _digest(value, field)


def _owner_digest(owner_token: str) -> str:
    token = str(owner_token)
    if (
        len(token) < 16
        or len(token) > 256
        or any(ord(character) < 33 for character in token)
    ):
        raise ValueError(
            "owner_token must be an unpersisted, bounded process capability."
        )
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _canonical_role_limits(
    role_limits: Mapping[str, int],
) -> tuple[dict[str, int], str]:
    normalized: dict[str, int] = {}
    for raw_role, raw_limit in role_limits.items():
        role = _identity(raw_role, "role")
        if (
            not isinstance(raw_limit, int)
            or isinstance(raw_limit, bool)
            or raw_limit <= 0
        ):
            raise ValueError("Every role attempt limit must be a positive integer.")
        normalized[role] = raw_limit
    if not normalized:
        raise ValueError("At least one role-specific attempt limit is required.")
    serialized = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return normalized, serialized


def _artifact_fields(
    reference: ArtifactRef | None,
) -> tuple[str | None, str | None, int | None]:
    if reference is None:
        return None, None, None
    if not isinstance(reference, ArtifactRef):
        raise ValueError("output_artifact must be an ArtifactRef.")
    digest = _digest(reference.digest, "output_artifact.digest")
    relative = Path(reference.relative_path)
    relative_text = relative.as_posix()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or len(relative_text) > _MAX_ARTIFACT_PATH_LENGTH
        or relative.suffix != ".json"
        or any(_ARTIFACT_SEGMENT.fullmatch(part) is None for part in relative.parts)
    ):
        raise ValueError(
            "output_artifact.relative_path must be a safe relative JSON path."
        )
    if (
        not isinstance(reference.size_bytes, int)
        or isinstance(reference.size_bytes, bool)
        or reference.size_bytes <= 0
        or reference.size_bytes > MAX_ARTIFACT_BYTES
    ):
        raise ValueError("output_artifact.size_bytes is invalid.")
    return digest, relative_text, reference.size_bytes


def _normalize_sql(value: str) -> str:
    return " ".join(value.split()).casefold()


def _schema_fingerprint() -> str:
    payload = "\n".join(
        f"{name}|{kind}|{_normalize_sql(sql)}"
        for name, (kind, sql) in sorted(_EXPECTED_SCHEMA.items())
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AttemptLedger:
    """SQLite ledger with persisted contracts, exclusive leases, and hard caps."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        total_limit: int = 120,
        role_limits: Mapping[str, int] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        busy_timeout_ms: int = 10_000,
    ) -> None:
        if (
            not isinstance(total_limit, int)
            or isinstance(total_limit, bool)
            or total_limit <= 0
        ):
            raise ValueError("total_limit must be a positive integer.")
        if not isinstance(busy_timeout_ms, int) or busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be a positive integer.")
        configured = DEFAULT_ROLE_LIMITS if role_limits is None else role_limits
        self.role_limits, self._role_limits_json = _canonical_role_limits(configured)
        self.total_limit = total_limit
        self._clock = clock
        self._busy_timeout_ms = busy_timeout_ms
        self._closed = False
        self.db_path = self._prepare_path(Path(db_path).expanduser())
        self._initialize_database()

    @staticmethod
    def make_owner_token() -> str:
        """Return a process capability; callers must never persist or print it."""

        return "owner:" + secrets.token_hex(32)

    @staticmethod
    def make_logical_work_key(*components: str) -> str:
        if not components:
            raise ValueError("At least one logical work component is required.")
        encoded: list[bytes] = []
        for component in components:
            value = _logical_key(component)
            payload = value.encode("utf-8")
            encoded.append(len(payload).to_bytes(4, "big") + payload)
        return "lwk:" + hashlib.sha256(b"".join(encoded)).hexdigest()

    @staticmethod
    def _work_item_id(
        campaign_id: str,
        schema_version: str,
        run_id: str,
        logical_work_key: str,
    ) -> str:
        payload = "\0".join(
            (campaign_id, schema_version, run_id, logical_work_key)
        ).encode("utf-8")
        return "work:" + hashlib.sha256(payload).hexdigest()

    def _prepare_path(self, requested_path: Path) -> Path:
        absolute = Path(os.path.abspath(requested_path))
        _reject_symlink_components(absolute)
        resolved = absolute.resolve(strict=False)
        if _is_within(resolved, _repo_root()) or _is_in_git_workspace(resolved):
            raise ValueError(
                "Attempt ledger must remain outside every repository workspace."
            )
        _private_directory(resolved.parent)
        if resolved.exists():
            self._validate_private_regular_file(resolved)
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(resolved, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory(resolved.parent)
        self._validate_private_regular_file(resolved)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{resolved}{suffix}")
            if sidecar.exists():
                self._validate_private_regular_file(sidecar)
        return resolved

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_private_regular_file(path: Path) -> None:
        _reject_symlink_components(path)
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        ):
            raise ValueError(
                "Attempt ledger files must be owner-only regular files (0600)."
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AttemptLedger is closed.")

    def _secure_database_files(self) -> None:
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError(
                    "SQLite ledger artifacts cannot be opened safely."
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError(
                        "SQLite ledger artifacts must remain regular files."
                    )
                if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
                    raise RuntimeError(
                        "SQLite ledger artifacts must remain owner-controlled."
                    )
                os.fchmod(descriptor, 0o600)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    raise RuntimeError(
                        "SQLite ledger artifacts must remain owner-only (0600)."
                    )
            finally:
                os.close(descriptor)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        connection = sqlite3.connect(
            self.db_path,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            self._secure_database_files()
            yield connection
        finally:
            connection.close()
            self._secure_database_files()

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if [str(row[0]).casefold() for row in integrity_rows] != ["ok"]:
            raise RuntimeError("Attempt ledger integrity check failed.")
        rows = connection.execute(
            """
            SELECT name, type, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'index', 'view', 'trigger')
            """
        ).fetchall()
        actual = {str(row["name"]): (str(row["type"]), row["sql"]) for row in rows}
        if set(actual) != set(_EXPECTED_SCHEMA):
            raise RuntimeError(
                "Attempt ledger schema objects do not match the pinned schema."
            )
        for name, (expected_kind, expected_sql) in _EXPECTED_SCHEMA.items():
            actual_kind, actual_sql = actual[name]
            if actual_sql is None or actual_kind != expected_kind:
                raise RuntimeError("Attempt ledger schema object type is invalid.")
            if _normalize_sql(str(actual_sql)) != _normalize_sql(expected_sql):
                raise RuntimeError(
                    f"Attempt ledger schema definition changed for {name!r}."
                )
        row = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_fingerprint'"
        ).fetchone()
        if row is None or row["value"] != _schema_fingerprint():
            raise RuntimeError(
                "Attempt ledger schema fingerprint is missing or invalid."
            )

    def _initialize_database(self) -> None:
        with self._connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).casefold() != "wal":
                raise RuntimeError("Attempt ledger requires SQLite WAL mode.")
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    objects = connection.execute(
                        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    if objects:
                        raise RuntimeError(
                            "Unversioned attempt ledger data is unsafe to migrate."
                        )
                    for ddl in (
                        _LEDGER_META_DDL,
                        _RUN_CONTRACTS_DDL,
                        _ATTEMPTS_DDL,
                        _ROLE_INDEX_DDL,
                        _STATUS_INDEX_DDL,
                    ):
                        connection.execute(ddl)
                    connection.execute(
                        "INSERT INTO ledger_meta(key, value) VALUES ('schema_fingerprint', ?)",
                        (_schema_fingerprint(),),
                    )
                    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                elif version != _SCHEMA_VERSION:
                    raise RuntimeError("Attempt ledger schema version is unsupported.")
                self._verify_schema(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def acquire_run_lease(
        self,
        *,
        run_id: str,
        owner_token: str,
        campaign_id: str,
        schema_version: str,
        manifest_digest: str,
        config_digest: str,
        lease_deadline: datetime,
    ) -> LeaseResult:
        """Create or renew a lease, or take it over only after its deadline."""

        run_id = _identity(run_id, "run_id")
        owner = _owner_digest(owner_token)
        campaign_id = _identity(campaign_id, "campaign_id")
        schema_version = _identity(schema_version, "schema_version")
        manifest_digest = _digest(manifest_digest, "manifest_digest")
        config_digest = _digest(config_digest, "config_digest")
        now = _clock_now(self._clock)
        requested_deadline = _aware_datetime(lease_deadline, "lease_deadline")
        if requested_deadline <= now:
            raise ValueError("lease_deadline must be in the future.")
        now_text = _timestamp(now)
        deadline_text = _timestamp(requested_deadline)
        immutable = {
            "campaign_id": campaign_id,
            "schema_version": schema_version,
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "total_limit": self.total_limit,
            "role_limits_json": self._role_limits_json,
        }

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM run_contracts WHERE run_id = ?", (run_id,)
                ).fetchone()
                reconciled: list[str] = []
                taken_over = False
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO run_contracts (
                            run_id, campaign_id, schema_version, manifest_digest, config_digest,
                            total_limit, role_limits_json, lease_owner_digest, lease_deadline,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            campaign_id,
                            schema_version,
                            manifest_digest,
                            config_digest,
                            self.total_limit,
                            self._role_limits_json,
                            owner,
                            deadline_text,
                            now_text,
                            now_text,
                        ),
                    )
                else:
                    mismatches = [
                        field
                        for field, value in immutable.items()
                        if row[field] != value
                    ]
                    if mismatches:
                        raise RunContractConflictError(
                            "Persisted run contract changed for: "
                            + ", ".join(mismatches)
                        )
                    existing_deadline = _parse_timestamp(
                        row["lease_deadline"], "lease_deadline"
                    )
                    lease_expired = existing_deadline <= now
                    if row["lease_owner_digest"] != owner and not lease_expired:
                        raise RunLeaseConflictError(
                            "Run lease is active for another owner."
                        )
                    if lease_expired:
                        attempt_rows = connection.execute(
                            """
                            SELECT work_item_id FROM attempts
                            WHERE run_id = ? AND status = 'started'
                            ORDER BY started_at, work_item_id
                            """,
                            (run_id,),
                        ).fetchall()
                        reconciled = [
                            str(attempt["work_item_id"]) for attempt in attempt_rows
                        ]
                        if reconciled:
                            connection.execute(
                                """
                                UPDATE attempts
                                SET status = 'indeterminate', terminal_code = 'lease_expired',
                                    terminal_at = ?
                                WHERE run_id = ? AND status = 'started'
                                """,
                                (now_text, run_id),
                            )
                        taken_over = row["lease_owner_digest"] != owner
                    elif requested_deadline < existing_deadline:
                        requested_deadline = existing_deadline
                        deadline_text = _timestamp(existing_deadline)
                    connection.execute(
                        """
                        UPDATE run_contracts
                        SET lease_owner_digest = ?, lease_deadline = ?, updated_at = ?
                        WHERE run_id = ?
                        """,
                        (owner, deadline_text, now_text, run_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return LeaseResult(
            run_id=run_id,
            lease_deadline=deadline_text,
            taken_over=taken_over,
            reconciled_work_item_ids=tuple(reconciled),
        )

    @staticmethod
    def _work_contract_mismatches(
        contract: sqlite3.Row,
        *,
        campaign_id: str,
        schema_version: str,
        manifest_digest: str,
        config_digest: str,
    ) -> list[str]:
        expected = {
            "campaign_id": campaign_id,
            "schema_version": schema_version,
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
        }
        return [field for field, value in expected.items() if contract[field] != value]

    def _require_active_lease(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        owner: str,
        now: datetime,
    ) -> sqlite3.Row:
        contract = connection.execute(
            "SELECT * FROM run_contracts WHERE run_id = ?", (run_id,)
        ).fetchone()
        if contract is None:
            raise RunLeaseConflictError(
                "Run contract and lease have not been acquired."
            )
        if contract["lease_owner_digest"] != owner:
            raise RunLeaseConflictError("Run lease is owned by another process.")
        if _parse_timestamp(contract["lease_deadline"], "lease_deadline") <= now:
            raise RunLeaseExpiredError(
                "Run lease expired before provider work was claimed."
            )
        return contract

    def claim(
        self,
        *,
        role: str,
        logical_work_key: str,
        run_id: str,
        owner_token: str,
        campaign_id: str,
        schema_version: str,
        manifest_digest: str,
        config_digest: str,
        template_digest: str,
        provider: str,
        model: str,
        request_digest: str,
        cache_digest: str,
        root_reference: str | None = None,
        variant_key: str | None = None,
    ) -> ClaimResult:
        """Atomically claim provider authority under the persisted active lease."""

        role = _identity(role, "role")
        logical_work_key = _generated_logical_key(logical_work_key)
        run_id = _identity(run_id, "run_id")
        owner = _owner_digest(owner_token)
        campaign_id = _identity(campaign_id, "campaign_id")
        schema_version = _identity(schema_version, "schema_version")
        manifest_digest = _digest(manifest_digest, "manifest_digest")
        config_digest = _digest(config_digest, "config_digest")
        template_digest = _digest(template_digest, "template_digest")
        provider = _identity(provider, "provider")
        model = _model_identity(model)
        request_digest = _digest(request_digest, "request_digest")
        cache_digest = _digest(cache_digest, "cache_digest")
        root_reference = _optional_identity(root_reference, "root_reference")
        variant_key = _optional_identity(variant_key, "variant_key")
        work_item_id = self._work_item_id(
            campaign_id, schema_version, run_id, logical_work_key
        )
        immutable = {
            "work_item_id": work_item_id,
            "logical_work_key": logical_work_key,
            "role": role,
            "root_reference": root_reference,
            "variant_key": variant_key,
            "run_id": run_id,
            "campaign_id": campaign_id,
            "schema_version": schema_version,
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "template_digest": template_digest,
            "provider": provider,
            "model": model,
            "request_digest": request_digest,
            "cache_digest": cache_digest,
        }

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = _clock_now(self._clock)
                contract = self._require_active_lease(
                    connection, run_id=run_id, owner=owner, now=now
                )
                contract_mismatches = self._work_contract_mismatches(
                    contract,
                    campaign_id=campaign_id,
                    schema_version=schema_version,
                    manifest_digest=manifest_digest,
                    config_digest=config_digest,
                )
                if contract_mismatches:
                    raise RunContractConflictError(
                        "Claim changed persisted run contract fields: "
                        + ", ".join(contract_mismatches)
                    )
                role_limits = json.loads(contract["role_limits_json"])
                if role not in role_limits:
                    raise ValueError(
                        f"No provider-attempt budget is configured for role {role!r}."
                    )
                existing = connection.execute(
                    "SELECT * FROM attempts WHERE run_id = ? AND logical_work_key = ?",
                    (run_id, logical_work_key),
                ).fetchone()
                if existing is not None:
                    mismatches = [
                        field
                        for field, value in immutable.items()
                        if existing[field] != value
                    ]
                    if mismatches:
                        raise AttemptConflictError(
                            "Logical work metadata changed for: "
                            + ", ".join(mismatches)
                        )
                    connection.commit()
                    return ClaimResult(
                        work_item_id=work_item_id,
                        logical_work_key=logical_work_key,
                        role=role,
                        status=AttemptStatus(existing["status"]),
                        claimed=False,
                    )
                total_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (run_id,)
                    ).fetchone()[0]
                )
                if total_count >= int(contract["total_limit"]):
                    raise AttemptLimitExceededError(
                        f"Run provider-attempt ceiling ({contract['total_limit']}) reached."
                    )
                role_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id = ? AND role = ?",
                        (run_id, role),
                    ).fetchone()[0]
                )
                if role_count >= int(role_limits[role]):
                    raise AttemptLimitExceededError(
                        f"Provider-attempt ceiling for role {role!r} "
                        f"({role_limits[role]}) reached."
                    )
                connection.execute(
                    """
                    INSERT INTO attempts (
                        work_item_id, logical_work_key, role, root_reference, variant_key,
                        run_id, campaign_id, schema_version, manifest_digest, config_digest,
                        template_digest, provider, model, request_digest, cache_digest,
                        status, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', ?)
                    """,
                    (
                        work_item_id,
                        logical_work_key,
                        role,
                        root_reference,
                        variant_key,
                        run_id,
                        campaign_id,
                        schema_version,
                        manifest_digest,
                        config_digest,
                        template_digest,
                        provider,
                        model,
                        request_digest,
                        cache_digest,
                        _timestamp(now),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return ClaimResult(
            work_item_id=work_item_id,
            logical_work_key=logical_work_key,
            role=role,
            status=AttemptStatus.STARTED,
            claimed=True,
        )

    def _terminal_transition(
        self,
        work_item_id: str,
        status: AttemptStatus,
        *,
        owner_token: str,
        terminal_code: str | None,
        response_digest: str | None,
        receipt_digest: str | None,
        output_artifact: ArtifactRef | None,
        artifact_verifier: Callable[[ArtifactRef], Any] | None,
    ) -> None:
        work_item_id = _identity(work_item_id, "work_item_id")
        owner = _owner_digest(owner_token)
        if status is AttemptStatus.COMPLETED:
            response_digest = _digest(response_digest or "", "response_digest")
            receipt_digest = _digest(receipt_digest or "", "receipt_digest")
            if terminal_code is not None:
                raise ValueError(
                    "Completed attempts cannot carry a terminal error code."
                )
            if output_artifact is None:
                raise ValueError(
                    "Completed attempts require an immutable output artifact."
                )
        else:
            if terminal_code is None:
                raise ValueError(
                    "Failed and indeterminate attempts require a terminal code."
                )
            terminal_code = _terminal_code(terminal_code)
            response_digest = _optional_digest(response_digest, "response_digest")
            receipt_digest = _optional_digest(receipt_digest, "receipt_digest")
        if output_artifact is not None:
            if artifact_verifier is None:
                raise ValueError(
                    "An artifact existence and digest verifier is required before terminal storage."
                )
            artifact_verifier(output_artifact)
        output_digest, output_relative_path, output_size_bytes = _artifact_fields(
            output_artifact
        )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT attempts.status, attempts.run_id,
                           run_contracts.lease_owner_digest,
                           run_contracts.lease_deadline
                    FROM attempts JOIN run_contracts USING (run_id)
                    WHERE attempts.work_item_id = ?
                    """,
                    (work_item_id,),
                ).fetchone()
                if row is None:
                    raise InvalidTerminalTransitionError(
                        "Unknown attempt work_item_id."
                    )
                if row["lease_owner_digest"] != owner:
                    raise RunLeaseConflictError(
                        "Attempt is owned by a different run lease."
                    )
                now = _clock_now(self._clock)
                if _parse_timestamp(row["lease_deadline"], "lease_deadline") <= now:
                    raise RunLeaseExpiredError(
                        "Run lease expired before the terminal result was recorded."
                    )
                if row["status"] != AttemptStatus.STARTED.value:
                    raise InvalidTerminalTransitionError(
                        f"Attempt is already terminal with status {row['status']!r}."
                    )
                updated = connection.execute(
                    """
                    UPDATE attempts
                    SET status = ?, terminal_code = ?, response_digest = ?,
                        receipt_digest = ?, output_digest = ?, output_relative_path = ?,
                        output_size_bytes = ?, terminal_at = ?
                    WHERE work_item_id = ? AND status = 'started'
                    """,
                    (
                        status.value,
                        terminal_code,
                        response_digest,
                        receipt_digest,
                        output_digest,
                        output_relative_path,
                        output_size_bytes,
                        _timestamp(now),
                        work_item_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise InvalidTerminalTransitionError(
                        "Attempt terminal transition lost a race."
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def complete(
        self,
        work_item_id: str,
        *,
        owner_token: str,
        response_digest: str,
        receipt_digest: str,
        output_artifact: ArtifactRef,
        artifact_verifier: Callable[[ArtifactRef], Any],
    ) -> None:
        self._terminal_transition(
            work_item_id,
            AttemptStatus.COMPLETED,
            owner_token=owner_token,
            terminal_code=None,
            response_digest=response_digest,
            receipt_digest=receipt_digest,
            output_artifact=output_artifact,
            artifact_verifier=artifact_verifier,
        )

    def fail(
        self,
        work_item_id: str,
        *,
        owner_token: str,
        terminal_code: str,
        response_digest: str | None = None,
        receipt_digest: str | None = None,
        output_artifact: ArtifactRef | None = None,
        artifact_verifier: Callable[[ArtifactRef], Any] | None = None,
    ) -> None:
        self._terminal_transition(
            work_item_id,
            AttemptStatus.FAILED,
            owner_token=owner_token,
            terminal_code=terminal_code,
            response_digest=response_digest,
            receipt_digest=receipt_digest,
            output_artifact=output_artifact,
            artifact_verifier=artifact_verifier,
        )

    def mark_indeterminate(
        self,
        work_item_id: str,
        *,
        owner_token: str,
        terminal_code: str,
        response_digest: str | None = None,
        receipt_digest: str | None = None,
        output_artifact: ArtifactRef | None = None,
        artifact_verifier: Callable[[ArtifactRef], Any] | None = None,
    ) -> None:
        self._terminal_transition(
            work_item_id,
            AttemptStatus.INDETERMINATE,
            owner_token=owner_token,
            terminal_code=terminal_code,
            response_digest=response_digest,
            receipt_digest=receipt_digest,
            output_artifact=output_artifact,
            artifact_verifier=artifact_verifier,
        )

    def reconcile_open_attempts(
        self,
        *,
        run_id: str,
        owner_token: str,
        terminal_code: str = "lease_expired",
    ) -> list[str]:
        """Seal open work only after the caller's own persisted lease expires."""

        run_id = _identity(run_id, "run_id")
        owner = _owner_digest(owner_token)
        terminal_code = _terminal_code(terminal_code)
        now = _clock_now(self._clock)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                contract = connection.execute(
                    "SELECT * FROM run_contracts WHERE run_id = ?", (run_id,)
                ).fetchone()
                if contract is None or contract["lease_owner_digest"] != owner:
                    raise RunLeaseConflictError(
                        "Run lease is not owned by this process."
                    )
                if _parse_timestamp(contract["lease_deadline"], "lease_deadline") > now:
                    raise RunLeaseConflictError(
                        "An active run lease cannot be reconciled."
                    )
                rows = connection.execute(
                    """
                    SELECT work_item_id FROM attempts
                    WHERE run_id = ? AND status = 'started'
                    ORDER BY started_at, work_item_id
                    """,
                    (run_id,),
                ).fetchall()
                work_item_ids = [str(row["work_item_id"]) for row in rows]
                if work_item_ids:
                    connection.execute(
                        """
                        UPDATE attempts
                        SET status = 'indeterminate', terminal_code = ?, terminal_at = ?
                        WHERE run_id = ? AND status = 'started'
                        """,
                        (terminal_code, _timestamp(now), run_id),
                    )
                connection.commit()
                return work_item_ids
            except Exception:
                connection.rollback()
                raise

    def _require_run(self, connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM run_contracts WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def get_total_attempts(self, run_id: str) -> int:
        run_id = _identity(run_id, "run_id")
        with self._connection() as connection:
            self._require_run(connection, run_id)
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )

    def get_role_attempts(self, run_id: str, role: str) -> int:
        run_id = _identity(run_id, "run_id")
        role = _identity(role, "role")
        with self._connection() as connection:
            self._require_run(connection, run_id)
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE run_id = ? AND role = ?",
                    (run_id, role),
                ).fetchone()[0]
            )

    def get_indeterminate_attempts(self, run_id: str) -> list[str]:
        run_id = _identity(run_id, "run_id")
        with self._connection() as connection:
            self._require_run(connection, run_id)
            rows = connection.execute(
                """
                SELECT work_item_id FROM attempts
                WHERE run_id = ? AND status IN ('started', 'indeterminate')
                ORDER BY started_at, work_item_id
                """,
                (run_id,),
            ).fetchall()
            return [str(row["work_item_id"]) for row in rows]

    def get_attempt(self, work_item_id: str) -> dict[str, Any]:
        work_item_id = _identity(work_item_id, "work_item_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE work_item_id = ?", (work_item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(work_item_id)
            return dict(row)

    def get_output_artifact(self, work_item_id: str) -> ArtifactRef:
        row = self.get_attempt(work_item_id)
        if (
            row["output_digest"] is None
            or row["output_relative_path"] is None
            or row["output_size_bytes"] is None
        ):
            raise KeyError(f"Attempt {work_item_id!r} has no output artifact.")
        return ArtifactRef(
            digest=str(row["output_digest"]).removeprefix("sha256:"),
            relative_path=str(row["output_relative_path"]),
            size_bytes=int(row["output_size_bytes"]),
        )

    def list_attempts(self, run_id: str) -> list[dict[str, Any]]:
        """Return a deterministic, report-safe snapshot of every run attempt."""

        run_id = _identity(run_id, "run_id")
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                self._require_run(connection, run_id)
                rows = connection.execute(
                    """
                    SELECT * FROM attempts WHERE run_id = ?
                    ORDER BY started_at, role, logical_work_key, work_item_id
                    """,
                    (run_id,),
                ).fetchall()
                connection.commit()
                return [dict(row) for row in rows]
            except Exception:
                connection.rollback()
                raise

    def snapshot_run(self, run_id: str) -> dict[str, Any]:
        run_id = _identity(run_id, "run_id")
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                contract = dict(self._require_run(connection, run_id))
                contract.pop("lease_owner_digest", None)
                contract["role_limits"] = json.loads(contract.pop("role_limits_json"))
                rows = connection.execute(
                    """
                    SELECT * FROM attempts WHERE run_id = ?
                    ORDER BY started_at, role, logical_work_key, work_item_id
                    """,
                    (run_id,),
                ).fetchall()
                attempts = [dict(row) for row in rows]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"contract": contract, "attempts": attempts}

    @staticmethod
    def _snapshot_digest(snapshot: dict[str, Any]) -> str:
        payload = json.dumps(
            snapshot,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def snapshot_digest(self, run_id: str) -> str:
        return self._snapshot_digest(self.snapshot_run(run_id))

    def summarize_run(self, run_id: str) -> dict[str, Any]:
        snapshot = self.snapshot_run(run_id)
        attempts = snapshot["attempts"]
        statuses = {status.value: 0 for status in AttemptStatus}
        roles = {role: 0 for role in sorted(snapshot["contract"]["role_limits"])}
        for attempt in attempts:
            statuses[str(attempt["status"])] += 1
            roles[str(attempt["role"])] = roles.get(str(attempt["role"]), 0) + 1
        return {
            "run_id": run_id,
            "total_attempts": len(attempts),
            "status_counts": statuses,
            "role_counts": roles,
            "snapshot_digest": self._snapshot_digest(snapshot),
        }

    # Compatibility spellings retain the strict lease and artifact contracts.
    log_started = claim
    log_completed = complete
    log_failed = fail

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> AttemptLedger:
        self._ensure_open()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
