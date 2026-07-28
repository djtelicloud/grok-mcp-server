from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DENYLIST = REPOSITORY_ROOT / "scripts" / "ci-insider-denylist.sh"

PRIVATE_TREE_FIXTURES = (
    ".agents/skills/private.md",
    "agentixos/control_plane/private.py",
    "architecture/agentixos/v1/private.json",
    "campaigns/private.md",
    "codex/private.md",
    "cursor/private.md",
    "harvest/private.json",
    "playbooks/private.md",
    "providers/private.md",
    "tools/private/private.py",
)


def _run_denylist(repository: Path) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        [bash, str(DENYLIST)],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("relative_path", PRIVATE_TREE_FIXTURES)
def test_private_tree_is_rejected_from_public_clone(
    tmp_path: Path, relative_path: str
) -> None:
    fixture = tmp_path / relative_path
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("fixture\n", encoding="utf-8")

    result = _run_denylist(tmp_path)

    assert result.returncode == 1
    assert relative_path in result.stderr


def test_public_product_tree_remains_allowed(tmp_path: Path) -> None:
    for relative_path in (
        "src/unigrok_public/server.py",
        "docs/reference.md",
        "scripts/check_docs.py",
        "tests/test_public_boundary.py",
    ):
        fixture = tmp_path / relative_path
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("public fixture\n", encoding="utf-8")

    result = _run_denylist(tmp_path)

    assert result.returncode == 0, result.stderr
