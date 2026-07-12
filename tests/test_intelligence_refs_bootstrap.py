import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bootstrap_intelligence_refs as bootstrap


ROOT = Path(__file__).parents[1]


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    target.mkdir()
    git(target, "init", "-b", "main")
    git(target, "config", "user.name", "Capsule Test")
    git(target, "config", "user.email", "capsule@example.test")
    schema_dir = target / "docs" / "okf"
    schema_dir.mkdir(parents=True)
    shutil.copy2(ROOT / bootstrap.SCHEMA_PATH, schema_dir / bootstrap.SCHEMA_PATH.name)
    (target / "README.md").write_text("test repository\n", encoding="utf-8")
    git(target, "add", ".")
    git(target, "commit", "-m", "base")
    git(target, "update-ref", bootstrap.SOURCE_REF, git(target, "rev-parse", "HEAD"))
    return target


def apply_bootstrap(repo: Path) -> dict[str, object]:
    return bootstrap.bootstrap(repo, apply=True)


def test_dry_run_is_local_and_does_not_create_objects_or_refs(repo):
    before = git(repo, "count-objects", "-v")
    result = bootstrap.bootstrap(repo, apply=False)
    after = git(repo, "count-objects", "-v")

    assert result["status"] == "would_initialize"
    assert before == after
    assert git(repo, "for-each-ref", "--format=%(refname)", "refs/unigrok/") == ""


def test_bootstrap_creates_one_valid_zero_parent_anchor_transactionally(repo):
    result = apply_bootstrap(repo)
    anchor = result["anchor"]

    assert result["status"] == "initialized"
    assert isinstance(anchor, str)
    assert anchor == bootstrap.EXPECTED_GENESIS_OID
    for ref in bootstrap.FIXED_REFS:
        assert git(repo, "rev-parse", ref) == anchor
        assert git(repo, "reflog", "show", "--format=%H", ref) == anchor
    assert git(repo, "rev-list", "--parents", "-n", "1", anchor).split() == [anchor]
    paths = git(repo, "ls-tree", "-r", "--name-only", anchor).splitlines()
    assert paths == sorted([bootstrap.PROTOCOL_PATH, bootstrap.TREE_SCHEMA_PATH])
    tree = git(repo, "show", "-s", "--format=%T", anchor)
    assert (
        git(repo, "cat-file", "-p", anchor) + "\n"
        == bootstrap.canonical_root_bytes(tree).decode()
    )


def test_independent_clones_create_the_same_deterministic_genesis(repo, tmp_path):
    second = tmp_path / "second"
    git(tmp_path, "clone", str(repo), str(second))

    first_result = apply_bootstrap(repo)
    second_result = apply_bootstrap(second)

    assert first_result["anchor"] == second_result["anchor"]


