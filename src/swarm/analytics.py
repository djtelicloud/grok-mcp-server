"""Deterministic, non-executing Python analytics for the Swarm target picker.

This module parses source as data. It never imports the submitted module,
opens a network connection, or executes user code. Optional Ruff analysis is
an isolated linter subprocess over stdin; only aggregate rule counts survive.
"""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
import io
import json
from pathlib import Path
import shutil
import sys
import tokenize
from typing import Any, Dict, Iterable, List, Optional

from ..utils import redact_secrets

MAX_SOURCE_BYTES = 256 * 1024
_CONTROL_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def _byte_offset(lines: List[bytes], line: int, column: int) -> int:
    return sum(len(value) for value in lines[: max(0, line - 1)]) + column


def _node_span(node: ast.AST, source_lines: List[bytes]) -> tuple[int, int]:
    decorators = getattr(node, "decorator_list", [])
    first = min([node, *decorators], key=lambda item: (item.lineno, item.col_offset))
    return (
        _byte_offset(source_lines, first.lineno, first.col_offset),
        _byte_offset(source_lines, node.end_lineno, node.end_col_offset),
    )


def _children_without_nested_scopes(node: ast.AST) -> Iterable[ast.AST]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _children_without_nested_scopes(child)


def _complexity(node: ast.AST) -> tuple[int, int, int]:
    branch_points = 0
    max_nesting = 0

    def walk(current: ast.AST, depth: int) -> None:
        nonlocal branch_points, max_nesting
        for child in ast.iter_child_nodes(current):
            if child is not node and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            increment = 0
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                branch_points += 1
                increment = 1
            elif isinstance(child, (ast.Try, ast.TryStar)):
                branch_points += len(child.handlers)
                increment = 1
            elif isinstance(child, ast.BoolOp):
                branch_points += max(0, len(child.values) - 1)
            elif isinstance(child, ast.IfExp):
                branch_points += 1
            elif isinstance(child, ast.Match):
                branch_points += len(child.cases)
                increment = 1
            elif isinstance(child, ast.comprehension):
                branch_points += len(child.ifs) + (1 if child.is_async else 0)
                increment = 1
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                increment = 1
            next_depth = depth + increment
            max_nesting = max(max_nesting, next_depth)
            walk(child, next_depth)

    walk(node, 0)
    return 1 + branch_points, branch_points, max_nesting


def _focus_node(path: List[ast.AST], node: ast.AST) -> str:
    class_names = [item.name for item in path if isinstance(item, ast.ClassDef)]
    function_names = [
        item.name for item in path if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if class_names and not function_names:
        return f"method:{class_names[-1]}.{node.name}"
    if class_names and function_names:
        return f"method:{class_names[-1]}." + ".".join([*function_names, node.name])
    return "function:" + ".".join([*function_names, node.name])


def _function_inventory(tree: ast.AST, source_lines: List[bytes]) -> List[Dict[str, Any]]:
    functions: List[Dict[str, Any]] = []

    def visit(container: ast.AST, path: List[ast.AST]) -> None:
        for child in ast.iter_child_nodes(container):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = _node_span(child, source_lines)
                complexity, branches, nesting = _complexity(child)
                functions.append(
                    {
                        "focus_node": _focus_node(path, child),
                        "name": child.name,
                        "kind": "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function",
                        "span": [start, end],
                        "line_start": min(
                            [child.lineno, *[item.lineno for item in child.decorator_list]]
                        ),
                        "line_end": child.end_lineno,
                        "loc": child.end_lineno - child.lineno + 1,
                        "parameters": len(child.args.posonlyargs)
                        + len(child.args.args)
                        + len(child.args.kwonlyargs)
                        + int(child.args.vararg is not None)
                        + int(child.args.kwarg is not None),
                        "cyclomatic_complexity": complexity,
                        "branch_points": branches,
                        "max_nesting": nesting,
                    }
                )
                visit(child, [*path, child])
            elif isinstance(child, ast.ClassDef):
                visit(child, [*path, child])

    visit(tree, [])
    functions.sort(key=lambda item: (item["span"][0], item["focus_node"]))
    return functions


def _imports_and_unused(tree: ast.AST) -> tuple[List[str], List[str]]:
    modules: set[str] = set()
    bindings: set[str] = set()
    loaded = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                bindings.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            modules.add(("." * node.level) + (node.module or ""))
            for alias in node.names:
                if alias.name != "*":
                    bindings.add(alias.asname or alias.name)
    return sorted(modules), sorted(bindings - loaded)


def _private_dead_names(tree: ast.AST) -> List[str]:
    assigned: set[str] = set()
    loaded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id.startswith("_"):
                assigned.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
    return sorted(assigned - loaded)


def _duplication(source: str, window: int = 8) -> Dict[str, int]:
    try:
        tokens = [
            item.string
            for item in tokenize.generate_tokens(io.StringIO(source).readline)
            if item.type
            not in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.COMMENT,
            }
        ]
    except (tokenize.TokenError, IndentationError):
        return {"window_tokens": window, "duplicate_windows": 0, "duplicated_tokens": 0}
    counts = Counter(tuple(tokens[index : index + window]) for index in range(len(tokens) - window + 1))
    duplicates = [count for count in counts.values() if count > 1]
    return {
        "window_tokens": window,
        "duplicate_windows": len(duplicates),
        "duplicated_tokens": sum((count - 1) * window for count in duplicates),
    }


