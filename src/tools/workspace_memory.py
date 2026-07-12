"""MCP tools for verified, commit-anchored workspace evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..identity import caller_from_mcp_context
from ..utils import register_internal_tool, store
from ..workspace_memory import (
    WorkspaceMemoryError,
    explain_workspace_evidence as _explain,
    recall_workspace_memory as _recall,
    record_landed_outcome as _record,
    import_git_notes,
    sync_pending_notes,
    workspace_memory_status as _status,
)

READONLY_TOOL = ToolAnnotations(readOnlyHint=True)


def _error(exc: Exception) -> Dict[str, Any]:
    return {"error": f"Workspace Memory Error: {exc}"}


async def recall_workspace_memory(
    query: str,
    head_sha: str,
    changed_paths: Optional[List[str]] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """Recall engineering evidence relevant to one specific local checkout.

    The caller must supply its own full Git HEAD because the shared Docker MCP
    sees the main checkout and cannot infer an IDE's hidden task worktree.

    Args:
        query: Current engineering task or question.
        head_sha: Full 40-character commit id checked out by the calling agent.
        changed_paths: Repository-relative paths currently in scope.
        limit: Maximum evidence cards to return (1-10, default 3).
    """
    try:
        return await _recall(
            store,
            query=query,
            head_sha=head_sha,
            changed_paths=changed_paths,
            limit=limit,
        )
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


async def record_landed_outcome(
    landed_sha: str,
    summary: str,
    kind: str = "decision",
    paths: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    confidence: float = 0.8,
    supersedes: Optional[List[str]] = None,
    task_memory_ids: Optional[List[int]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Record one engineering outcome after ``scripts/land`` succeeds.

    The server verifies that ``landed_sha`` is reachable from local main and
    has a matching passing landing receipt. SQLite commits first; compact Git
    Notes mirroring is best-effort through a durable outbox.

    Args:
        landed_sha: Full commit id printed by ``scripts/land``.
        summary: Concise decision, constraint, failure lesson, or workaround.
        kind: decision, invariant, workaround, failure, observation, or routing.
        paths: Repository-relative files affected; defaults to receipt paths.
        symbols: Optional functions/classes/config keys in scope.
        confidence: Evidence confidence from 0.0 to 1.0.
        supersedes: Older evidence ids explicitly invalidated by this outcome.
        task_memory_ids: Optional routing task-memory rows linked as provenance.
    """
    try:
        caller = caller_from_mcp_context(ctx) if ctx is not None else None
        return await _record(
            store,
            landed_sha=landed_sha,
            summary=summary,
            kind=kind,
            paths=paths,
            symbols=symbols,
            confidence=confidence,
            supersedes=supersedes,
            task_memory_ids=task_memory_ids,
            source_caller=caller,
        )
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


async def explain_workspace_evidence(
    evidence_id: str, head_sha: str
) -> Dict[str, Any]:
    """Explain provenance and current-head applicability for one evidence card."""
    try:
        return await _explain(store, evidence_id=evidence_id, head_sha=head_sha)
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


async def workspace_memory_status() -> Dict[str, Any]:
    """Show mode, local evidence counts, Git Notes outbox, and note-ref state."""
    try:
        return await _status(store)
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


async def sync_workspace_memory_notes(limit: int = 20) -> Dict[str, Any]:
    """Retry pending compact Git Notes mirrors when local Git writes are enabled."""
    try:
        return await sync_pending_notes(store, limit=max(1, min(int(limit or 20), 100)))
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


async def import_workspace_memory_notes(limit: int = 200) -> Dict[str, Any]:
    """Recover SQLite evidence from verified compact envelopes in Git Notes."""
    try:
        return await import_git_notes(store, limit=max(1, min(int(limit or 200), 1000)))
    except (WorkspaceMemoryError, ValueError, OSError) as exc:
        return _error(exc)


def register_workspace_memory_tools(mcp: FastMCP) -> None:
    mcp.add_tool(recall_workspace_memory, annotations=READONLY_TOOL)
    mcp.add_tool(record_landed_outcome)
    mcp.add_tool(explain_workspace_evidence, annotations=READONLY_TOOL)
    mcp.add_tool(workspace_memory_status, annotations=READONLY_TOOL)
    mcp.add_tool(sync_workspace_memory_notes)
    mcp.add_tool(import_workspace_memory_notes)


register_internal_tool("recall_workspace_memory", recall_workspace_memory)
register_internal_tool("record_landed_outcome", record_landed_outcome)
register_internal_tool("explain_workspace_evidence", explain_workspace_evidence)
register_internal_tool("workspace_memory_status", workspace_memory_status)
register_internal_tool("sync_workspace_memory_notes", sync_workspace_memory_notes)
register_internal_tool("import_workspace_memory_notes", import_workspace_memory_notes)
