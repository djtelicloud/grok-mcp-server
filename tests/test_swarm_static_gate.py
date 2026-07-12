# tests/test_swarm_static_gate.py
# The $0 static fast-gate: ruff F821/F823 diagnostics (baseline-relative,
# degrade-to-no-op) and AST no-op mutant detection. Plus funnel wiring: a
# compiling-but-undefined-name mutant must die at the "lint" stage without
# ever booting the sandbox, and a formatting-only no-op must be a free
# discard.

import shutil
from pathlib import Path

import pytest

from src.swarm.ast_utils import extract_node_span, is_ast_identical
from src.swarm.engine import EngineConfig, SwarmEngine
from src.swarm.generate import GenerationResult
from src.swarm.preflight import run_preflight
from src.swarm.sandbox import SwarmSandbox
from src.swarm.static_gate import count_violations, ruff_bin, violation_counts
from src.swarm.ast_utils import span_line_range

FIXTURE = Path(__file__).parent / "fixtures" / "swarm_target"

# Compiles fine (NameError is runtime) but trips F821 — the exact
# hallucination class the gate exists for.
_UNDEFINED_NAME = "def slow_sort(items):\n    return sorted(resultz)\n"


# ─────────────────────────────────────────────────────────────────────────────
# count_violations
# ─────────────────────────────────────────────────────────────────────────────

class TestCountViolations:
    def test_ruff_is_a_pinned_dependency(self):
        assert ruff_bin() is not None, "ruff must ship with the project venv"

    @pytest.mark.asyncio
    async def test_clean_source_counts_zero(self):
        assert await count_violations(b"def f(x):\n    return x + 1\n") == 0

    @pytest.mark.asyncio
    async def test_undefined_name_counted(self):
        count = await count_violations(_UNDEFINED_NAME.encode())
        assert count is not None and count >= 1

    @pytest.mark.asyncio
    async def test_style_violations_do_not_count(self):
        # Ugly but correct code must pass: the gate is correctness-only, so
        # style rules never cull mutant diversity.
        ugly = b"import os\ndef f( x ):\n  y=x;  return  y\n"  # unused import, spacing
        assert await count_violations(ugly) == 0

    @pytest.mark.asyncio
    async def test_noqa_cannot_suppress_correctness_gate(self):
        source = b"def f():\n    return hallucinated  # noqa: F821\n"
        assert await count_violations(source) == 1

    @pytest.mark.asyncio
    async def test_missing_ruff_returns_none(self, monkeypatch):
        monkeypatch.setattr("src.swarm.static_gate.ruff_bin", lambda: None)
        assert await count_violations(b"def f(): pass\n") is None

    @pytest.mark.asyncio
    async def test_diagnostics_distinguish_equal_count_name_changes(self):
        old = await violation_counts(b"def f():\n    return old_missing\n")
        new = await violation_counts(b"def f():\n    return new_missing\n")
        assert old is not None and new is not None
        assert sum(old.values()) == sum(new.values()) == 1
        assert new - old


# ─────────────────────────────────────────────────────────────────────────────
# is_ast_identical
# ─────────────────────────────────────────────────────────────────────────────

class TestAstIdentical:
    ORIGINAL = b"def f(items):\n    total = 0\n    for i in items:\n        total += i\n    return total\n"

    def test_comment_and_whitespace_only_is_identical(self):
        variant = (
            b"def f(items):\n    # accumulate\n    total = 0\n\n"
            b"    for i in items:\n        total += i\n    return total\n"
        )
        assert is_ast_identical(self.ORIGINAL, variant) is True

    def test_real_change_is_not_identical(self):
        assert is_ast_identical(self.ORIGINAL, b"def f(items):\n    return sum(items)\n") is False

    def test_indented_method_span_handled(self):
        original = b"    def m(self):\n        return 1\n"
        same = b"    def m(self):  # noqa\n        return 1\n"
        assert is_ast_identical(original, same) is True

    def test_unparseable_returns_false_never_raises(self):
        assert is_ast_identical(b"def f(:\n", b"def f(:\n") is False


# ─────────────────────────────────────────────────────────────────────────────
# Funnel wiring (real sandbox, fake generator)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine_env(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE, ws)
    sb = SwarmSandbox(ws, tmp_path / "wr", "slow_mod.py")
    sb.create()
    yield sb
    sb.destroy()


async def _make_engine(sb, generator, **cfg):
    src = sb.read_target()
    start, end = extract_node_span(src, "function:slow_sort")
    oracle = await run_preflight(
        sb, target_rel="slow_mod.py", span_lines=span_line_range(src, start, end),
        test_target="test_slow.py", bench_argv=[sb.python_bin(), "bench_slow.py"],
        bench_repeats=3, eval_timeout=60.0, stage_budget_fraction=0.9,
    )
    cfg.setdefault("population", 2)
    cfg.setdefault("max_generations", 1)
    cfg.setdefault("seed", 1)
    cfg.setdefault("bench_repeats", 3)
    cfg.setdefault("eval_timeout", 60.0)
    return SwarmEngine(
        sandbox=sb, task_id="t-gate", focus_node="function:slow_sort",
        target_rel="slow_mod.py", test_target="test_slow.py",
        bench_argv=[sb.python_bin(), "bench_slow.py"], baseline=oracle["bench"],
        span=(start, end), original_span=src[start:end], file_source=src,
        config=EngineConfig(**cfg), generator=generator,
    )


