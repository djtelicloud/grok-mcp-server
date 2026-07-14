#!/usr/bin/env python3
"""Generate and verify the canonical OKF API reference and public mirror."""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
OKF_DIR = ROOT_DIR / "docs" / "okf"
API_REF_PATH = OKF_DIR / "api-reference.md"
MANIFEST_PATH = OKF_DIR / "okf-manifest.json"
PUBLIC_OKF_DIR = ROOT_DIR / "sites" / "unigrok-control-center" / "public" / "docs" / "okf"


class GenerationError(RuntimeError):
    """Raised when a complete deterministic OKF bundle cannot be produced."""


def get_keywords(node_name: str) -> str:
    """Generate keywords from a snake_case, dotted, or CamelCase symbol name."""
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+", node_name)
    return ", ".join(word.lower() for word in words)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, qualified_name: str | None = None) -> str:
    name = qualified_name or node.name
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {name}({ast.unparse(node.args)}){returns}"


def _function_item(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    owner: str | None = None,
) -> dict[str, str] | None:
    if node.name.startswith("_"):
        return None
    docstring = ast.get_docstring(node)
    if not docstring:
        return None
    qualified_name = f"{owner}.{node.name}" if owner else node.name
    return {
        "type": "method" if owner else "function",
        "name": qualified_name,
        "docstring": docstring,
        "keywords": get_keywords(qualified_name),
        "signature": _signature(node, qualified_name=qualified_name),
    }


def extract_docs_from_file(file_path: Path) -> list[dict[str, Any]]:
    """Extract documented public classes, methods, sync functions, and async functions."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise GenerationError(f"cannot parse {file_path}: {exc}") from exc

    items: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            docstring = ast.get_docstring(node)
            if not node.name.startswith("_") and docstring:
                items.append(
                    {
                        "type": "class",
                        "name": node.name,
                        "docstring": docstring,
                        "keywords": get_keywords(node.name),
                        "signature": f"class {node.name}",
                    }
                )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    item = _function_item(child, owner=node.name)
                    if item:
                        items.append(item)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            item = _function_item(node)
            if item:
                items.append(item)
    return items


def _anchor(value: str) -> str:
    # Preserve identifier underscores so public documentation anchors cannot
    # resemble provider secret prefixes such as ``xai-...``.
    return re.sub(r"[^a-z0-9_]+", "-", value.lower()).strip("-")


def render_api_reference() -> str:
    """Render the complete deterministic API reference without mutating the tree."""
    all_items: dict[str, list[dict[str, Any]]] = {}
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        items = extract_docs_from_file(py_file)
        if items:
            all_items[py_file.relative_to(SRC_DIR).as_posix()] = items

    markdown = [
        "---",
        'okf_version: "0.1"',
        'title: "API Reference"',
        'type: "api_reference"',
        'description: "Auto-generated API reference from the UniGrok codebase."',
        "---",
        "",
        "# API Reference",
        "",
        "This deterministic reference is generated from documented public Python symbols.",
        "It is a source-code inventory, not the MCP `tools/list` contract: a Python",
        "symbol appearing here does not mean it is exposed by the stable HTTP service.",
        "Use live MCP discovery for the deployed surface. Topic guides label stable",
        "HTTP, contributor Forge, and trusted stdio capabilities explicitly.",
        "Run `uv run python scripts/generate_okf.py --write` after changing the public API.",
        "",
    ]

    for module, items in all_items.items():
        module_anchor = _anchor(module.removesuffix(".py"))
        markdown.extend((f"## {module} {{#{module_anchor}}}", ""))
        for item in items:
            item_anchor = f"{module_anchor}-{_anchor(item['name'])}"
            markdown.extend(
                (
                    f"### {item['type'].capitalize()}: `{item['name']}` {{#{item_anchor}}}",
                    "",
                    f"```python\n{item['signature']}\n```",
                    "",
                    f"**Keywords:** {item['keywords']}",
                    "",
                    item["docstring"],
                    "",
                )
            )
    return "\n".join(markdown).rstrip() + "\n"


def canonical_manifest() -> dict[str, Any]:
    """Return the canonical manifest with the generated reference registered once."""
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise GenerationError("okf-manifest.json must contain a string files array")
    data["files"] = list(dict.fromkeys([*files, "api-reference.md"]))
    return data


def _expected_files() -> dict[Path, str]:
    manifest_text = json.dumps(canonical_manifest(), indent=2) + "\n"
    expected = {
        API_REF_PATH: render_api_reference(),
        MANIFEST_PATH: manifest_text,
    }
    for source in sorted(OKF_DIR.iterdir()):
        if source.is_file() and source.name not in {API_REF_PATH.name, MANIFEST_PATH.name}:
            expected[PUBLIC_OKF_DIR / source.name] = source.read_text(encoding="utf-8")
    expected[PUBLIC_OKF_DIR / API_REF_PATH.name] = expected[API_REF_PATH]
    expected[PUBLIC_OKF_DIR / MANIFEST_PATH.name] = manifest_text
    return expected


def write_bundle() -> None:
    """Write the canonical generated files and synchronize the public static mirror."""
    expected = _expected_files()
    PUBLIC_OKF_DIR.mkdir(parents=True, exist_ok=True)
    for stale in PUBLIC_OKF_DIR.iterdir():
        if stale.is_file() and stale not in expected:
            stale.unlink()
        elif stale.is_dir():
            shutil.rmtree(stale)
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"Generated {API_REF_PATH.relative_to(ROOT_DIR)}")
    print(f"Synchronized {PUBLIC_OKF_DIR.relative_to(ROOT_DIR)}")


def check_bundle() -> None:
    """Fail when generated or mirrored OKF files are missing or stale."""
    failures: list[str] = []
    expected = _expected_files()
    for path, content in expected.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError:
            failures.append(f"missing {path.relative_to(ROOT_DIR)}")
            continue
        if actual != content:
            diff = "".join(
                difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=str(path.relative_to(ROOT_DIR)),
                    tofile=f"generated/{path.relative_to(ROOT_DIR)}",
                    n=2,
                )
            )
            failures.append(diff or f"stale {path.relative_to(ROOT_DIR)}")
    if PUBLIC_OKF_DIR.is_dir():
        expected_public = {path for path in expected if path.parent == PUBLIC_OKF_DIR}
        extra = sorted(path for path in PUBLIC_OKF_DIR.iterdir() if path not in expected_public)
        failures.extend(f"unexpected {path.relative_to(ROOT_DIR)}" for path in extra)
    if failures:
        raise GenerationError(
            "OKF bundle is stale; run `uv run python scripts/generate_okf.py --write`:\n"
            + "\n".join(failures)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate canonical and public files")
    mode.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)
    try:
        write_bundle() if args.write else check_bundle()
    except GenerationError as exc:
        print(f"OKF generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
