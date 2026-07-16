"""Workspace evidence explain/sync/status must stay fenced to the current repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src import workspace_memory as wm
from src.utils import GrokSessionStore


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=False, text=True, capture_output=True
    )
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


@pytest.fixture
def memory_repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    monkeypatch.setattr(wm.PathResolver, "get_workspace_root", staticmethod(lambda: repo))
    monkeypatch.setenv("UNIGROK_SERVICE_MODE", "contributor")
    monkeypatch.setenv("UNIGROK_WORKSPACE_MEMORY", "mirror")
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    return repo


def _make_landed_commit(repo: Path, text: str) -> str:
    previous = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text(text + "\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", text)
    head = _git(repo, "rev-parse", "HEAD")
    receipts = repo / ".git" / "unigrok-land" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / f"{head}.json").write_text(
        json.dumps(
            {
                "head": head,
                "branch": "codex/test",
                "previous_main": previous,
                "changed_paths": ["README.md"],
                "tests": {"command": ["pytest"], "status": "passed"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return head


@pytest.mark.asyncio
async def test_explain_hides_foreign_repo_evidence(memory_repo, tmp_path):
    head = _make_landed_commit(memory_repo, "fence explain")
    store = GrokSessionStore(db_path=tmp_path / "fence-explain.db")
    try:
        recorded = await wm.record_landed_outcome(
            store,
            landed_sha=head,
            summary="Keep evidence scoped to its repository id.",
            kind="invariant",
        )
        foreign = dict(await store.get_workspace_evidence(recorded["evidence_id"]))
        foreign["evidence_id"] = "foreign-evidence-1"
        foreign["repo_id"] = "other-repo-id"
        foreign["content_hash"] = "a" * 64
        await store.save_workspace_evidence(foreign)

        with pytest.raises(wm.WorkspaceMemoryError, match="not found"):
            await wm.explain_workspace_evidence(
                store, evidence_id="foreign-evidence-1", head_sha=head
            )
        ok = await wm.explain_workspace_evidence(
            store, evidence_id=recorded["evidence_id"], head_sha=head
        )
        assert ok["evidence"]["evidence_id"] == recorded["evidence_id"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_status_and_sync_count_only_current_repo(memory_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_GIT_WRITE", "0")
    head = _make_landed_commit(memory_repo, "fence sync")
    store = GrokSessionStore(db_path=tmp_path / "fence-sync.db")
    try:
        recorded = await wm.record_landed_outcome(
            store,
            landed_sha=head,
            summary="Current-repo evidence should remain visible in status.",
            kind="observation",
        )
        foreign = dict(await store.get_workspace_evidence(recorded["evidence_id"]))
        foreign["evidence_id"] = "foreign-evidence-2"
        foreign["repo_id"] = "other-repo-id"
        foreign["content_hash"] = "b" * 64
        await store.save_workspace_evidence(foreign)

        status = await wm.workspace_memory_status(store)
        assert status["evidence_count"] == 1
        assert status["note_pending"] == 1

        pending = await store.list_unsynced_workspace_evidence(
            limit=10, repo_id=status["repo_id"]
        )
        assert [row["evidence_id"] for row in pending] == [recorded["evidence_id"]]

        summary = await wm.sync_pending_notes(store, limit=10)
        assert summary["reason"] == "git_write_disabled"
        assert summary["pending"] == 1
        assert await store.count_unsynced_workspace_evidence() == 2
    finally:
        await store.close()
