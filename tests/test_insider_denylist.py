from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DENYLIST = REPOSITORY_ROOT / "scripts" / "ci-insider-denylist.sh"

FORBIDDEN_FAMILY_PATHS = (
    ".agents",
    "agentixos",
    "architecture/agentixos",
    "campaigns",
    "codex",
    "cursor",
    "harvest",
    "playbooks",
    "providers",
    "tools",
)

FORBIDDEN_DESCENDANT_FIXTURES = tuple(
    f"{family}/private.txt" for family in FORBIDDEN_FAMILY_PATHS
)


def _run_denylist(
    repository: Path, *, track: bool = False
) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    bash = shutil.which("bash")
    assert git is not None
    assert bash is not None
    subprocess.run(
        [git, "init", "--quiet"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    if track:
        subprocess.run(
            [git, "add", "--all"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        [bash, str(DENYLIST)],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("relative_path", FORBIDDEN_DESCENDANT_FIXTURES)
def test_private_tree_is_rejected_from_public_clone(
    tmp_path: Path, relative_path: str
) -> None:
    fixture = tmp_path / relative_path
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("fixture\n", encoding="utf-8")

    result = _run_denylist(tmp_path)

    assert result.returncode == 1
    assert relative_path in result.stderr


@pytest.mark.parametrize("relative_path", FORBIDDEN_FAMILY_PATHS)
def test_exact_private_family_path_is_rejected(
    tmp_path: Path, relative_path: str
) -> None:
    fixture = tmp_path / relative_path
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("fixture\n", encoding="utf-8")

    result = _run_denylist(tmp_path)

    assert result.returncode == 1
    assert relative_path in result.stderr


@pytest.mark.parametrize("relative_path", FORBIDDEN_FAMILY_PATHS)
def test_exact_private_family_symlink_is_rejected(
    tmp_path: Path, relative_path: str
) -> None:
    fixture = tmp_path / relative_path
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.symlink_to("not-inspected-target")

    result = _run_denylist(tmp_path, track=True)

    assert result.returncode == 1
    assert "FORBIDDEN tracked symlink" in result.stderr


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/nested-link",
        "docs/内部-link",
        "docs/control\nlink",
    ),
)
def test_any_nested_tracked_symlink_is_rejected(
    tmp_path: Path, relative_path: str
) -> None:
    fixture = tmp_path / relative_path
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.symlink_to("not-inspected-target")

    result = _run_denylist(tmp_path, track=True)

    assert result.returncode == 1
    assert "FORBIDDEN tracked symlink" in result.stderr


def test_untracked_symlink_is_rejected_before_staging(tmp_path: Path) -> None:
    fixture = tmp_path / "docs/untracked-link"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.symlink_to("not-inspected-target")

    result = _run_denylist(tmp_path)

    assert result.returncode == 1
    assert "FORBIDDEN untracked symlink" in result.stderr


def test_tracked_nested_repository_is_rejected(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run(
        [git, "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            git,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'1' * 40},docs/nested-repository",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    result = _run_denylist(tmp_path)

    assert result.returncode == 1
    assert "FORBIDDEN tracked nested repository" in result.stderr


def test_similarly_named_regular_public_files_remain_allowed(tmp_path: Path) -> None:
    for relative_path in (
        "src/unigrok_public/server.py",
        "docs/reference.md",
        "scripts/check_docs.py",
        "tests/test_public_boundary.py",
        ".agents.md",
        ".claude.md",
        ".a2a.md",
        "agentixos.md",
        "architecture/agentixos.md",
        "archives.md",
        "campaigns.md",
        "codex.md",
        "cursor.md",
        "evals.md",
        "harvest.md",
        "mcp_ui.md",
        "playbooks.md",
        "providers.md",
        "sites.md",
        "tools.md",
        "docs/forge-console-notes.md",
        "docs/内部.md",
        "docs/control\nname.md",
    ):
        fixture = tmp_path / relative_path
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("public fixture\n", encoding="utf-8")

    executable = tmp_path / "scripts/public-helper"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    result = _run_denylist(tmp_path, track=True)

    assert result.returncode == 0, result.stderr
