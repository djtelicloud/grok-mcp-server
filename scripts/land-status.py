#!/usr/bin/env python3
"""Show whether visible main, agent worktrees, and the shared runtime agree."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from land import LandError, common_git_dir, git, main_worktree, worktrees


def main() -> int:
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    primary = main_worktree(repo)
    main_head = git(primary, "rev-parse", "HEAD")
    print(f"Visible main: {main_head} ({primary})")
    marker = common_git_dir(repo) / "unigrok-land" / "runtime-head"
    try:
        runtime_head = marker.read_text(encoding="utf-8").strip()
    except OSError:
        runtime_head = "unknown"
    relation = "matches main" if runtime_head == main_head else "differs or not yet recorded"
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
                runtime = json.load(response)
            print(
                f"{label}: ready "
                f"(transport={runtime.get('transport')}, "
                f"api={runtime.get('api_plane', {}).get('xai_api_key')}, "
                f"cli={runtime.get('cli_plane', {}).get('auth_state')})"
            )
        except (OSError, URLError, ValueError) as exc:
            print(f"{label}: unavailable ({exc})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LandError, subprocess.SubprocessError) as exc:
        print(f"status failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
