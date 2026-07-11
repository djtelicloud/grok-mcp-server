# tests/test_swarm_ast.py
# tree-sitter span extraction + byte-exact replacement invariants. The span
# contract is load-bearing: a wrong span that still passes tests corrupts
# adjacent code at apply, so decorators, methods, nested defs, and ambiguity
# all get explicit coverage.

from pathlib import Path

import pytest

from src.swarm.ast_utils import (
    apply_byte_replacement,
    extract_node_span,
    parse_ok,
    signature_fingerprint,
    span_line_range,
)

FIXTURE = Path(__file__).parent / "fixtures" / "swarm_target" / "slow_mod.py"


@pytest.fixture
def source():
    return FIXTURE.read_bytes()


class TestParseOk:
    def test_clean_source_parses(self, source):
        assert parse_ok(source) is True

    def test_syntax_error_detected(self):
        assert parse_ok(b"def f(:\n    pass\n") is False


class TestExtractSpan:
    def test_module_function(self, source):
        start, end = extract_node_span(source, "function:slow_sort")
        assert source[start:end].startswith(b"def slow_sort(items):")
        assert source[start:end].rstrip().endswith(b"return data")

    def test_span_includes_decorator(self, source):
        start, end = extract_node_span(source, "function:decorated_helper")
        assert source[start:end].startswith(b"@functools.lru_cache")

    def test_method_span(self, source):
        start, end = extract_node_span(source, "method:Widget.render")
        assert source[start:end].startswith(b"def render(self):")

    def test_nested_def_not_matched_at_module_level(self, source):
        # `inner` is nested inside `outer`; only `outer` is a module function.
        with pytest.raises(ValueError, match="not found"):
            extract_node_span(source, "function:inner")

    def test_module_and_method_same_name_disambiguated(self, source):
        # slow_sort exists both at module level and as Widget.slow_sort;
        # neither reference is ambiguous because they're different kinds.
        mod = extract_node_span(source, "function:slow_sort")
        meth = extract_node_span(source, "method:Widget.slow_sort")
        assert mod != meth
        assert source[mod[0]:mod[1]].startswith(b"def slow_sort(items):")
        assert source[meth[0]:meth[1]].startswith(b"def slow_sort(self, items):")

    def test_ambiguous_top_level_redefinition_is_fatal(self):
        # Two module-level defs of the same name (the second shadows the
        # first at runtime) — the span is genuinely ambiguous, so fatal.
        src = b"def g():\n    return 1\n\ndef g():\n    return 2\n"
        with pytest.raises(ValueError, match="ambiguous"):
            extract_node_span(src, "function:g")

    def test_malformed_focus_spec_rejected(self, source):
        with pytest.raises(ValueError, match="function:|method:"):
            extract_node_span(source, "slow_sort")

    def test_missing_node_rejected(self, source):
        with pytest.raises(ValueError, match="not found"):
            extract_node_span(source, "function:does_not_exist")

    def test_unparseable_source_rejected(self):
        with pytest.raises(ValueError, match="parse"):
            extract_node_span(b"def broken(:\n", "function:broken")


class TestByteReplacement:
    def test_splice_is_byte_identical_outside_span(self, source):
        start, end = extract_node_span(source, "function:slow_sort")
        replacement = b"def slow_sort(items):\n    return sorted(items)\n"
        patched = apply_byte_replacement(source, start, end, replacement)
        assert patched[:start] == source[:start]
        assert patched[start:start + len(replacement)] == replacement
        assert patched[start + len(replacement):] == source[end:]

    def test_round_trip_identity(self, source):
        start, end = extract_node_span(source, "method:Widget.render")
        assert apply_byte_replacement(source, start, end, source[start:end]) == source

    def test_out_of_bounds_rejected(self, source):
        with pytest.raises(ValueError, match="out of bounds"):
            apply_byte_replacement(source, 5, len(source) + 10, b"x")


class TestSignatureFingerprint:
    def test_body_changes_preserve_signature(self):
        a = b"def f(a, /, b=1, *, c=None):\n    return a\n"
        b = b"def f(a, /, b=1, *, c=None):\n    return b + c\n"
        assert signature_fingerprint(a, "function:f") == signature_fingerprint(
            b, "function:f"
        )

    def test_argument_or_async_change_is_detected(self):
        original = b"def f(a, b=1):\n    return a\n"
        changed = b"async def f(a, b=2):\n    return a\n"
        assert signature_fingerprint(original, "function:f") != signature_fingerprint(
            changed, "function:f"
        )


class TestSpanLineRange:
    def test_line_range_matches_content(self, source):
        start, end = extract_node_span(source, "function:slow_sort")
        first, last = span_line_range(source, start, end)
        lines = source.decode().splitlines()
        assert lines[first - 1].startswith("def slow_sort")
        assert "return data" in lines[last - 1]
