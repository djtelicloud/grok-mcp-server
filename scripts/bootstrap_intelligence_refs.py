#!/usr/bin/env python3
"""Safely bootstrap the local UniGrok Insider intelligence refs.

The command is deliberately local-only. It never discovers, fetches, or pushes
a remote. Remote compatibility projection and trust promotion are separate
admin workflows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

FIXED_REFS = (
    "refs/unigrok/schema/v1",
    "refs/unigrok/knowledge/verified",
    "refs/unigrok/benchmarks/main",
    "refs/unigrok/policies/active",
    "refs/unigrok/failures/sanitized",
)
SCHEMA_REF = FIXED_REFS[0]
SCHEMA_PATH = Path("docs/okf/intelligence-capsule-v1.schema.json")
SOURCE_REF = "refs/remotes/origin/main"
PROTOCOL_PATH = "protocol.jcs"
TREE_SCHEMA_PATH = "intelligence-capsule-v1.schema.json"
ROOT_NAME = "UniGrok Protocol"
ROOT_EMAIL = "protocol@grokmcp.org"
ROOT_EPOCH = 946684800
ROOT_MESSAGE = "Initialize UniGrok Insider IntelligenceCapsule v1\n"
OBJECT_FORMAT = "sha1"
EXPECTED_SCHEMA_SHA256 = (
    "10c2ec4638bd6c4e303b3e2c4c7d91ae582554f48aaa01fac2d9370062b98d4c"
)
EXPECTED_GENESIS_OID = "6dadda28ac4174bf227f36b45917e15c663987ce"


class BootstrapError(RuntimeError):
    pass


def run_git(
    repo: Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise BootstrapError(f"git {' '.join(args)}: {detail}")
    return result


def hash_object(
    repo: Path,
    payload: bytes,
    *,
    object_type: str = "blob",
    write: bool,
) -> str:
    args = ["git", "hash-object", "-t", object_type]
    if write:
        args.append("-w")
    args.append("--stdin")
    result = subprocess.run(
        args,
        cwd=repo,
        input=payload,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if result.returncode:
        detail = (
            (result.stderr or result.stdout or b"git hash-object failed")
            .decode("utf-8", errors="replace")
            .strip()
        )
        raise BootstrapError(f"git {' '.join(args[1:])}: {detail}")
    return result.stdout.decode("ascii").strip()


def resolve_repo(path: Path) -> Path:
    result = run_git(path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode:
        raise BootstrapError(f"not a Git worktree: {path}")
    repo = Path(result.stdout.strip()).resolve()
    object_format = run_git(repo, "rev-parse", "--show-object-format").stdout.strip()
    if object_format != OBJECT_FORMAT:
        raise BootstrapError(
            f"IntelligenceCapsule v1 requires Git object format {OBJECT_FORMAT}, "
            f"found {object_format}"
        )
    return repo


def require_direct_ref(repo: Path, ref: str) -> None:
    result = run_git(repo, "symbolic-ref", "-q", ref, check=False)
    if result.returncode == 0:
        raise BootstrapError(f"{ref} must be a direct ref, not a symbolic ref")
    if result.returncode != 1:
        detail = (result.stderr or result.stdout or "cannot inspect ref").strip()
        raise BootstrapError(f"cannot inspect {ref}: {detail}")


def read_blob(repo: Path, object_spec: str, *, label: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", object_spec],
        cwd=repo,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if result.returncode:
        detail = (
            (result.stderr or result.stdout or b"git cat-file failed")
            .decode("utf-8", errors="replace")
            .strip()
        )
        raise BootstrapError(f"cannot read {label}: {detail}")
    return result.stdout


def read_schema_source(repo: Path) -> tuple[str, bytes]:
    require_direct_ref(repo, SOURCE_REF)
    result = run_git(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{SOURCE_REF}^{{commit}}",
        check=False,
    )
    if result.returncode:
        raise BootstrapError(
            f"public source ref is unavailable: {SOURCE_REF}; pull the public "
            "repository before bootstrapping"
        )
    source_commit = result.stdout.strip()
    try:
        schema_bytes = read_blob(
            repo,
            f"{source_commit}:{SCHEMA_PATH.as_posix()}",
            label=f"{SCHEMA_PATH} at public source {source_commit}",
        )
    except BootstrapError as exc:
        raise BootstrapError(
            f"public source {source_commit} does not contain {SCHEMA_PATH}"
        ) from exc
    digest = hashlib.sha256(schema_bytes).hexdigest()
    if digest != EXPECTED_SCHEMA_SHA256:
        raise BootstrapError(
            f"public source schema digest {digest} is not the immutable v1 digest "
            f"{EXPECTED_SCHEMA_SHA256}; publish a new protocol version instead"
        )
    return source_commit, schema_bytes


def protocol_bytes(schema_bytes: bytes) -> bytes:
    manifest = {
        "protocol": "org.grokmcp.intelligence-capsule",
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "serialization": "rfc8785-jcs-unigrok-profile-v1",
        "version": 1,
    }
    return json.dumps(
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def read_ref(repo: Path, ref: str) -> str | None:
    result = run_git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def read_refs(repo: Path) -> dict[str, str | None]:
    for ref in FIXED_REFS:
        require_direct_ref(repo, ref)
    return {ref: read_ref(repo, ref) for ref in FIXED_REFS}


def canonical_root_bytes(tree: str) -> bytes:
    identity = f"{ROOT_NAME} <{ROOT_EMAIL}> {ROOT_EPOCH} +0000"
    return (
        f"tree {tree}\nauthor {identity}\ncommitter {identity}\n\n{ROOT_MESSAGE}"
    ).encode("utf-8")


def validate_schema_anchor(repo: Path, commit: str, schema_bytes: bytes) -> None:
    if commit != EXPECTED_GENESIS_OID:
        raise BootstrapError(
            f"{SCHEMA_REF} must point to pinned v1 genesis {EXPECTED_GENESIS_OID}"
        )
    object_type = run_git(repo, "cat-file", "-t", commit).stdout.strip()
    if object_type != "commit":
        raise BootstrapError(f"{SCHEMA_REF} does not point to a commit")
    commit_text = run_git(repo, "cat-file", "-p", commit).stdout
    header = commit_text.partition("\n\n")[0].splitlines()
    if any(line.startswith("parent ") for line in header):
        raise BootstrapError(f"{SCHEMA_REF} must point to a zero-parent anchor commit")
    if not header or not header[0].startswith("tree "):
        raise BootstrapError(f"{SCHEMA_REF} anchor has no tree")
    tree = header[0].removeprefix("tree ")
    if commit_text.encode("utf-8") != canonical_root_bytes(tree):
        raise BootstrapError(f"{SCHEMA_REF} is not the deterministic v1 genesis commit")

    expected_blobs = {
        PROTOCOL_PATH: hash_object(repo, protocol_bytes(schema_bytes), write=False),
        TREE_SCHEMA_PATH: hash_object(repo, schema_bytes, write=False),
    }
    expected_entries = [
        f"100644 blob {expected_blobs[path]}\t{path}" for path in sorted(expected_blobs)
    ]
    entries = run_git(repo, "ls-tree", commit).stdout.splitlines()
    if entries != expected_entries:
        raise BootstrapError(
            f"{SCHEMA_REF} tree must contain exactly two 100644 protocol blobs"
        )
    actual_protocol = read_blob(
        repo, f"{commit}:{PROTOCOL_PATH}", label=f"{SCHEMA_REF} protocol"
    )
    actual_schema = read_blob(
        repo, f"{commit}:{TREE_SCHEMA_PATH}", label=f"{SCHEMA_REF} schema"
    )
    if actual_protocol != protocol_bytes(schema_bytes):
        raise BootstrapError(f"{SCHEMA_REF} protocol bytes do not match v1")
    if actual_schema != schema_bytes:
        raise BootstrapError(
            f"{SCHEMA_REF} schema bytes do not match the pinned v1 schema"
        )


def create_root_commit(repo: Path, schema_bytes: bytes) -> str:
    blobs = {
        PROTOCOL_PATH: hash_object(repo, protocol_bytes(schema_bytes), write=True),
        TREE_SCHEMA_PATH: hash_object(repo, schema_bytes, write=True),
    }
    tree_input = "".join(
        f"100644 blob {oid}\t{name}\n" for name, oid in sorted(blobs.items())
    )
    tree = run_git(repo, "mktree", input_text=tree_input).stdout.strip()
    commit = hash_object(
        repo,
        canonical_root_bytes(tree),
        object_type="commit",
        write=True,
    )
    if commit != EXPECTED_GENESIS_OID:
        raise BootstrapError(
            f"computed v1 genesis {commit} does not match {EXPECTED_GENESIS_OID}"
        )
    return commit


def validate_present_refs(
    repo: Path,
    refs: dict[str, str | None],
    anchor: str,
    schema_bytes: bytes,
) -> None:
    if refs.get(SCHEMA_REF) != anchor:
        raise BootstrapError(f"{SCHEMA_REF} changed during validation")
    validate_schema_anchor(repo, anchor, schema_bytes)
    for ref, oid in refs.items():
        if ref == SCHEMA_REF or oid is None:
            continue
        if run_git(repo, "cat-file", "-t", oid).stdout.strip() != "commit":
            raise BootstrapError(f"{ref} must point to a commit")
        ancestry = run_git(
            repo,
            "merge-base",
            "--is-ancestor",
            anchor,
            oid,
            check=False,
        )
        if ancestry.returncode:
            raise BootstrapError(f"{ref} is not descended from {SCHEMA_REF}")


def validate_complete_namespace(
    repo: Path,
    refs: dict[str, str | None],
    schema_bytes: bytes,
) -> str:
    missing = [ref for ref, oid in refs.items() if oid is None]
    if missing:
        raise BootstrapError(f"ref namespace is incomplete: missing {missing}")
    anchor = refs[SCHEMA_REF]
    assert anchor is not None
    validate_present_refs(repo, refs, anchor, schema_bytes)
    return anchor


def update_refs(
    repo: Path,
    commands: Iterable[str],
) -> None:
    transaction = "\n".join(["start", *commands, "prepare", "commit", ""])
    run_git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "update-ref",
        "--no-deref",
        "--create-reflog",
        "--stdin",
        input_text=transaction,
    )


def bootstrap(
    repo: Path,
    *,
    apply: bool,
) -> dict[str, object]:
    repo = resolve_repo(repo)
    source_commit, schema_bytes = read_schema_source(repo)
    refs = read_refs(repo)
    existing = {ref: oid for ref, oid in refs.items() if oid}

    if SCHEMA_REF not in existing and existing:
        raise BootstrapError(
            f"ref namespace is partial or corrupt: {SCHEMA_REF} is missing while "
            f"{sorted(existing)} exist"
        )

    if SCHEMA_REF in existing:
        anchor = existing[SCHEMA_REF]
        validate_present_refs(repo, refs, anchor, schema_bytes)
        missing = [ref for ref, oid in refs.items() if oid is None]
        if not missing:
            return {
                "anchor": anchor,
                "created": [],
                "source_commit": source_commit,
                "status": "ready",
            }
        if not apply:
            return {
                "anchor": anchor,
                "created": missing,
                "source_commit": source_commit,
                "status": "would_repair",
            }
        commands = [
            *(f"verify {ref} {oid}" for ref, oid in refs.items() if oid is not None),
            *(f"create {ref} {anchor}" for ref in missing),
        ]
        try:
            update_refs(repo, commands)
        except BootstrapError:
            winner = read_refs(repo)
            winner_anchor = validate_complete_namespace(repo, winner, schema_bytes)
            return {
                "anchor": winner_anchor,
                "created": [],
                "source_commit": source_commit,
                "status": "ready_after_race",
            }
        validate_complete_namespace(repo, read_refs(repo), schema_bytes)
        return {
            "anchor": anchor,
            "created": missing,
            "source_commit": source_commit,
            "status": "repaired",
        }

    if not apply:
        return {
            "anchor": None,
            "created": list(FIXED_REFS),
            "source_commit": source_commit,
            "status": "would_initialize",
        }

    anchor = create_root_commit(repo, schema_bytes)
    try:
        update_refs(repo, [f"create {ref} {anchor}" for ref in FIXED_REFS])
    except BootstrapError:
        # A concurrent initializer may have won. Accept only a complete,
        # byte-valid winner; never reset or overwrite any ref.
        winner = read_refs(repo)
        winner_anchor = validate_complete_namespace(repo, winner, schema_bytes)
        return {
            "anchor": winner_anchor,
            "created": [],
            "source_commit": source_commit,
            "status": "ready_after_race",
        }
    validate_complete_namespace(repo, read_refs(repo), schema_bytes)
    return {
        "anchor": anchor,
        "created": list(FIXED_REFS),
        "source_commit": source_commit,
        "status": "initialized",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap local-only refs/unigrok/* IntelligenceCapsule heads."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--apply", action="store_true", help="create or repair local refs"
    )
    args = parser.parse_args()
    try:
        result = bootstrap(args.repo, apply=args.apply)
    except BootstrapError as exc:
        parser.exit(2, f"bootstrap refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
