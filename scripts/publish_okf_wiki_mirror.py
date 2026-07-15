#!/usr/bin/env python3
"""Build a deterministic GitHub Wiki mirror from public UniGrok knowledge.

Mirror only — never the source of truth. The output directory is replaced on
each run so deleted OKF pages cannot survive as stale wiki content. Publishing
to the repository wiki remains a separate operator step.

Example:
  uv run python scripts/publish_okf_wiki_mirror.py --out-dir /tmp/unigrok-wiki
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OKF = ROOT / "docs" / "okf"
PACKS = ROOT / "docs" / "public-intelligence" / "packs"
OKF_MANIFEST = OKF / "okf-manifest.json"
RESERVED_WIKI_SLUGS = {"home", "_sidebar"}
HOME_BANNER = """\
> **Mirror only.** Source of truth: [OKF on the site](https://grokmcp.org/docs/okf/index.md)
> and `docs/okf/` in the repository. Do not hand-edit this wiki.

"""


def _strip_front_matter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\r\n") if len(parts) == 3 else text


def _slug(name: str) -> str:
    base = Path(name).name
    if base.lower().endswith(".md"):
        base = base[:-3]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "page"
    if slug.casefold() in RESERVED_WIKI_SLUGS:
        raise ValueError(f"{name!r} produces reserved wiki slug {slug!r}")
    return slug


def _reserve_slug(seen: dict[str, str], slug: str, source: str) -> None:
    key = slug.casefold()
    previous = seen.get(key)
    if previous is not None:
        raise ValueError(
            f"wiki slug collision for {slug!r}: {previous!r} and {source!r}"
        )
    seen[key] = source


def _write_page(
    out: Path,
    *,
    slug: str,
    body: str,
    source: str,
    seen: dict[str, str],
) -> None:
    _reserve_slug(seen, slug, source)
    (out / f"{slug}.md").write_text(HOME_BANNER + body, encoding="utf-8")


def _manifest_files() -> tuple[Path, list[Path]]:
    manifest = json.loads(OKF_MANIFEST.read_text(encoding="utf-8"))
    names = manifest.get("files")
    root_name = manifest.get("root")
    if not isinstance(names, list) or not names:
        raise ValueError("OKF manifest files must be a non-empty list")
    if not isinstance(root_name, str) or root_name not in names:
        raise ValueError("OKF manifest root must name one listed file")

    files: list[Path] = []
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"invalid OKF manifest path: {name!r}")
        path = OKF / name
        if path.suffix not in {".md", ".json"} or not path.is_file():
            raise ValueError(f"missing or unsupported OKF artifact: {name!r}")
        files.append(path)
    return OKF / root_name, files


def _json_page(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    json.loads(raw)
    return f"# {path.name}\n\n```json\n{raw.rstrip()}\n```\n"


def build_mirror(out: Path) -> list[Path]:
    out = out.resolve()
    root = ROOT.resolve()
    if out == root or out.is_relative_to(root):
        raise ValueError("--out-dir must be outside the repository")

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()

    index_path, okf_files = _manifest_files()
    pack_files = sorted(PACKS.glob("v*.md")) if PACKS.is_dir() else []
    seen = {slug: f"generated {slug}" for slug in RESERVED_WIKI_SLUGS}

    index = _strip_front_matter(index_path.read_text(encoding="utf-8"))
    home = "# UniGrok knowledge (OKF mirror)\n\n" + index
    home += "\n\n## Public intelligence packs\n\n"
    for pack in pack_files:
        home += f"- [[{_slug(pack.name)}|{pack.stem}]]\n"
    (out / "Home.md").write_text(HOME_BANNER + home, encoding="utf-8")

    markdown_files = [path for path in okf_files if path.suffix == ".md"]
    json_files = [path for path in okf_files if path.suffix == ".json"]
    for path in markdown_files:
        if path == index_path:
            continue
        _write_page(
            out,
            slug=_slug(path.name),
            body=_strip_front_matter(path.read_text(encoding="utf-8")),
            source=str(path.relative_to(ROOT)),
            seen=seen,
        )
    for path in json_files:
        _write_page(
            out,
            slug=_slug(path.name),
            body=_json_page(path),
            source=str(path.relative_to(ROOT)),
            seen=seen,
        )
    for path in pack_files:
        _write_page(
            out,
            slug=_slug(path.name),
            body=_strip_front_matter(path.read_text(encoding="utf-8")),
            source=str(path.relative_to(ROOT)),
            seen=seen,
        )

    sidebar_lines = [HOME_BANNER.strip(), "", "**OKF**", "[[Home]]"]
    for path in markdown_files:
        if path != index_path:
            sidebar_lines.append(f"[[{_slug(path.name)}|{path.stem}]]")
    if json_files:
        sidebar_lines.extend(["", "**Schemas and data**"])
        for path in json_files:
            sidebar_lines.append(f"[[{_slug(path.name)}|{path.name}]]")
    if pack_files:
        sidebar_lines.extend(["", "**Public packs**"])
        for path in pack_files:
            sidebar_lines.append(f"[[{_slug(path.name)}|{path.stem}]]")
    (out / "_Sidebar.md").write_text("\n".join(sidebar_lines) + "\n", encoding="utf-8")
    return sorted(out.glob("*.md"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="External directory replaced with generated wiki markdown.",
    )
    args = parser.parse_args()
    written = build_mirror(args.out_dir)
    out = args.out_dir.resolve()

    print(f"Wrote {len(written)} wiki pages to {out}")
    print("Publish separately, e.g.:")
    print(
        "  git clone https://github.com/djtelicloud/grok-mcp-server.wiki.git "
        "grok-mcp-server.wiki"
    )
    print(f"  rsync -a --delete {shlex.quote(f'{out}/')} grok-mcp-server.wiki/")
    print("  cd grok-mcp-server.wiki && git add -A && git commit && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
