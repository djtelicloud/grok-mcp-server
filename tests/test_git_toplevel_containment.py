"""Refuse git tools when workspace .git points at an external repository."""

from __future__ import annotations

import subprocess

import pytest

from src.tools import git as git_tools


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def twin_repos(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    for repo in (workspace, outside):
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text(f"root={repo.name}\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "initial")

    # Replace workspace .git with a gitfile pointing at the external repo.
    import shutil

    shutil.rmtree(workspace / ".git")
    (workspace / ".git").write_text(f"gitdir: {outside / '.git'}\n", encoding="utf-8")

    monkeypatch.setattr(
        git_tools.PathResolver, "get_workspace_root", staticmethod(lambda: workspace)
    )
    return workspace, outside


@pytest.mark.asyncio
async def test_git_show_rejects_external_gitdir(twin_repos, monkeypatch):
    _workspace, outside = twin_repos
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    with pytest.raises(PermissionError, match="escapes the workspace|gitdir redirect"):
        await git_tools.git_show("HEAD")
    # External history must remain untouched / unread via workspace tools.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "initial" in log


@pytest.mark.asyncio
async def test_git_create_branch_rejects_external_gitdir(twin_repos, monkeypatch):
    _workspace, outside = twin_repos
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")
    with pytest.raises(PermissionError, match="escapes the workspace|gitdir redirect"):
        await git_tools.git_create_branch("pwned-branch")
    branches = subprocess.run(
        ["git", "branch", "--list", "pwned-branch"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "pwned-branch" not in branches