class TestFunnelGate:
    @pytest.mark.asyncio
    async def test_undefined_name_dies_at_lint_without_sandbox(self, engine_env):
        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(_UNDEFINED_NAME, "CLI", 0.0, "final_answer")

        engine = await _make_engine(engine_env, gen)
        # Spy: the sandbox tests must never run for a lint-killed mutant.
        calls = []
        original_run_tests = engine_env.run_tests

        async def spy(*args, **kw):
            calls.append(args)
            return await original_run_tests(*args, **kw)

        engine_env.run_tests = spy
        outcomes = await engine.run()
        stages = [c["stage_reached"] for o in outcomes for c in o.candidates]
        assert stages and all(s == "lint" for s in stages)
        assert calls == []  # sandbox never booted for these mutants

    @pytest.mark.asyncio
    async def test_gate_disabled_falls_through_to_tests(self, engine_env):
        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(_UNDEFINED_NAME, "CLI", 0.0, "final_answer")

        engine = await _make_engine(engine_env, gen, ruff_filter=False)
        outcomes = await engine.run()
        stages = [c["stage_reached"] for o in outcomes for c in o.candidates]
        # Without the gate the tests stage still catches it — the gate only
        # ever saves time, never decides alone.
        assert stages and all(s == "tests" for s in stages)

    @pytest.mark.asyncio
    async def test_gate_noops_when_ruff_missing(self, engine_env, monkeypatch):
        monkeypatch.setattr("src.swarm.static_gate.ruff_bin", lambda: None)

        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(_UNDEFINED_NAME, "CLI", 0.0, "final_answer")

        engine = await _make_engine(engine_env, gen)
        outcomes = await engine.run()
        stages = [c["stage_reached"] for o in outcomes for c in o.candidates]
        assert stages and all(s == "tests" for s in stages)

    @pytest.mark.asyncio
    async def test_baseline_relative_preexisting_violation_not_fatal(self, engine_env):
        # Inject a pre-existing F821 into an UNRELATED function of the file:
        # the baseline count rises with it, so a clean mutant must pass.
        src = engine_env.read_target()
        polluted = src + b"\n\ndef broken_helper():\n    return missing_name\n"
        engine_env.write_target(polluted)

        fast = "def slow_sort(items):\n    return sorted(list(items))\n"

        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(fast, "CLI", 0.0, "final_answer")

        src2 = engine_env.read_target()
        start, end = extract_node_span(src2, "function:slow_sort")
        engine = SwarmEngine(
            sandbox=engine_env, task_id="t-rel", focus_node="function:slow_sort",
            target_rel="slow_mod.py", test_target="test_slow.py",
            bench_argv=[engine_env.python_bin(), "bench_slow.py"],
            baseline={"latency_ms": 5.0, "latency_samples": [5.0, 5.0, 5.0]},
            span=(start, end), original_span=src2[start:end], file_source=src2,
            config=EngineConfig(population=1, max_generations=1, seed=1,
                                bench_repeats=3, eval_timeout=60.0),
            generator=gen,
        )
        outcomes = await engine.run()
        stages = [c["stage_reached"] for o in outcomes for c in o.candidates]
        assert stages and all(s != "lint" for s in stages)

    @pytest.mark.asyncio
    async def test_equal_count_different_violation_is_fatal(self, engine_env):
        # A total-count comparison would let the mutant trade old_missing for
        # new_missing. Diagnostic multiset comparison must reject the new name.
        original = b"def slow_sort(items):\n    return sorted(old_missing)\n"
        engine_env.write_target(original)
        start, end = extract_node_span(original, "function:slow_sort")

        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(
                "def slow_sort(items):\n    return sorted(new_missing)\n",
                "CLI", 0.0, "final_answer",
            )

        engine = SwarmEngine(
            sandbox=engine_env, task_id="t-trade", focus_node="function:slow_sort",
            target_rel="slow_mod.py", test_target="test_slow.py",
            bench_argv=[engine_env.python_bin(), "bench_slow.py"],
            baseline={"latency_ms": 5.0, "latency_samples": [5.0, 5.0, 5.0]},
            span=(start, end), original_span=original[start:end], file_source=original,
            config=EngineConfig(population=1, max_generations=1, seed=1,
                                bench_repeats=3, eval_timeout=60.0),
            generator=gen,
        )
        outcomes = await engine.run()
        stages = [c["stage_reached"] for o in outcomes for c in o.candidates]
        assert stages == ["lint"]

    @pytest.mark.asyncio
    async def test_formatting_noop_mutant_is_free_discard(self, engine_env):
        src = engine_env.read_target()
        start, end = extract_node_span(src, "function:slow_sort")
        # Same AST, different bytes (added comment) → distinct hash, no-op AST.
        noop = src[start:end].decode().replace(
            "def slow_sort(items):", "def slow_sort(items):\n    # cosmetic"
        )

        async def gen(prompt, system, *, remaining_budget_usd, **kw):
            return GenerationResult(noop, "CLI", 0.0, "final_answer")

        engine = await _make_engine(engine_env, gen)
        outcomes = await engine.run()
        # Discarded like a duplicate: no candidate rows at all.
        assert all(not o.candidates for o in outcomes)
