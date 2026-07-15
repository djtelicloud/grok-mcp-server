#!/usr/bin/env python3
"""Build a GitHub Wiki mirror from docs/okf + public intelligence packs.

Mirror only — never the source of truth. Writes markdown into --out-dir.
Push to the repo wiki is a separate operator step (or CI with wiki write token).

Example:
  uv run python scripts/publish_okf_wiki_mirror.py --out-dir /tmp/unigrok-wiki
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OKF = ROOT / "docs" / "okf"
PACKS = ROOT / "docs" / "public-intelligence" / "packs"
HOME_BANNER = """\
> **Mirror only.** Source of truth: [OKF on the site](https://grokmcp.org/docs/okf/index.md)
> and `docs/okf/` in the repository. Do not hand-edit this wiki.

"""


def _slug(name: str) -> str:
    base = Path(name).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "page"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to write wiki markdown (created if missing).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing .md files in out-dir before writing.",
    )
    args = parser.parse_args()
    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for old in out.glob("*.md"):
            old.unlink()

    # Home
    index = (OKF / "index.md").read_text(encoding="utf-8")
    # Strip YAML front matter if present
    if index.startswith("---"):
        parts = index.split("---", 2)
        if len(parts) >= 3:
            index = parts[2].lstrip("\n")
    home = HOME_BANNER + "# UniGrok knowledge (OKF mirror)\n\n" + index
    home += "\n\n## Public intelligence packs\n\n"
    if PACKS.is_dir():
        for pack in sorted(PACKS.glob("v*.md")):
            home += f"- [[{_slug(pack.name)}|{pack.stem}]]\n"
    (out / "Home.md").write_text(home, encoding="utf-8")

    # OKF pages
    for path in sorted(OKF.glob("*.md")):
        if path.name == "index.md":
            continue
        body = path.read_text(encoding="utf-8")
        if body.startswith("---"):
            parts = body.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].lstrip("\n")
        (out / f"{_slug(path.name)}.md").write_text(
            HOME_BANNER + body, encoding="utf-8"
        )

    # Public packs
    if PACKS.is_dir():
        for path in sorted(PACKS.glob("v*.md")):
            body = path.read_text(encoding="utf-8")
            (out / f"{_slug(path.name)}.md").write_text(
                HOME_BANNER + body, encoding="utf-8"
            )

    # Sidebar for GitHub wiki
    sidebar_lines = [
        HOME_BANNER.strip(),
        "",
        "**OKF**",
        "[[Home]]",
    ]
    for path in sorted(OKF.glob("*.md")):
        if path.name == "index.md":
            continue
        sidebar_lines.append(f"[[{_slug(path.name)}|{path.stem}]]")
    if PACKS.is_dir() and any(PACKS.glob("v*.md")):
        sidebar_lines.append("")
        sidebar_lines.append("**Public packs**")
        for path in sorted(PACKS.glob("v*.md")):
            sidebar_lines.append(f"[[{_slug(path.name)}|{path.stem}]]")
    (out / "_Sidebar.md").write_text("\n".join(sidebar_lines) + "\n", encoding="utf-8")

    print(f"Wrote wiki mirror to {out}")
    print("Push separately, e.g.:")
    print("  git clone https://github.com/djtelicloud/grok-mcp-server.wiki.git")
    print("  cp -R out-dir/* wiki-clone/ && cd wiki-clone && git add -A && git commit && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
