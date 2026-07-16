"""git_apply_patch holds a per-repo lock across check+apply."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from src.tools import git as git_tools


def _git(repo, *args):
    subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    monkeypatch.setattr(
        git_tools.PathResolver, "get_workspace_root", staticmethod(lambda: repo)
    )
    return repo


@pytest.mark.asyncio
async def test_git_apply_patch_serializes_check_and_apply(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    events: list[str] = []
    real_run = git_tools._run_git
    gate = asyncio.Event()
    in_check = asyncio.Event()

    async def traced(args, repo_path=None, stdin=None):
        label = " ".join(args)
        events.append(f"enter:{label}")
        if args[:2] == ["apply", "--check"]:
            in_check.set()
            await gate.wait()
        result = await real_run(args, repo_path, stdin=stdin)
        events.append(f"exit:{label}")
        return result

    monkeypatch.setattr(git_tools, "_run_git", traced)

    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 hello
+locked
"""

    task = asyncio.create_task(git_tools.git_apply_patch(patch))
    await asyncio.wait_for(in_check.wait(), timeout=2.0)
    # While first call is inside --check, the lock must still be held so a
    # second apply cannot enter --check yet.
    assert git_tools._git_repo_lock(git_repo).locked()
    gate.set()
    await task
    assert events[0] == "enter:apply --check -"
    assert "enter:apply -" in events
    assert events.index("exit:apply --check -") < events.index("enter:apply -")
