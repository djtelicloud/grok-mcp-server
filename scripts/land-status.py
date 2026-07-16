#!/usr/bin/env python3
"""Show whether visible main, agent worktrees, and the shared runtime agree."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from land import LandError, common_git_dir, git, main_worktree, worktrees


def tree_id(repo: Path, revision: str) -> str | None:
    """Resolve a commit tree without letting a stale marker break status."""

    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return None
    try:
        return git(repo, "rev-parse", "--verify", f"{revision}^{{tree}}")
    except LandError:
        return None


def format_age(seconds: float) -> str:
    """Render elapsed time as a coarse, human-readable age."""

    if seconds < 60:
        return f"~{int(seconds)}s ago"
    if seconds < 3600:
        return f"~{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"~{int(seconds // 3600)}h ago"
    return f"~{int(seconds // 86400)}d ago"


def origin_sync_line(primary: Path, main_head: str) -> str:
    """Compare visible main against last-fetched origin/main, all offline."""

    try:
        git(primary, "rev-parse", "--verify", "refs/remotes/origin/main")
    except LandError:
        return "origin/main not found (unfetched or local-only repo)"
    try:
        counts = git(
            primary,
            "rev-list",
            "--left-right",
            "--count",
            f"{main_head}...refs/remotes/origin/main",
        )
    except LandError:
        # Degrade just this line; details stay out of shared logs.
        return "origin/main comparison failed"
    ahead, _, behind = counts.partition("\t")
    return f"{ahead} ahead / {behind} behind origin/main"


def fetch_age_line(repo: Path) -> str:
    """Report the most recent FETCH_HEAD write across all worktrees."""

    git_dir = common_git_dir(repo)
    candidates = [git_dir / "FETCH_HEAD", *git_dir.glob("worktrees/*/FETCH_HEAD")]
    latest: float | None = None
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    if latest is None:
        return "never fetched"
    return f"last fetch {format_age(time.time() - latest)}"


def main() -> int:
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    primary = main_worktree(repo)
    main_head = git(primary, "rev-parse", "HEAD")
    print(f"Visible main: {main_head} ({primary})")
    print(f"Origin sync: {origin_sync_line(primary, main_head)} ({fetch_age_line(repo)})")
    marker = common_git_dir(repo) / "unigrok-land" / "runtime-head"
    try:
        runtime_head = marker.read_text(encoding="utf-8").strip()
    except OSError:
        runtime_head = "unknown"
    if runtime_head == main_head:
        relation = "matches main"
    else:
        runtime_tree = tree_id(primary, runtime_head)
        main_tree = tree_id(primary, main_head)
        relation = (
            "matches main tree"
            if runtime_tree is not None and runtime_tree == main_tree
            else "differs or not yet recorded"
        )
    print(f"Contributor runtime source marker: {runtime_head} ({relation})")
    print("Worktrees:")
    for item in worktrees(repo):
        branch = item.get("branch", "detached").removeprefix("refs/heads/")
        head = item.get("HEAD", "unknown")
        relation = "main" if head == main_head else "differs"
        print(f"  {branch}: {head[:12]} ({relation}) — {item['worktree']}")
    print("Branches ahead of main:")
    ahead = False
    branches = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    for branch in branches:
        if branch == "main":
            continue
        count = git(repo, "rev-list", "--count", f"main..{branch}")
        if count != "0":
            ahead = True
            print(f"  {branch}: {count} commit(s)")
    if not ahead:
        print("  none")
    for label, url in (
        ("Stable service", "http://127.0.0.1:4765/runtimez"),
        ("Contributor dev service", "http://127.0.0.1:4766/runtimez"),
    ):
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310
                json.load(response)
            print(f"{label}: ready")
        except (OSError, URLError, ValueError):
            # The fixed endpoint is enough context; exception text may include
            # response details that should not be copied into shared logs.
            print(f"{label}: unavailable")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LandError, subprocess.SubprocessError) as exc:
        print(f"status failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
