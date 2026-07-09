import subprocess

import pytest

from src.tools import git as git_tools


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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

    monkeypatch.setattr(git_tools.PathResolver, "get_project_root", staticmethod(lambda: repo))
    return repo


@pytest.mark.asyncio
async def test_git_read_tools(git_repo, monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    (git_repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")

    status = await git_tools.git_status()
    diff = await git_tools.git_diff()
    branch = await git_tools.git_current_branch()
    log = await git_tools.git_log(limit=1)
    show = await git_tools.git_show("HEAD")

    assert "README.md" in status
    assert "+world" in diff
    assert branch in {"main", "master"}
    assert "initial" in log
    assert "README.md" in show


@pytest.mark.asyncio
async def test_git_write_tools_fail_by_default(git_repo, monkeypatch):
    monkeypatch.delenv("UNIGROK_RUNTIME", raising=False)
    monkeypatch.delenv("ENABLE_GIT_WRITE", raising=False)

    with pytest.raises(PermissionError):
        await git_tools.git_create_branch("feature/test")


@pytest.mark.asyncio
async def test_git_write_tools_work_when_enabled(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    branch_res = await git_tools.git_create_branch("feature/test")
    assert "feature/test" in branch_res

    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 hello
+patched
"""
    apply_res = await git_tools.git_apply_patch(patch)
    assert "applied" in apply_res.lower()

    commit_res = await git_tools.git_commit("update readme", ["README.md"])
    assert "update readme" in commit_res or "files changed" in commit_res


@pytest.mark.asyncio
async def test_git_write_tools_fail_in_cloudrun(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    with pytest.raises(PermissionError):
        await git_tools.git_create_branch("feature/cloud")


@pytest.mark.asyncio
async def test_git_apply_patch_size_limit(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    with pytest.raises(ValueError, match="512KB"):
        await git_tools.git_apply_patch("x" * (git_tools.PATCH_LIMIT_BYTES + 1))


@pytest.mark.asyncio
async def test_git_apply_patch_allows_safe_dev_null_create(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    patch = """diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+new
"""

    result = await git_tools.git_apply_patch(patch)

    assert "applied" in result.lower()
    assert (git_repo / "new.txt").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_git_apply_patch_rejects_traversal_paths(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    patch = """diff --git a/../evil.txt b/../evil.txt
--- a/../evil.txt
+++ b/../evil.txt
@@ -0,0 +1 @@
+evil
"""

    with pytest.raises(PermissionError):
        await git_tools.git_apply_patch(patch)


@pytest.mark.asyncio
async def test_git_apply_patch_rejects_absolute_paths(git_repo, monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "local")
    monkeypatch.setenv("ENABLE_GIT_WRITE", "1")

    patch = """--- /tmp/evil.txt
+++ /tmp/evil.txt
@@ -0,0 +1 @@
+evil
"""

    with pytest.raises(PermissionError):
        await git_tools.git_apply_patch(patch)


@pytest.mark.asyncio
async def test_git_read_tools_noop_in_cloudrun(monkeypatch):
    monkeypatch.setenv("UNIGROK_RUNTIME", "cloudrun")

    assert "unavailable" in (await git_tools.git_status()).lower()