def analyze_python_source(source: str) -> Dict[str, Any]:
    """Return measured-only AST analytics or a structured parse error."""
    if not isinstance(source, str):
        raise TypeError("code must be a string")
    encoded = source.encode("utf-8")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError(f"code exceeds the {MAX_SOURCE_BYTES // 1024} KiB analysis cap")
    secret_warning = redact_secrets(source) != source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return {
            "format": "unigrok-swarm-analytics-v1",
            "language": "python",
            "source": "paste",
            "parse_ok": False,
            "parse_error": {
                "line": getattr(exc, "lineno", None),
                "column": getattr(exc, "offset", None),
                "message": str(getattr(exc, "msg", exc))[:200],
            },
            "secret_warning": secret_warning,
            "functions": [],
            "searchability": {"ready": False, "blockers": ["parse_error"]},
        }

    lines = encoded.splitlines(keepends=True)
    if not lines or not encoded.endswith((b"\n", b"\r")):
        lines.append(b"")
    functions = _function_inventory(tree, lines)
    modules, unused_imports = _imports_and_unused(tree)
    blockers = [] if functions else ["no_functions"]
    if secret_warning:
        blockers.append("secret_like_source")
    return {
        "format": "unigrok-swarm-analytics-v1",
        "language": "python",
        "source": "paste",
        "parse_ok": True,
        "bytes": len(encoded),
        "loc": len(source.splitlines()),
        "secret_warning": secret_warning,
        "functions": functions,
        "imports": modules,
        "dead_code": {
            "unused_imports": unused_imports,
            "unreferenced_private_names": _private_dead_names(tree),
        },
        "duplication": _duplication(source),
        "searchability": {"ready": False, "blockers": [*blockers, "missing_tests", "missing_benchmark"]},
        "tooling": {"python_ast": f"{sys.version_info.major}.{sys.version_info.minor}"},
    }


def _ruff_binary() -> Optional[str]:
    sibling = Path(sys.executable).parent / "ruff"
    return str(sibling) if sibling.is_file() else shutil.which("ruff")


async def add_ruff_summary(source: str, analytics: Dict[str, Any]) -> Dict[str, Any]:
    """Attach isolated Ruff aggregate counts without returning source excerpts."""
    binary = _ruff_binary()
    if not binary or not analytics.get("parse_ok"):
        analytics["ruff"] = {"available": False, "counts_by_code": {}}
        return analytics
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "check",
            "--isolated",
            "--output-format",
            "json",
            "--stdin-filename",
            "pasted.py",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await asyncio.wait_for(proc.communicate(source.encode()), timeout=10.0)
        if proc.returncode not in (0, 1):
            raise ValueError("ruff failed")
        findings = json.loads(output.decode("utf-8") or "[]")
        counts = Counter(
            item.get("code") for item in findings if isinstance(item, dict) and item.get("code")
        )
        analytics["ruff"] = {"available": True, "counts_by_code": dict(sorted(counts.items()))}
        analytics.setdefault("tooling", {})["ruff"] = "isolated"
    except (OSError, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
        analytics["ruff"] = {"available": False, "counts_by_code": {}}
    return analytics


async def analyze_python_source_full(source: str) -> Dict[str, Any]:
    return await add_ruff_summary(source, analyze_python_source(source))
