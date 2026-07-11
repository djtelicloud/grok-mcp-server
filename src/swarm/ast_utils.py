"""tree-sitter span extraction and byte-exact replacement (Python only, v1).

The mutation primitive is an exact byte-slice replacement, so the span
contract is load-bearing: `source[start:end]` must byte-equal the focus
node's full source INCLUDING decorators, and ambiguous focus references are
rejected at task start rather than discovered as corrupted files at apply.
"""

from __future__ import annotations

import ast
from typing import List, Optional, Tuple

import tree_sitter_python
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tree_sitter_python.language())


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def parse_ok(source: bytes) -> bool:
    """Syntax filter: True when tree-sitter parses without any error node.
    A syntax filter ONLY — import-time and collection failures are the
    sandbox stages' job."""
    tree = _parser().parse(source)
    return not tree.root_node.has_error


def _definition_span(node: Node) -> Tuple[int, int]:
    """Byte span of a definition INCLUDING its decorators."""
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        return parent.start_byte, parent.end_byte
    return node.start_byte, node.end_byte


def _functions_in(container: Node, name: bytes) -> List[Node]:
    """Direct function_definition children of `container` named `name`,
    looking through decorated_definition wrappers. Deliberately NOT
    recursive: nested defs belong to their enclosing function's span."""
    found: List[Node] = []
    for child in container.named_children:
        candidate = child
        if child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is None:
                continue
            candidate = inner
        if candidate.type != "function_definition":
            continue
        name_node = candidate.child_by_field_name("name")
        if name_node is not None and name_node.text == name:
            found.append(candidate)
    return found


def _classes_in(container: Node, name: bytes) -> List[Node]:
    found: List[Node] = []
    for child in container.named_children:
        candidate = child
        if child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is None:
                continue
            candidate = inner
        if candidate.type == "class_definition":
            name_node = candidate.child_by_field_name("name")
            if name_node is not None and name_node.text == name:
                found.append(candidate)
    return found


def extract_node_span(source: bytes, focus_node: str) -> Tuple[int, int]:
    """Resolve `focus_node` ("function:<name>" at module level, or
    "method:<Class>.<name>") to its exact byte span, decorators included.

    Raises ValueError on: unparseable source, malformed focus spec, missing
    node, or an AMBIGUOUS node (multiple same-named matches — e.g.
    conditional redefinitions) — a wrong span that still passes tests would
    corrupt adjacent code at apply, so ambiguity is fatal by design."""
    kind, _, spec = str(focus_node or "").partition(":")
    spec = spec.strip()
    if kind not in ("function", "method") or not spec:
        raise ValueError(
            f"focus_node must be 'function:<name>' or 'method:<Class>.<name>', got {focus_node!r}"
        )
    tree = _parser().parse(source)
    if tree.root_node.has_error:
        raise ValueError("target file does not parse cleanly")

    if kind == "function":
        matches = _functions_in(tree.root_node, spec.encode("utf-8"))
    else:
        class_name, _, fn_name = spec.partition(".")
        if not class_name or not fn_name:
            raise ValueError(f"method focus needs '<Class>.<name>', got {spec!r}")
        classes = _classes_in(tree.root_node, class_name.encode("utf-8"))
        if not classes:
            raise ValueError(f"class {class_name!r} not found at module level")
        if len(classes) > 1:
            raise ValueError(f"class {class_name!r} is ambiguous ({len(classes)} definitions)")
        body = classes[0].child_by_field_name("body")
        if body is None:
            raise ValueError(f"class {class_name!r} has no body")
        matches = _functions_in(body, fn_name.encode("utf-8"))

    if not matches:
        raise ValueError(f"focus node {focus_node!r} not found")
    if len(matches) > 1:
        raise ValueError(f"focus node {focus_node!r} is ambiguous ({len(matches)} definitions)")
    start, end = _definition_span(matches[0])
    if not (0 <= start < end <= len(source)):
        raise ValueError("resolved span is out of bounds")  # defensive; should not happen
    return start, end


def signature_fingerprint(source: bytes, focus_node: str) -> str:
    """Return a stable fingerprint for the focused callable's signature.

    The optimizer may change the body and decorators, but not sync/async kind
    or arguments. Tests rarely exercise every valid calling convention, so a
    passing suite alone is not enough to enforce this drop-in contract.
    """
    kind, _, spec = str(focus_node or "").partition(":")
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError("target file does not parse cleanly") from exc
    nodes: List[ast.AST]
    if kind == "function":
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == spec
        ]
    elif kind == "method" and "." in spec:
        class_name, fn_name = spec.split(".", 1)
        classes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        if len(classes) != 1:
            raise ValueError(f"class {class_name!r} is missing or ambiguous")
        nodes = [
            node for node in classes[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn_name
        ]
    else:
        raise ValueError(f"invalid focus node {focus_node!r}")
    if len(nodes) != 1:
        raise ValueError(f"focus node {focus_node!r} is missing or ambiguous")
    node = nodes[0]
    callable_kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
    return callable_kind + ":" + ast.dump(node.args, include_attributes=False)


def span_line_range(source: bytes, start: int, end: int) -> Tuple[int, int]:
    """1-based inclusive line range covered by a byte span (for coverage
    intersection in the preflight oracle check)."""
    first = source.count(b"\n", 0, start) + 1
    last = source.count(b"\n", 0, max(start, end - 1)) + 1
    return first, last


def apply_byte_replacement(source: bytes, start: int, end: int, replacement: bytes) -> bytes:
    """Exact byte splice: everything outside [start:end) is byte-identical."""
    if not (0 <= start <= end <= len(source)):
        raise ValueError("replacement span out of bounds")
    return source[:start] + replacement + source[end:]
