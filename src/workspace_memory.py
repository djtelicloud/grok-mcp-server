"""Commit-anchored workspace evidence with a compact local Git Notes mirror.

SQLite is authoritative. Git Notes carry small provenance envelopes attached
only to commits that have a verified ``scripts/land`` receipt. Retrieval is
explicit in this first rollout: no automatic prompt injection occurs here.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, TextIO

from .utils import (
    PathResolver,
    _bounded_redacted,
    _task_terms,
    get_active_caller,
    get_unigrok_runtime,
    run_blocking,
)

logger = logging.getLogger("GrokMCP")

NOTE_REF = "refs/notes/unigrok-local"
VALID_MODES = ("off", "mirror", "shadow", "active")
VALID_KINDS = ("decision", "invariant", "workaround", "failure", "observation", "routing")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class WorkspaceMemoryError(RuntimeError):
    pass


def workspace_memory_mode() -> str:
    raw = os.environ.get("UNIGROK_WORKSPACE_MEMORY", "mirror").strip().lower() or "mirror"
    return raw if raw in VALID_MODES else "off"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        text=True,
        capture_output=True,
        timeout=15,
    )
    if check and result.returncode:
        raise WorkspaceMemoryError((result.stderr or result.stdout or "git command failed").strip())
    return result.stdout.strip()


def _repo() -> Path:
    return PathResolver.get_project_root().resolve()


def _common_git_dir(repo: Path) -> Path:
    raw = Path(_git(repo, "rev-parse", "--git-common-dir"))
    return raw if raw.is_absolute() else (repo / raw).resolve()


def repository_id(repo: Optional[Path] = None) -> str:
    root = repo or _repo()
    roots = sorted(_git(root, "rev-list", "--max-parents=0", "HEAD").splitlines())
    if not roots:
        raise WorkspaceMemoryError("repository has no root commit")
    return hashlib.sha256("\n".join(roots).encode()).hexdigest()[:24]


def _validate_sha(repo: Path, value: str, *, require_landed: bool = False) -> str:
    sha = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise WorkspaceMemoryError("commit SHA must be a full 40-character lowercase hex id")
    _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if require_landed:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "refs/heads/main"],
            cwd=repo,
            check=False,
            capture_output=True,
            timeout=15,
        )
        if result.returncode:
            raise WorkspaceMemoryError("commit is not reachable from visible local main")
    return sha


def _normalize_paths(values: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    for raw in values or []:
        value = str(raw or "").replace("\\", "/").strip()
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise WorkspaceMemoryError(f"invalid repository-relative path: {raw!r}")
        clean = str(path)[:300]
        if clean not in normalized:
            normalized.append(clean)
    if len(normalized) > 32:
        raise WorkspaceMemoryError("workspace evidence accepts at most 32 paths")
    return normalized


def _normalize_short_list(values: Optional[List[Any]], *, label: str, limit: int = 32) -> List[str]:
    normalized: List[str] = []
    for raw in values or []:
        value = _bounded_redacted(str(raw or ""), 160).replace("\n", " ").strip()
        if value and value not in normalized:
            normalized.append(value)
    if len(normalized) > limit:
        raise WorkspaceMemoryError(f"workspace evidence accepts at most {limit} {label}")
    return normalized


def verified_landing_receipt(repo: Path, landed_sha: str) -> tuple[Dict[str, Any], str]:
    sha = _validate_sha(repo, landed_sha, require_landed=True)
    receipt_path = _common_git_dir(repo) / "unigrok-land" / "receipts" / f"{sha}.json"
    try:
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise WorkspaceMemoryError(f"verified landing receipt unavailable for {sha}: {exc}") from exc
    if receipt.get("head") != sha:
        raise WorkspaceMemoryError("landing receipt head does not match the requested commit")
    tests = receipt.get("tests")
    if tests and tests.get("status") != "passed":
        raise WorkspaceMemoryError("landing receipt does not certify a passing test run")
    return receipt, hashlib.sha256(raw).hexdigest()


def _note_write_enabled() -> bool:
    return (
        workspace_memory_mode() != "off"
        and get_unigrok_runtime() != "cloudrun"
        and os.environ.get("ENABLE_GIT_WRITE") == "1"
    )


def _note_envelope(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidence_id": row["evidence_id"],
        "kind": row["kind"],
        "landed_sha": row["landed_sha"],
        "previous_main": row.get("previous_main") or "",
        "summary": row["summary"],
        "paths": row.get("paths") or [],
        "symbols": row.get("symbols") or [],
        "tests": row.get("tests") or {},
        "confidence": row.get("confidence"),
        "supersedes": row.get("supersedes") or [],
        "task_memory_ids": row.get("task_memory_ids") or [],
        "source_caller": row.get("source_caller") or "unknown",
        "receipt_hash": row["receipt_hash"],
        "content_hash": row["content_hash"],
        "created_at": row.get("created_at"),
    }


def _write_git_note(repo: Path, row: Dict[str, Any]) -> None:
    if not _note_write_enabled():
        raise WorkspaceMemoryError(
            "Git Notes mirror requires ENABLE_GIT_WRITE=1 outside Cloud Run"
        )
    lock_path = _common_git_dir(repo) / "unigrok-memory.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        existing_raw = _git(
            repo, "notes", f"--ref={NOTE_REF}", "show", row["landed_sha"], check=False
        )
        if existing_raw:
            try:
                document = json.loads(existing_raw)
            except (TypeError, ValueError) as exc:
                raise WorkspaceMemoryError("existing UniGrok note is corrupt; refusing overwrite") from exc
        else:
            document = {"schema_version": 1, "entries": []}
        if document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
            raise WorkspaceMemoryError("existing UniGrok note has an unsupported schema")
        entries = [
            entry for entry in document["entries"]
            if isinstance(entry, dict) and entry.get("evidence_id") != row["evidence_id"]
        ]
        entries.append(_note_envelope(row))
        document["entries"] = sorted(entries, key=lambda item: str(item.get("evidence_id") or ""))
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 64 * 1024:
            raise WorkspaceMemoryError("Git Note would exceed the 64KB safety limit")
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "UniGrok Workspace Memory",
                "GIT_AUTHOR_EMAIL": "unigrok@local.invalid",
                "GIT_COMMITTER_NAME": "UniGrok Workspace Memory",
                "GIT_COMMITTER_EMAIL": "unigrok@local.invalid",
            }
        )
        result = subprocess.run(
            [
                "git", "notes", f"--ref={NOTE_REF}", "add", "-f", "-m", payload,
                row["landed_sha"],
            ],
            cwd=repo,
            check=False,
            text=True,
            capture_output=True,
            timeout=15,
            env=env,
        )
        if result.returncode:
            raise WorkspaceMemoryError((result.stderr or "git notes write failed").strip())


async def sync_pending_notes(store: Any, *, limit: int = 20) -> Dict[str, Any]:
    if not _note_write_enabled():
        return {
            "synced": 0,
            "failed": 0,
            "pending": await store.count_unsynced_workspace_evidence(),
            "reason": "git_write_disabled",
        }
    repo = _repo()
    rows = await store.list_unsynced_workspace_evidence(limit=limit, max_attempts=5)
    synced = 0
    failed = 0
    for row in rows:
        try:
            await run_blocking(_write_git_note, repo, row, timeout=20.0)
            await store.mark_workspace_evidence_synced(row["evidence_id"], NOTE_REF)
            synced += 1
        except Exception as exc:
            failed += 1
            await store.mark_workspace_evidence_sync_failed(row["evidence_id"], str(exc))
            logger.warning("Workspace evidence Git Notes mirror failed: %s", exc)
    return {
        "synced": synced,
        "failed": failed,
        "pending": await store.count_unsynced_workspace_evidence(),
    }


async def import_git_notes(store: Any, *, limit: int = 200) -> Dict[str, Any]:
    """Recover verified evidence envelopes from the local notes ref.

    Import never trusts a note alone: the annotated commit must still have the
    exact landing receipt hash recorded in the envelope.
    """
    if workspace_memory_mode() == "off":
        raise WorkspaceMemoryError("workspace memory is disabled")
    repo = _repo()
    listing = _git(repo, "notes", f"--ref={NOTE_REF}", "list", check=False)
    imported = 0
    skipped = 0
    errors: List[str] = []
    repo_id = repository_id(repo)
    for line in listing.splitlines()[: max(1, min(int(limit or 200), 1000))]:
        parts = line.split()
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[1]):
            skipped += 1
            continue
        landed_sha = parts[1]
        raw = _git(repo, "notes", f"--ref={NOTE_REF}", "show", landed_sha, check=False)
        try:
            document = json.loads(raw)
        except (TypeError, ValueError):
            skipped += 1
            errors.append(f"{landed_sha[:12]}: corrupt note")
            continue
        if document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
            skipped += 1
            errors.append(f"{landed_sha[:12]}: unsupported schema")
            continue
        try:
            receipt, receipt_hash = verified_landing_receipt(repo, landed_sha)
        except WorkspaceMemoryError as exc:
            skipped += len(document["entries"])
            errors.append(f"{landed_sha[:12]}: {exc}")
            continue
        for entry in document["entries"]:
            try:
                if not isinstance(entry, dict) or entry.get("landed_sha") != landed_sha:
                    raise WorkspaceMemoryError("entry commit mismatch")
                if entry.get("receipt_hash") != receipt_hash:
                    raise WorkspaceMemoryError("landing receipt hash mismatch")
                evidence_id = str(entry.get("evidence_id") or "")
                content_hash = str(entry.get("content_hash") or "")
                if not ID_RE.fullmatch(evidence_id) or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                    raise WorkspaceMemoryError("invalid evidence identity")
                payload = {
                    "evidence_id": evidence_id,
                    "repo_id": repo_id,
                    "landed_sha": landed_sha,
                    "previous_main": entry.get("previous_main") or receipt.get("previous_main") or "",
                    "kind": entry.get("kind") or "decision",
                    "summary": entry.get("summary") or "",
                    "paths": _normalize_paths(entry.get("paths")),
                    "symbols": _normalize_short_list(entry.get("symbols"), label="symbols"),
                    "tests": entry.get("tests") or receipt.get("tests") or {},
                    "confidence": entry.get("confidence", 0.8),
                    "supersedes": _normalize_short_list(entry.get("supersedes"), label="supersedes"),
                    "task_memory_ids": entry.get("task_memory_ids") or [],
                    "source_caller": entry.get("source_caller") or "notes-import",
                    "receipt_hash": receipt_hash,
                    "content_hash": content_hash,
                }
                await store.save_workspace_evidence(payload)
                await store.mark_workspace_evidence_synced(evidence_id, NOTE_REF)
                imported += 1
            except Exception as exc:
                skipped += 1
                errors.append(f"{landed_sha[:12]}: {_bounded_redacted(str(exc), 160)}")
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


async def record_landed_outcome(
    store: Any,
    *,
    landed_sha: str,
    summary: str,
    kind: str = "decision",
    paths: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    confidence: float = 0.8,
    supersedes: Optional[List[str]] = None,
    task_memory_ids: Optional[List[int]] = None,
    source_caller: Optional[str] = None,
) -> Dict[str, Any]:
    if workspace_memory_mode() == "off":
        raise WorkspaceMemoryError("workspace memory is disabled")
    repo = _repo()
    sha = _validate_sha(repo, landed_sha, require_landed=True)
    receipt, receipt_hash = verified_landing_receipt(repo, sha)
    text = _bounded_redacted(str(summary or ""), 2000)
    if not text:
        raise WorkspaceMemoryError("summary must not be empty")
    kind_value = str(kind or "decision").strip().lower()
    if kind_value not in VALID_KINDS:
        raise WorkspaceMemoryError(f"kind must be one of: {', '.join(VALID_KINDS)}")
    path_values = _normalize_paths(paths if paths is not None else receipt.get("changed_paths"))
    symbol_values = _normalize_short_list(symbols, label="symbols")
    supersedes_values = _normalize_short_list(supersedes, label="supersedes")
    if any(not ID_RE.fullmatch(value) for value in supersedes_values):
        raise WorkspaceMemoryError("supersedes contains an invalid evidence id")
    memory_ids = sorted({int(value) for value in task_memory_ids or [] if int(value) > 0})[:32]
    confidence_value = max(0.0, min(float(confidence), 1.0))
    canonical = json.dumps(
        {
            "kind": kind_value,
            "summary": text,
            "paths": path_values,
            "symbols": symbol_values,
            "supersedes": supersedes_values,
            "task_memory_ids": memory_ids,
            "confidence": confidence_value,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    evidence_id = f"ev-{content_hash[:24]}"
    payload = {
        "evidence_id": evidence_id,
        "repo_id": repository_id(repo),
        "landed_sha": sha,
        "previous_main": receipt.get("previous_main") or "",
        "kind": kind_value,
        "summary": text,
        "paths": path_values,
        "symbols": symbol_values,
        "tests": receipt.get("tests") or {"command": receipt.get("test_command"), "status": "passed"},
        "confidence": confidence_value,
        "supersedes": supersedes_values,
        "task_memory_ids": memory_ids,
        "source_caller": source_caller or get_active_caller() or "unknown",
        "receipt_hash": receipt_hash,
        "content_hash": content_hash,
    }
    row_id = await store.save_workspace_evidence(payload)
    sync = await sync_pending_notes(store, limit=20)
    row = await store.get_workspace_evidence(evidence_id)
    return {
        "evidence_id": evidence_id,
        "row_id": row_id,
        "landed_sha": sha,
        "note_synced": bool(row and row.get("note_synced_at")),
        "sync": sync,
    }


def _parse_datetime(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _freshness(kind: str, created_at: Any) -> float:
    half_lives = {
        "routing": 14.0,
        "observation": 30.0,
        "workaround": 60.0,
        "failure": 90.0,
        "decision": 365.0,
        "invariant": None,
    }
    half_life = half_lives.get(kind, 180.0)
    if half_life is None:
        return 1.0
    created = _parse_datetime(created_at)
    if created is None:
        return 0.75
    age_days = max(0.0, (datetime.now() - created).total_seconds() / 86400.0)
    return 2.0 ** (-age_days / half_life)


def _git_applicability(repo: Path, row: Dict[str, Any], head: str) -> Optional[Dict[str, Any]]:
    landed = row["landed_sha"]
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", landed, head],
        cwd=repo,
        check=False,
        capture_output=True,
        timeout=15,
    )
    if ancestor.returncode:
        return None
    distance = int(_git(repo, "rev-list", "--count", f"{landed}..{head}") or "0")
    paths = row.get("paths") or []
    changed: List[str] = []
    missing: List[str] = []
    if paths:
        changed = [
            line for line in _git(repo, "diff", "--name-only", f"{landed}..{head}", "--", *paths).splitlines()
            if line
        ]
        for path in paths:
            probe = subprocess.run(
                ["git", "cat-file", "-e", f"{head}:{path}"],
                cwd=repo,
                check=False,
                capture_output=True,
                timeout=10,
            )
            if probe.returncode:
                missing.append(path)
    scope_stability = 1.0
    if changed:
        scope_stability *= 0.75
    if missing:
        scope_stability *= 0.4
    return {
        "distance": distance,
        "topology": 0.98 ** min(distance, 50),
        "scope_stability": scope_stability,
        "changed_since": changed,
        "missing_at_head": missing,
    }


def _is_ancestor(repo: Path, candidate: str, head: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", candidate, head],
        cwd=repo,
        check=False,
        capture_output=True,
        timeout=15,
    )
    return result.returncode == 0


async def recall_workspace_memory(
    store: Any,
    *,
    query: str,
    head_sha: str,
    changed_paths: Optional[List[str]] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    if workspace_memory_mode() == "off":
        raise WorkspaceMemoryError("workspace memory is disabled")
    text = _bounded_redacted(str(query or ""), 1000)
    if not text:
        raise WorkspaceMemoryError("query must not be empty")
    repo = _repo()
    head = _validate_sha(repo, head_sha)
    repo_id = repository_id(repo)
    rows = await store.list_workspace_evidence(repo_id, limit=500)
    # Supersession is itself commit-scoped. A later decision must not erase
    # the historically-correct evidence seen from an older task worktree.
    invalidated = set()
    for row in rows:
        supersedes = row.get("supersedes") or []
        if supersedes and await run_blocking(
            _is_ancestor, repo, row["landed_sha"], head, timeout=20.0
        ):
            invalidated.update(supersedes)
    query_terms = set(_task_terms(text))
    current_paths = set(_normalize_paths(changed_paths))
    candidates: List[tuple[float, Dict[str, Any], float, float]] = []
    for row in rows:
        if row["evidence_id"] in invalidated:
            continue
        row_terms = set(str(row.get("terms") or "").split())
        overlap = len(query_terms & row_terms) / max(len(query_terms), 1)
        path_overlap = len(current_paths & set(row.get("paths") or []))
        if overlap <= 0 and path_overlap <= 0:
            continue
        candidates.append((overlap, row, 1.25 if path_overlap else 1.0, float(path_overlap)))
    candidates.sort(key=lambda item: (item[0], item[3], int(item[1]["id"])), reverse=True)
    results: List[Dict[str, Any]] = []
    for relevance, row, scope_overlap, _ in candidates[:50]:
        applicability = await run_blocking(_git_applicability, repo, row, head, timeout=20.0)
        if applicability is None:
            continue
        confidence = max(0.0, min(float(row.get("confidence") or 0.0), 1.0))
        freshness = _freshness(str(row.get("kind") or "decision"), row.get("created_at"))
        score = (
            max(relevance, 0.2 if scope_overlap > 1.0 else 0.0)
            * confidence
            * scope_overlap
            * applicability["scope_stability"]
            * freshness
            * applicability["topology"]
        )
        results.append(
            {
                "evidence_id": row["evidence_id"],
                "kind": row["kind"],
                "summary": row["summary"],
                "paths": row.get("paths") or [],
                "symbols": row.get("symbols") or [],
                "landed_sha": row["landed_sha"],
                "citation": f"commit:{row['landed_sha']}",
                "score": round(score, 6),
                "score_components": {
                    "relevance": round(relevance, 6),
                    "confidence": confidence,
                    "scope_overlap": scope_overlap,
                    "scope_stability": applicability["scope_stability"],
                    "freshness": round(freshness, 6),
                    "topology": round(applicability["topology"], 6),
                },
                "distance": applicability["distance"],
                "changed_since": applicability["changed_since"],
                "missing_at_head": applicability["missing_at_head"],
            }
        )
    results.sort(key=lambda item: (item["score"], item["evidence_id"]), reverse=True)
    bounded = max(1, min(int(limit or 3), 10))
    return {
        "head_sha": head,
        "repo_id": repo_id,
        "mode": workspace_memory_mode(),
        "automatic_injection": False,
        "evidence": results[:bounded],
        "count": min(len(results), bounded),
    }


async def explain_workspace_evidence(
    store: Any, *, evidence_id: str, head_sha: str
) -> Dict[str, Any]:
    if workspace_memory_mode() == "off":
        raise WorkspaceMemoryError("workspace memory is disabled")
    target = str(evidence_id or "").strip()
    if not ID_RE.fullmatch(target):
        raise WorkspaceMemoryError("invalid evidence id")
    row = await store.get_workspace_evidence(target)
    if row is None:
        raise WorkspaceMemoryError("workspace evidence not found")
    repo = _repo()
    head = _validate_sha(repo, head_sha)
    applicability = await run_blocking(_git_applicability, repo, row, head, timeout=20.0)
    return {
        "evidence": _note_envelope(row),
        "reachable_from_head": applicability is not None,
        "applicability": applicability,
        "note_synced": bool(row.get("note_synced_at")),
        "note_ref": row.get("note_ref"),
        "sync_error": row.get("sync_error"),
    }


async def workspace_memory_status(store: Any) -> Dict[str, Any]:
    repo = _repo()
    head = _git(repo, "rev-parse", "HEAD")
    note_head = _git(repo, "rev-parse", NOTE_REF, check=False)
    return {
        "mode": workspace_memory_mode(),
        "automatic_injection": False,
        "repo_id": repository_id(repo),
        "visible_head": head,
        "evidence_count": await store.count_workspace_evidence(),
        "note_pending": await store.count_unsynced_workspace_evidence(),
        "note_ref": NOTE_REF,
        "note_ref_head": note_head or None,
        "git_write_enabled": _note_write_enabled(),
    }


_MEMORY_USAGE = """usage: unigrok-mcp memory <subcommand>

subcommands:
  status              mode, evidence count, note outbox, and local note ref
  sync [--limit N]    retry pending compact Git Notes mirrors
  import [--limit N]  recover verified envelopes from the local notes ref
"""


def workspace_memory_cli(
    args: List[str], stream: Optional[TextIO] = None, store: Any = None
) -> int:
    out = stream or sys.stdout
    args = list(args or [])
    command = args[0] if args else ""
    if command not in ("status", "sync", "import"):
        print(_MEMORY_USAGE, file=out)
        return 2
    if store is None:
        from .utils import store as shared_store

        store = shared_store

    async def _run() -> int:
        try:
            if command == "status":
                result = await workspace_memory_status(store)
            else:
                limit = 20
                if "--limit" in args:
                    try:
                        limit = max(1, min(int(args[args.index("--limit") + 1]), 100))
                    except (IndexError, ValueError):
                        print("--limit needs a positive integer", file=out)
                        return 2
                result = (
                    await import_git_notes(store, limit=limit)
                    if command == "import"
                    else await sync_pending_notes(store, limit=limit)
                )
            print(json.dumps(result, indent=2, sort_keys=True), file=out)
            return 0
        finally:
            with contextlib.suppress(Exception):
                await store.close()

    return asyncio.run(_run())
