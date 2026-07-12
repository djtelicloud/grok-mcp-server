"""Small closed registry of deterministic AST candidate generators.

These are candidates, not trusted rewrites: every result still passes the
same signature, static, test, and benchmark funnel as an LLM mutation.
"""

from __future__ import annotations

import ast
import textwrap
from typing import List, Tuple


class _ConstantBranchReducer(ast.NodeTransformer):
    changed = False

    def visit_If(self, node: ast.If):  # noqa: N802 - ast visitor contract
        node = self.generic_visit(node)
        if isinstance(node.test, ast.Constant) and isinstance(node.test.value, bool):
            self.changed = True
            return node.body if node.test.value else node.orelse
        return node


class _AppendLoopToComprehension(ast.NodeTransformer):
    changed = False

    def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
        node.body = self._rewrite_body(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # noqa: N802
        node.body = self._rewrite_body(node.body)
        return node

    def _rewrite_body(self, body):
        rewritten = []
        index = 0
        while index < len(body):
            if index + 1 < len(body):
                assignment, loop = body[index], body[index + 1]
                replacement = self._match(assignment, loop)
                if replacement is not None:
                    rewritten.append(replacement)
                    self.changed = True
                    index += 2
                    continue
            rewritten.append(self.generic_visit(body[index]))
            index += 1
        return rewritten

    def _match(self, assignment, loop):
        if not (
            isinstance(assignment, ast.Assign)
            and len(assignment.targets) == 1
            and isinstance(assignment.targets[0], ast.Name)
            and isinstance(assignment.value, ast.List)
            and not assignment.value.elts
            and isinstance(loop, ast.For)
            and len(loop.body) == 1
            and not loop.orelse
        ):
            return None
        statement = loop.body[0]
        conditions = []
        if isinstance(statement, ast.If) and len(statement.body) == 1 and not statement.orelse:
            conditions = [statement.test]
            statement = statement.body[0]
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and not statement.value.keywords
            and len(statement.value.args) == 1
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "append"
            and isinstance(statement.value.func.value, ast.Name)
            and statement.value.func.value.id == assignment.targets[0].id
        ):
            return None
        output_name = assignment.targets[0].id
        # A comprehension cannot reference the list while constructing it.
        # The classic ordered-dedup loop checks ``item not in result`` and is
        # intentionally rejected instead of emitting a guaranteed F821.
        if any(
            isinstance(item, ast.Name) and item.id == output_name
            for expression in [statement.value.args[0], *conditions]
            for item in ast.walk(expression)
        ):
            return None
        comprehension = ast.ListComp(
            elt=statement.value.args[0],
            generators=[
                ast.comprehension(
                    target=loop.target,
                    iter=loop.iter,
                    ifs=conditions,
                    is_async=0,
                )
            ],
        )
        return ast.copy_location(
            ast.Assign(targets=assignment.targets, value=comprehension), assignment
        )


class _ComprehensionToAppendLoop(ast.NodeTransformer):
    changed = False

    def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
        node.body = self._rewrite_body(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # noqa: N802
        node.body = self._rewrite_body(node.body)
        return node

    def _rewrite_body(self, body):
        rewritten = []
        for statement in body:
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.ListComp)
                and len(statement.value.generators) == 1
            ):
                rewritten.append(self.generic_visit(statement))
                continue
            generator = statement.value.generators[0]
            if generator.is_async:
                rewritten.append(statement)
                continue
            name = statement.targets[0].id
            initial = ast.copy_location(
                ast.Assign(targets=statement.targets, value=ast.List(elts=[], ctx=ast.Load())),
                statement,
            )
            append = ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(id=name, ctx=ast.Load()), attr="append", ctx=ast.Load()),
                    args=[statement.value.elt],
                    keywords=[],
                )
            )
            loop_body = [append]
            for condition in reversed(generator.ifs):
                loop_body = [ast.If(test=condition, body=loop_body, orelse=[])]
            loop = ast.For(
                target=generator.target,
                iter=generator.iter,
                body=loop_body,
                orelse=[],
                type_comment=None,
            )
            rewritten.extend([initial, ast.copy_location(loop, statement)])
            self.changed = True
        return rewritten


_REGISTRY = (
    ("append_loop_to_listcomp", _AppendLoopToComprehension),
    ("listcomp_to_append_loop", _ComprehensionToAppendLoop),
    ("constant_branch_reduction", _ConstantBranchReducer),
)


def deterministic_transforms(source: str) -> List[Tuple[str, str]]:
    """Return unique named rewrites of one definition, in registry order."""
    leading = ""
    for line in source.splitlines():
        if line.strip():
            leading = line[: len(line) - len(line.lstrip())]
            break
    try:
        ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return []
    outputs: List[Tuple[str, str]] = []
    seen = {source.strip()}
    for name, transformer_type in _REGISTRY:
        candidate_tree = ast.parse(textwrap.dedent(source))
        transformer = transformer_type()
        transformed = transformer.visit(candidate_tree)
        if not transformer.changed:
            continue
        ast.fix_missing_locations(transformed)
        rendered = ast.unparse(transformed).strip()
        if leading:
            rendered = textwrap.indent(rendered, leading)
        if rendered not in seen:
            seen.add(rendered)
            outputs.append((name, rendered))
    return outputs
