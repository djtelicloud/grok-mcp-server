#!/usr/bin/env python3
"""Validate relative links in tracked Markdown without making network requests."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _tracked_markdown() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable was not found")
    completed = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in completed.stdout.splitlines() if line]


def main() -> int:
    failures: list[str] = []
    try:
        files = _tracked_markdown()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"docs-contract: ERROR: {exc}", file=sys.stderr)
        return 2
    for document in files:
        for line_number, line in enumerate(
            document.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for raw_target in LINK.findall(line):
                target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
                if not target or target.startswith(
                    ("#", "http://", "https://", "mailto:")
                ):
                    continue
                relative = unquote(target.split("#", 1)[0])
                if not relative:
                    continue
                if Path(relative).is_absolute():
                    failures.append(
                        f"{document.relative_to(ROOT)}:{line_number}: "
                        f"absolute filesystem link is not allowed: {relative}"
                    )
                    continue
                resolved = (document.parent / relative).resolve()
                if not resolved.is_relative_to(ROOT.resolve()):
                    failures.append(
                        f"{document.relative_to(ROOT)}:{line_number}: "
                        f"link escapes repository: {relative}"
                    )
                elif not resolved.exists():
                    failures.append(
                        f"{document.relative_to(ROOT)}:{line_number}: missing {relative}"
                    )
    if failures:
        print("docs-contract: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"docs-contract: OK ({len(files)} repository Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
