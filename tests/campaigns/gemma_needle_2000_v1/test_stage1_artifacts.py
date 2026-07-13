import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evals.campaigns.gemma_needle_2000_v1.stage1_artifacts import (
    PrivateArtifactStore,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def test_private_content_addressed_round_trip(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    reference = store.write_content_addressed(
        ("mock-run", "tool-selection"),
        "root",
        {"root_id": "root-1", "status": "verified"},
    )

    assert store.read(reference) == {"root_id": "root-1", "status": "verified"}
    assert _mode(store.root) == 0o700
    assert _mode(store.root / reference.relative_path) == 0o600


def test_named_artifacts_are_idempotent_but_immutable(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")
    first = store.write_named(("mock-run",), "manifest.json", {"version": 1})
    repeated = store.write_named(("mock-run",), "manifest.json", {"version": 1})

    assert repeated == first
    with pytest.raises(FileExistsError, match="different content"):
        store.write_named(("mock-run",), "manifest.json", {"version": 2})


def test_concurrent_named_publication_has_one_immutable_value(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    def publish(version: int):
        try:
            return store.write_named(
                ("mock-run",), "manifest.json", {"version": version}
            )
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        references = list(executor.map(publish, range(12)))

    winners = [reference for reference in references if reference is not None]
    assert winners
    assert len({reference.digest for reference in winners}) == 1
    assert store.read(winners[0])["version"] in range(12)


def test_read_rejects_tampered_artifact(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")
    reference = store.write_content_addressed(("mock-run",), "report", {"ok": True})
    path = store.root / reference.relative_path
    path.write_text('{"ok":false}', encoding="utf-8")
    os.chmod(path, 0o600)

    with pytest.raises(ValueError, match="digest validation"):
        store.read(reference)


def test_store_rejects_repo_and_symlink_paths(tmp_path: Path):
    with pytest.raises(ValueError, match="outside Git workspaces"):
        PrivateArtifactStore(Path.cwd() / ".unsafe-stage1-artifacts")

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked-artifacts"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        PrivateArtifactStore(link)


def test_store_rejects_secret_like_content(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="secret-like"):
        store.write_named(
            ("mock-run",),
            "unsafe.json",
            {"value": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"},
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_store_rejects_non_finite_json_numbers(tmp_path: Path, value: float):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="deterministic JSON"):
        store.write_named(("mock-run",), "non-finite.json", {"value": value})


@pytest.mark.parametrize("value", [[1, 2, 3], "text", 1, None])
def test_store_rejects_non_object_artifact_roots(tmp_path: Path, value):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="root must be an object"):
        store.write_named(("mock-run",), "invalid.json", value)
    with pytest.raises(ValueError, match="root must be an object"):
        store.write_content_addressed(("mock-run",), "invalid", value)


def test_content_addressed_prefix_is_predictably_bounded(tmp_path: Path):
    store = PrivateArtifactStore(tmp_path / "artifacts")

    reference = store.write_content_addressed(("mock-run",), "x" * 31, {"ok": True})
    assert store.read(reference) == {"ok": True}
    with pytest.raises(ValueError, match="at most 31"):
        store.write_content_addressed(("mock-run",), "x" * 32, {"ok": True})