def test_rerun_is_idempotent_and_preserves_advanced_mutable_heads(repo):
    first = apply_bootstrap(repo)
    anchor = str(first["anchor"])
    tree = git(repo, "show", "-s", "--format=%T", anchor)
    advanced = subprocess.run(
        ["git", "commit-tree", tree, "-p", anchor],
        cwd=repo,
        input="Promote verified knowledge\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    git(repo, "update-ref", "refs/unigrok/knowledge/verified", advanced, anchor)

    second = apply_bootstrap(repo)

    assert second == {
        "anchor": anchor,
        "created": [],
        "source_commit": git(repo, "rev-parse", bootstrap.SOURCE_REF),
        "status": "ready",
    }
    assert git(repo, "rev-parse", "refs/unigrok/knowledge/verified") == advanced
    assert git(repo, "rev-parse", bootstrap.SCHEMA_REF) == anchor


def test_missing_mutable_head_is_repaired_without_resetting_existing_heads(repo):
    first = apply_bootstrap(repo)
    anchor = str(first["anchor"])
    missing = "refs/unigrok/failures/sanitized"
    git(repo, "update-ref", "-d", missing, anchor)

    preview = bootstrap.bootstrap(repo, apply=False)
    repaired = apply_bootstrap(repo)

    expected_source = git(repo, "rev-parse", bootstrap.SOURCE_REF)
    assert preview == {
        "anchor": anchor,
        "created": [missing],
        "source_commit": expected_source,
        "status": "would_repair",
    }
    assert repaired == {
        "anchor": anchor,
        "created": [missing],
        "source_commit": expected_source,
        "status": "repaired",
    }
    assert git(repo, "rev-parse", missing) == anchor


def test_repair_transaction_verifies_every_observed_ref(repo, monkeypatch):
    first = apply_bootstrap(repo)
    anchor = str(first["anchor"])
    missing = "refs/unigrok/failures/sanitized"
    git(repo, "update-ref", "-d", missing, anchor)
    observed = bootstrap.read_refs(repo)
    original = bootstrap.update_refs
    captured: list[str] = []

    def recording_update(target, commands):
        captured.extend(commands)
        return original(target, captured)

    monkeypatch.setattr(bootstrap, "update_refs", recording_update)
    result = apply_bootstrap(repo)

    assert result["status"] == "repaired"
    for ref, oid in observed.items():
        if oid is not None:
            assert f"verify {ref} {oid}" in captured
    assert f"create {missing} {anchor}" in captured


def test_orphaned_namespace_without_schema_ref_fails_closed(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/unigrok/benchmarks/main", base)

    with pytest.raises(bootstrap.BootstrapError, match="partial or corrupt"):
        bootstrap.bootstrap(repo, apply=False)


def test_corrupt_schema_anchor_is_never_repaired_or_reset(repo):
    apply_bootstrap(repo)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", bootstrap.SCHEMA_REF, base)

    with pytest.raises(bootstrap.BootstrapError, match="pinned v1 genesis"):
        bootstrap.bootstrap(repo, apply=True)
    assert git(repo, "rev-parse", bootstrap.SCHEMA_REF) == base


def test_schema_anchor_requires_exact_blob_modes_and_kinds(repo, monkeypatch):
    _, schema_bytes = bootstrap.read_schema_source(repo)
    protocol_oid = bootstrap.hash_object(
        repo, bootstrap.protocol_bytes(schema_bytes), write=True
    )
    schema_oid = bootstrap.hash_object(repo, schema_bytes, write=True)
    tree_input = (
        f"100644 blob {protocol_oid}\t{bootstrap.PROTOCOL_PATH}\n"
        f"100755 blob {schema_oid}\t{bootstrap.TREE_SCHEMA_PATH}\n"
    )
    tree = bootstrap.run_git(repo, "mktree", input_text=tree_input).stdout.strip()
    anchor = bootstrap.hash_object(
        repo,
        bootstrap.canonical_root_bytes(tree),
        object_type="commit",
        write=True,
    )
    bootstrap.update_refs(
        repo, [f"create {ref} {anchor}" for ref in bootstrap.FIXED_REFS]
    )
    monkeypatch.setattr(bootstrap, "EXPECTED_GENESIS_OID", anchor)

    with pytest.raises(bootstrap.BootstrapError, match="two 100644 protocol blobs"):
        bootstrap.bootstrap(repo, apply=False)


def test_mutable_heads_must_be_descendant_commits(repo):
    result = apply_bootstrap(repo)
    anchor = str(result["anchor"])
    target = "refs/unigrok/knowledge/verified"
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=b"not a commit",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    git(repo, "update-ref", target, blob, anchor)
    with pytest.raises(bootstrap.BootstrapError, match="must point to a commit"):
        bootstrap.bootstrap(repo, apply=False)


def test_mutable_heads_cannot_switch_to_an_unrelated_commit(repo):
    result = apply_bootstrap(repo)
    anchor = str(result["anchor"])
    tree = git(repo, "show", "-s", "--format=%T", anchor)
    unrelated = subprocess.run(
        ["git", "commit-tree", tree],
        cwd=repo,
        input="Unrelated intelligence root\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    git(repo, "update-ref", "refs/unigrok/policies/active", unrelated, anchor)
    with pytest.raises(bootstrap.BootstrapError, match="is not descended"):
        bootstrap.bootstrap(repo, apply=False)


def test_schema_source_is_fetched_public_main_not_worktree_bytes(repo):
    tracked = git(repo, "show", f"{bootstrap.SOURCE_REF}:{bootstrap.SCHEMA_PATH}")
    (repo / bootstrap.SCHEMA_PATH).write_text("{}\n", encoding="utf-8")

    result = apply_bootstrap(repo)

    assert (
        git(
            repo,
            "show",
            f"{result['anchor']}:{bootstrap.TREE_SCHEMA_PATH}",
        )
        == tracked
    )


def test_missing_public_source_ref_fails_without_writing_objects(repo):
    git(repo, "update-ref", "-d", bootstrap.SOURCE_REF)
    before = git(repo, "count-objects", "-v")
    with pytest.raises(bootstrap.BootstrapError, match="pull the public repository"):
        bootstrap.bootstrap(repo, apply=True)
    assert git(repo, "count-objects", "-v") == before
    assert git(repo, "for-each-ref", "--format=%(refname)", "refs/unigrok/") == ""


def test_public_schema_bytes_are_pinned_to_the_immutable_v1_digest(repo):
    schema_path = repo / bootstrap.SCHEMA_PATH
    schema_path.write_text("{}\n", encoding="utf-8")
    git(repo, "add", str(bootstrap.SCHEMA_PATH))
    git(repo, "commit", "-m", "mutate schema")
    git(repo, "update-ref", bootstrap.SOURCE_REF, git(repo, "rev-parse", "HEAD"))

    with pytest.raises(bootstrap.BootstrapError, match="immutable v1 digest"):
        bootstrap.bootstrap(repo, apply=True)
    assert git(repo, "for-each-ref", "--format=%(refname)", "refs/unigrok/") == ""


def test_fixed_namespace_rejects_valid_and_broken_symbolic_refs(repo):
    result = apply_bootstrap(repo)
    anchor = str(result["anchor"])
    valid = "refs/unigrok/knowledge/verified"
    git(repo, "symbolic-ref", valid, "refs/heads/aliased-intelligence")
    git(repo, "update-ref", "refs/heads/aliased-intelligence", anchor)
    with pytest.raises(bootstrap.BootstrapError, match="direct ref"):
        bootstrap.bootstrap(repo, apply=False)

    git(repo, "symbolic-ref", valid, "refs/heads/broken-intelligence")
    with pytest.raises(bootstrap.BootstrapError, match="direct ref"):
        bootstrap.bootstrap(repo, apply=True)
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/broken-intelligence", check=False)
        == ""
    )


def test_public_source_ref_must_be_direct(repo):
    git(repo, "symbolic-ref", bootstrap.SOURCE_REF, "refs/heads/main")
    with pytest.raises(bootstrap.BootstrapError, match="direct ref"):
        bootstrap.bootstrap(repo, apply=False)


def test_concurrent_initializer_accepts_only_a_complete_valid_winner(repo, monkeypatch):
    original_update_refs = bootstrap.update_refs
    raced = False

    def race_once(target, commands):
        nonlocal raced
        if raced:
            return original_update_refs(target, commands)
        raced = True
        command_list = list(commands)
        anchor = command_list[0].split()[2]
        original_update_refs(
            target, [f"create {ref} {anchor}" for ref in bootstrap.FIXED_REFS]
        )
        raise bootstrap.BootstrapError("simulated compare-and-swap race")

    monkeypatch.setattr(bootstrap, "update_refs", race_once)
    result = apply_bootstrap(repo)

    assert result["status"] == "ready_after_race"
    assert all(
        git(repo, "rev-parse", ref) == result["anchor"] for ref in bootstrap.FIXED_REFS
    )


def test_two_process_initializers_converge_on_one_namespace(repo):
    command = [
        sys.executable,
        str(ROOT / "scripts" / "bootstrap_intelligence_refs.py"),
        "--repo",
        str(repo),
        "--apply",
    ]
    processes = [
        subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert all(process.returncode == 0 for process in processes), results
    payloads = [json.loads(stdout) for stdout, _stderr in results]
    anchors = {payload["anchor"] for payload in payloads}
    assert len(anchors) == 1
    assert all(git(repo, "rev-parse", ref) in anchors for ref in bootstrap.FIXED_REFS)


def test_bootstrap_uses_only_local_git_object_and_ref_commands(repo, monkeypatch):
    original_run = subprocess.run
    commands: list[str] = []

    def recording_run(args, *positional, **kwargs):
        if args and args[0] == "git":
            index = 1
            while args[index] == "-c":
                index += 2
            commands.append(args[index])
        return original_run(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    apply_bootstrap(repo)

    assert set(commands) <= {
        "cat-file",
        "hash-object",
        "ls-tree",
        "merge-base",
        "mktree",
        "rev-parse",
        "show",
        "symbolic-ref",
        "update-ref",
    }
    assert not {"fetch", "push", "ls-remote"} & set(commands)


def test_bootstrap_never_touches_public_consumer_sqlite(repo):
    database = repo / ".grok" / "grok_sessions.db"
    database.parent.mkdir()
    marker = b"private-public-consumer-state\x00\xff"
    database.write_bytes(marker)

    apply_bootstrap(repo)

    assert database.read_bytes() == marker
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert "grok_sessions.db" not in source
    assert "aiosqlite" not in source
