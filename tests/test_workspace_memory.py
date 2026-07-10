import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.utils import GrokSessionStore
from src import workspace_memory as wm


def git(repo: Path, *args: str, check: bool = True) -> str:
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
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    monkeypatch.setattr(wm.PathResolver, "get_project_root", staticmethod(lambda: repo))
    monkeypatch.setenv("UNIGROK_WORKSPACE_MEMORY", "mirror")
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    return repo


def make_landed_commit(repo: Path, text: str, *, path: str = "README.md") -> tuple[str, str]:
    previous = git(repo, "rev-parse", "HEAD")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", text)
    head = git(repo, "rev-parse", "HEAD")
    receipts = repo / ".git" / "unigrok-land" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / f"{head}.json").write_text(
        json.dumps(
            {
                "head": head,
                "branch": "codex/test",
                "previous_main": previous,
                "changed_paths": [path],
                "tests": {"command": ["pytest"], "status": "passed"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return previous, head


@pytest.mark.asyncio
async def test_record_is_deduplicated_and_mirrored_to_git_note(memory_repo, tmp_path):
    _, head = make_landed_commit(memory_repo, "serialize agent landings")
    store = GrokSessionStore(db_path=tmp_path / "evidence.db")
    try:
        first = await wm.record_landed_outcome(
            store,
            landed_sha=head,
            summary="Serialize verified agent landings before updating visible main.",
            kind="invariant",
            symbols=["directory_lock"],
            confidence=0.95,
            source_caller="codex",
        )
        second = await wm.record_landed_outcome(
            store,
            landed_sha=head,
            summary="Serialize verified agent landings before updating visible main.",
            kind="invariant",
            symbols=["directory_lock"],
            confidence=0.95,
            source_caller="codex",
        )

        assert first["evidence_id"] == second["evidence_id"]
        assert await store.count_workspace_evidence() == 1
        assert await store.count_unsynced_workspace_evidence() == 0
        raw = git(memory_repo, "notes", f"--ref={wm.NOTE_REF}", "show", head)
        note = json.loads(raw)
        assert note["schema_version"] == 1
        assert len(note["entries"]) == 1
        entry = note["entries"][0]
        assert entry["evidence_id"] == first["evidence_id"]
        assert entry["paths"] == ["README.md"]
        assert "vector" not in entry and "prompt" not in entry
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_record_requires_verified_passing_landing_receipt(memory_repo, tmp_path):
    (memory_repo / "unlanded.txt").write_text("draft\n", encoding="utf-8")
    git(memory_repo, "add", "unlanded.txt")
    git(memory_repo, "commit", "-m", "unlanded")
    head = git(memory_repo, "rev-parse", "HEAD")
    store = GrokSessionStore(db_path=tmp_path / "missing.db")
    try:
        with pytest.raises(wm.WorkspaceMemoryError, match="receipt unavailable"):
            await wm.record_landed_outcome(store, landed_sha=head, summary="must not save")

        receipts = memory_repo / ".git" / "unigrok-land" / "receipts"
        receipts.mkdir(parents=True, exist_ok=True)
        (receipts / f"{head}.json").write_text(
            json.dumps({"head": head, "tests": {"status": "failed"}}), encoding="utf-8"
        )
        with pytest.raises(wm.WorkspaceMemoryError, match="passing test"):
            await wm.record_landed_outcome(store, landed_sha=head, summary="must not save")
        assert await store.count_workspace_evidence() == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recall_is_ancestry_scope_and_supersession_aware(memory_repo, tmp_path):
    _, first_head = make_landed_commit(memory_repo, "first cache decision")
    store = GrokSessionStore(db_path=tmp_path / "recall.db")
    try:
        first = await wm.record_landed_outcome(
            store,
            landed_sha=first_head,
            summary="Cache policy uses a single process-local lock.",
            kind="decision",
            paths=["README.md"],
        )
        git(memory_repo, "branch", "old-worktree", first_head)
        _, second_head = make_landed_commit(memory_repo, "replace cache policy")
        second = await wm.record_landed_outcome(
            store,
            landed_sha=second_head,
            summary="Cache policy now uses a repository-wide file lock.",
            kind="decision",
            paths=["README.md"],
            supersedes=[first["evidence_id"]],
        )

        current = await wm.recall_workspace_memory(
            store,
            query="what cache lock policy applies",
            head_sha=second_head,
            changed_paths=["README.md"],
        )
        assert [item["evidence_id"] for item in current["evidence"]] == [second["evidence_id"]]
        assert current["evidence"][0]["score_components"]["scope_overlap"] == 1.25

        # Supersession is also commit-scoped: an older worktree still sees
        # the decision that was valid at its own HEAD.
        old = await wm.recall_workspace_memory(
            store,
            query="cache lock policy",
            head_sha=first_head,
            changed_paths=["README.md"],
        )
        assert [item["evidence_id"] for item in old["evidence"]] == [first["evidence_id"]]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recall_reports_changed_and_deleted_scope(memory_repo, tmp_path):
    _, evidence_head = make_landed_commit(memory_repo, "path-scoped decision")
    store = GrokSessionStore(db_path=tmp_path / "scope.db")
    try:
        recorded = await wm.record_landed_outcome(
            store,
            landed_sha=evidence_head,
            summary="README documents the required landing workflow.",
            paths=["README.md"],
        )
        git(memory_repo, "rm", "README.md")
        git(memory_repo, "commit", "-m", "remove readme")
        head = git(memory_repo, "rev-parse", "HEAD")

        recalled = await wm.recall_workspace_memory(
            store,
            query="required landing workflow readme",
            head_sha=head,
            changed_paths=["README.md"],
        )
        item = recalled["evidence"][0]
        assert item["evidence_id"] == recorded["evidence_id"]
        assert item["changed_since"] == ["README.md"]
        assert item["missing_at_head"] == ["README.md"]
        assert item["score_components"]["scope_stability"] == pytest.approx(0.3)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_git_write_disabled_leaves_retryable_sqlite_outbox(memory_repo, tmp_path, monkeypatch):
    _, head = make_landed_commit(memory_repo, "outbox decision")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "0")
    store = GrokSessionStore(db_path=tmp_path / "outbox.db")
    try:
        result = await wm.record_landed_outcome(
            store, landed_sha=head, summary="Git Notes are a retryable mirror."
        )
        assert result["note_synced"] is False
        assert result["sync"]["reason"] == "git_write_disabled"
        assert await store.count_workspace_evidence() == 1
        assert await store.count_unsynced_workspace_evidence() == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_verified_git_note_can_recover_an_empty_sqlite_store(memory_repo, tmp_path):
    _, head = make_landed_commit(memory_repo, "recoverable decision")
    original = GrokSessionStore(db_path=tmp_path / "original.db")
    try:
        recorded = await wm.record_landed_outcome(
            original,
            landed_sha=head,
            summary="Git Notes are compact verified recovery provenance.",
            kind="invariant",
        )
    finally:
        await original.close()

    recovered = GrokSessionStore(db_path=tmp_path / "recovered.db")
    try:
        result = await wm.import_git_notes(recovered)
        assert result == {"imported": 1, "skipped": 0, "errors": []}
        row = await recovered.get_workspace_evidence(recorded["evidence_id"])
        assert row["summary"] == "Git Notes are compact verified recovery provenance."
        assert row["note_ref"] == wm.NOTE_REF
        assert await recovered.count_unsynced_workspace_evidence() == 0
    finally:
        await recovered.close()


@pytest.mark.asyncio
async def test_corrupt_existing_note_is_never_overwritten(memory_repo, tmp_path):
    _, head = make_landed_commit(memory_repo, "corrupt note decision")
    git(memory_repo, "notes", f"--ref={wm.NOTE_REF}", "add", "-m", "not-json", head)
    store = GrokSessionStore(db_path=tmp_path / "corrupt.db")
    try:
        result = await wm.record_landed_outcome(
            store, landed_sha=head, summary="Do not overwrite corrupt note data."
        )
        assert result["note_synced"] is False
        assert result["sync"]["failed"] == 1
        assert git(memory_repo, "notes", f"--ref={wm.NOTE_REF}", "show", head) == "not-json"
        row = await store.get_workspace_evidence(result["evidence_id"])
        assert "corrupt" in row["sync_error"]
    finally:
        await store.close()


def test_concurrent_note_writers_preserve_every_entry(memory_repo):
    _, head = make_landed_commit(memory_repo, "concurrent note target")

    def row(index):
        return {
            "evidence_id": f"ev-concurrent-{index}",
            "kind": "observation",
            "landed_sha": head,
            "previous_main": "",
            "summary": f"concurrent evidence {index}",
            "paths": ["README.md"],
            "symbols": [],
            "tests": {"status": "passed"},
            "confidence": 0.8,
            "supersedes": [],
            "task_memory_ids": [],
            "source_caller": "test",
            "receipt_hash": f"receipt-{index}",
            "content_hash": f"content-{index}",
            "created_at": "2026-07-10T00:00:00",
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: wm._write_git_note(memory_repo, row(index)), range(8)))

    note = json.loads(git(memory_repo, "notes", f"--ref={wm.NOTE_REF}", "show", head))
    assert [entry["evidence_id"] for entry in note["entries"]] == [
        f"ev-concurrent-{index}" for index in range(8)
    ]


@pytest.mark.asyncio
async def test_input_bounds_and_redaction(memory_repo, tmp_path):
    _, head = make_landed_commit(memory_repo, "safe input decision")
    store = GrokSessionStore(db_path=tmp_path / "safe.db")
    try:
        with pytest.raises(wm.WorkspaceMemoryError, match="invalid repository-relative"):
            await wm.record_landed_outcome(
                store, landed_sha=head, summary="bad path", paths=["../secret"]
            )
        secret = "xai-live-" + "super-secret"
        result = await wm.record_landed_outcome(
            store,
            landed_sha=head,
            summary=f"Never persist XAI_API_KEY={secret} in evidence.",
        )
        row = await store.get_workspace_evidence(result["evidence_id"])
        assert secret not in row["summary"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_status_is_observable(memory_repo, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "status.db")
    try:
        status = await wm.workspace_memory_status(store)
        assert status["mode"] == "mirror"
        assert status["automatic_injection"] is False
        assert status["evidence_count"] == 0
    finally:
        await store.close()


def test_cli_status_is_observable(memory_repo, tmp_path):
    store = GrokSessionStore(db_path=tmp_path / "cli-status.db")

    out = io.StringIO()
    code = wm.workspace_memory_cli(["status"], stream=out, store=store)
    assert code == 0
    assert '"automatic_injection": false' in out.getvalue()


def test_full_sha_and_mode_validation(memory_repo, monkeypatch):
    with pytest.raises(wm.WorkspaceMemoryError, match="full 40-character"):
        wm._validate_sha(memory_repo, "HEAD")
    monkeypatch.setenv("UNIGROK_WORKSPACE_MEMORY", "nonsense")
    assert wm.workspace_memory_mode() == "off"


@pytest.mark.asyncio
async def test_workspace_memory_tools_are_registered_with_safe_schemas():
    from src.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}
    expected = {
        "recall_workspace_memory",
        "record_landed_outcome",
        "explain_workspace_evidence",
        "workspace_memory_status",
        "sync_workspace_memory_notes",
        "import_workspace_memory_notes",
    }
    assert expected <= tools.keys()
    for name in (
        "recall_workspace_memory",
        "explain_workspace_evidence",
        "workspace_memory_status",
    ):
        assert tools[name].annotations.readOnlyHint is True
    assert "ctx" not in tools["record_landed_outcome"].inputSchema.get("properties", {})
