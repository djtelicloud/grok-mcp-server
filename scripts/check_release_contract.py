#!/usr/bin/env python3
"""Fail when the public release version drifts across shipping surfaces."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _match(path: str, pattern: str) -> str | None:
    found = re.search(pattern, _text(path), flags=re.MULTILINE)
    return found.group(1) if found else None


def main() -> int:
    project = tomllib.loads(_text("pyproject.toml"))
    expected = str(project["project"]["version"])
    observed: dict[str, list[str | None]] = {
        "src/unigrok_public/__init__.py": [
            _match("src/unigrok_public/__init__.py", r'^__version__\s*=\s*"([^"]+)"')
        ],
        "README.md badge": [
            _match("README.md", r"shields\.io/badge/version-([0-9][0-9A-Za-z.-]*)-")
        ],
        "compose.yaml images": re.findall(
            r"^\s*image:\s*(?:\$\{UNIGROK_IMAGE:-)?unigrok:([^}\s]+)\}?",
            _text("compose.yaml"),
            flags=re.MULTILINE,
        ),
        "docs/reference.md": [
            _match("docs/reference.md", r"all report version `([^`]+)`")
        ],
        "scripts/smoke_mcp.py": [
            _match("scripts/smoke_mcp.py", r'^EXPECTED_VERSION\s*=\s*"([^"]+)"')
        ],
    }

    lock = tomllib.loads(_text("uv.lock"))
    root_versions = [
        str(package.get("version"))
        for package in lock.get("package", [])
        if package.get("name") == project["project"]["name"]
    ]
    observed["uv.lock root package"] = root_versions

    failures: list[str] = []
    for surface, versions in observed.items():
        if not versions:
            failures.append(f"{surface}: version marker missing")
            continue
        mismatches = [version for version in versions if version != expected]
        if mismatches:
            failures.append(f"{surface}: expected {expected}, found {versions}")

    compose_variables = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", _text("compose.yaml")))
    example_variables = set(
        re.findall(
            r"^\s*#?\s*([A-Z][A-Z0-9_]*)=",
            _text("example.env"),
            flags=re.MULTILINE,
        )
    )
    missing_examples = sorted(compose_variables - example_variables)
    if missing_examples:
        failures.append(
            "example.env: missing Compose variables " + ", ".join(missing_examples)
        )

    if failures:
        print("release-contract: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"release-contract: OK ({expected}; {len(observed)} surfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
