"""Swarm generation loop over one focus node.

``baseline_batch`` keeps every LLM mutant rooted at the original span.
``elite_offspring`` allocates elite children, baseline immigrants, and closed
deterministic AST candidates; selected genetic parents are recorded by id and
code hash. Elites also feed the bounded folded prompt context.

The loop is: route arms -> generate mutants (CLI plane) -> funnel each
(dedupe/ast-noop -> parse[+heal] -> signature -> compile -> lint -> tests ->
bench -> restore+hygiene) ->
Pareto-select -> reward the arms -> fold state -> stop check. Generation is the
one mockable seam (generate.generate_mutation); everything else is real.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .ast_utils import (
    apply_byte_replacement,
    is_ast_identical,
    parse_ok,
    signature_fingerprint,
)
from .fold import build_folded_state
from .generate import BudgetExceeded, generate_mutation
from .mutators import (
    HEAL_SUFFIX,
    build_mutation_prompt,
    build_system_prompt,
    parse_mutation_output,
)
from .pareto import rank_candidates, select_champion
from .preflight import noise_floor_pct
from .router import DiscountedUCBRouter, reward_for
from .sandbox import SandboxError, SwarmSandbox
from .static_gate import violation_counts
from .transforms import deterministic_transforms


@dataclass
class EngineConfig:
    population: int = 4
    max_generations: int = 6
    max_concurrent_gen: int = 2
    bench_repeats: int = 5
    eval_timeout: float = 120.0
    budget_usd: float = 2.0
    seed: int = 0
    allow_unstable_bench: bool = False
    # $0 ruff F821/F823 gate between compile() and the sandbox stages
    # (baseline-relative; no-ops when ruff is unavailable).
    ruff_filter: bool = True
    search_strategy: str = "baseline_batch"
    primary_goal: str = "balanced"


@dataclass
class GenerationOutcome:
    generation: int
    candidates: List[Dict[str, Any]]
    new_front_point: bool
    spent_usd: float
    folded_state: str


@dataclass
class SwarmEngine:
    sandbox: SwarmSandbox
    task_id: str
    focus_node: str
    target_rel: str
    test_target: str
    bench_argv: List[str]
    baseline: Dict[str, Any]
    span: tuple  # (byte_start, byte_end)
    original_span: bytes
    file_source: bytes
    config: EngineConfig
    goal: str = ""
    # Injected so the runner can persist rows / heartbeat / check cancel.
    on_candidate: Optional[Callable[[Dict[str, Any]], Any]] = None
    cancelled: Optional[Callable[[], bool]] = None
    # None => the module-level generate_mutation, resolved at call time so
    # tests can monkeypatch src.swarm.engine.generate_mutation.
    generator: Optional[Callable] = None

    router: DiscountedUCBRouter = field(init=False)
    _seen_hashes: set = field(default_factory=set, init=False)
    _front: List[Dict[str, Any]] = field(default_factory=list, init=False)
    _spent: float = field(default=0.0, init=False)
    _noise_floor_pct: float = field(default=5.0, init=False)
    # Baseline F821/F823 diagnostics, computed once on first use ("unset" sentinel;
    # None = ruff unavailable, gate disabled for the task).
    _baseline_lint: Any = field(default="unset", init=False)
    _evolution_rng: random.Random = field(init=False)

    def __post_init__(self):
        self.router = DiscountedUCBRouter(seed=self.config.seed)
        self._evolution_rng = random.Random(self.config.seed ^ 0xE10FF5)
        self._noise_floor_pct = noise_floor_pct(
            self.baseline.get("latency_samples") or [self.baseline.get("latency_ms", 0.0)]
        )

    # ── Public driver ────────────────────────────────────────────────────────

    async def run(self) -> List[GenerationOutcome]:
        outcomes: List[GenerationOutcome] = []
        stagnant = 0
        for generation in range(1, self.config.max_generations + 1):
            if self.cancelled and self.cancelled():
                break
            outcome = await self._run_generation(generation)
            outcomes.append(outcome)
            if outcome.new_front_point:
                stagnant = 0
            else:
                stagnant += 1
            if stagnant >= 2:
                break
            if self._spent >= self.config.budget_usd > 0:
                break
        return outcomes

    @property
    def front(self) -> List[Dict[str, Any]]:
        return list(self._front)

    @property
    def spent_usd(self) -> float:
        return self._spent

    # ── One generation ───────────────────────────────────────────────────────

    async def _run_generation(self, generation: int) -> GenerationOutcome:
        picks = self._generation_picks(generation)
        semaphore = asyncio.Semaphore(self.config.max_concurrent_gen)

        async def _gen(pick):
            if pick.get("origin") == "ast":
                return pick.get("replacement")
            async with semaphore:
                return await self._generate_one(pick, generation)

        generation_tasks = [asyncio.create_task(_gen(p)) for p in picks]
        try:
            generated = await asyncio.gather(*generation_tasks)
        except BaseException:
            # asyncio.gather propagates the first failure without cancelling
            # siblings. A plane/cost violation must not leave concurrent
            # provider calls running after the durable task is marked failed.
            for task in generation_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*generation_tasks, return_exceptions=True)
            raise

        candidates: List[Dict[str, Any]] = []
        # Funnel is sequential per candidate (shared work dir); generation was
        # concurrent above.
        for pick, raw in zip(picks, generated):
            if self.cancelled and self.cancelled():
                break
            candidate = await self._funnel(pick, raw, generation)
            if candidate is not None:
                candidates.append(candidate)

        # Select across THIS generation's feasible candidates plus the running
        # front, so elites persist.
        pool = candidates + self._front
        ranked = rank_candidates(pool)
        new_front = [c for c in ranked if c.get("pareto_rank") == 0]
        new_point = self._front_grew(new_front)
        self._front = new_front

        # Reward arms with the funnel-aligned level.
        front_ids = {id(c) for c in new_front}
        for candidate in candidates:
            on_front = id(candidate) in front_ids
            reward = reward_for(
                candidate["stage_reached"], bool(candidate.get("feasible")), on_front
            )
            candidate["reward"] = reward
            if candidate.get("origin") == "llm":
                self.router.update(candidate["mutator"], reward)
            candidate["pareto_rank"] = candidate.get("pareto_rank")
            candidate["crowding"] = candidate.get("crowding")
            if self.on_candidate is not None:
                await _maybe_await(self.on_candidate(candidate))

        folded = build_folded_state(
            goal=self.goal or f"optimize {self.focus_node}",
            target_path=self.target_rel,
            test_target=self.test_target,
            bench_command=" ".join(self.bench_argv),
            candidates=pool,
            front_size=len(new_front),
            best_delta_pct=self._best_delta_pct(new_front),
            generation=generation,
        )
        return GenerationOutcome(
            generation=generation,
            candidates=candidates,
            new_front_point=new_point,
            spent_usd=self._spent,
            folded_state=folded,
        )

    # ── Generation + funnel ──────────────────────────────────────────────────

    async def _generate_one(self, pick: Dict[str, Any], generation: int) -> Optional[str]:
        parent_source = str(pick.get("parent_code") or "") or self.original_span.decode(
            "utf-8", errors="replace"
        )
        prompt = build_mutation_prompt(
            arm=pick["arm"],
            focus_node=self.focus_node,
            original_span=parent_source,
            byte_start=self.span[0],
            byte_end=self.span[1],
            file_excerpt=self.file_source.decode("utf-8", errors="replace")[:8000],
            tests_excerpt=self._tests_excerpt(),
            folded_state=self._current_fold,
        )
        system = build_system_prompt()
        generator = self.generator or generate_mutation
        remaining = max(0.0, self.config.budget_usd - self._spent)
        result = await generator(prompt, system, remaining_budget_usd=remaining)
        self._spent += self._validated_generation_cost(result)
        text = parse_mutation_output(getattr(result, "text", ""))
        if text is None:
            # One heal retry, restating the contract.
            remaining = max(0.0, self.config.budget_usd - self._spent)
            healed = await generator(
                prompt + HEAL_SUFFIX, system, remaining_budget_usd=remaining
            )
            self._spent += self._validated_generation_cost(healed)
            text = parse_mutation_output(getattr(healed, "text", ""))
        return text

    @staticmethod
    def _validated_generation_cost(result: Any) -> float:
        """Fail closed before malformed accounting can poison durable spend."""
        try:
            cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise BudgetExceeded("swarm generation returned an invalid cost") from exc
        if not math.isfinite(cost) or cost != 0.0:
            raise BudgetExceeded(
                "swarm generation violated the exact-zero CLI cost contract "
                f"(cost={cost!r})"
            )
        return cost

    async def _funnel(
        self, pick: Dict[str, Any], replacement: Optional[str], generation: int
    ) -> Optional[Dict[str, Any]]:
        arm = pick["arm"]
        safe_arm = str(arm).replace(":", "-")
        candidate: Dict[str, Any] = {
            "id": f"{self.task_id}-g{generation}-s{pick['slot']}-{safe_arm}",
            "task_id": self.task_id,
            "parent_id": pick.get("parent_id"),
            "parent_code_hash": pick.get("parent_code_hash"),
            "generation": generation,
            "mutator": arm,
            "plane": "local" if pick.get("origin") == "ast" else "CLI",
            "origin": pick.get("origin") or "llm",
            "transform": pick.get("transform"),
            "byte_start": self.span[0],
            "byte_end": self.span[1],
            "arm_receipt": pick["receipt"],
            "feasible": False,
            "stage_reached": "generated",
        }
        if not replacement:
            candidate["stage_reached"] = "parse"
            return candidate

        replacement_bytes = replacement.encode("utf-8")
        code_hash = hashlib.sha256(replacement_bytes).hexdigest()[:24]
        if code_hash in self._seen_hashes:
            return None  # free discard, not persisted
        self._seen_hashes.add(code_hash)
        if is_ast_identical(self.original_span, replacement_bytes):
            # Formatting/comment-only no-op: evaluating it would re-measure
            # the baseline. Discarded free like a duplicate (no row, no
            # reward — symmetric with the hash dedupe above).
            return None
        candidate["code"] = replacement
        candidate["code_hash"] = code_hash
        candidate["diff_bytes"] = _byte_diff_size(self.original_span, replacement_bytes)

        patched = apply_byte_replacement(
            self.file_source, self.span[0], self.span[1], replacement_bytes
        )
        if not parse_ok(patched):
            candidate["stage_reached"] = "parse"
            return candidate
        if signature_fingerprint(patched, self.focus_node) != signature_fingerprint(
            self.file_source, self.focus_node
        ):
            candidate["stage_reached"] = "signature"
            return candidate
        try:
            compile(patched, self.target_rel, "exec")
        except SyntaxError:
            candidate["stage_reached"] = "compile"
            return candidate

        # $0 static gate: compile() accepts undefined names (NameError is a
        # runtime error), so ruff's F821/F823 catch that hallucination class
        # in milliseconds before seconds of sandbox time. Baseline-relative:
        # only NEW violations vs the original file kill a candidate, and an
        # unavailable/erroring ruff makes the gate a no-op — the tests stage
        # still catches everything this would have.
        if self.config.ruff_filter:
            baseline_lint = await self._baseline_lint_counts()
            if baseline_lint is not None:
                mutant_lint = await violation_counts(patched)
                # Compare diagnostic multisets, not just totals: replacing a
                # pre-existing undefined name with a different hallucinated
                # name must still be rejected even when the count is equal.
                if mutant_lint is not None and mutant_lint - baseline_lint:
                    candidate["stage_reached"] = "lint"
                    return candidate

        # Sandbox stages: write -> tests -> bench -> restore + hygiene.
        try:
            self.sandbox.write_target(patched)
            passed, _out = await self.sandbox.run_tests(
                self.test_target, timeout=self.config.eval_timeout
            )
            candidate["stage_reached"] = "tests"
            if not passed:
                return candidate
            try:
                bench = await self.sandbox.run_bench(
                    self.bench_argv, self.config.bench_repeats, self.config.eval_timeout
                )
            except SandboxError:
                candidate["stage_reached"] = "bench"
                return candidate
            candidate["stage_reached"] = "bench"
            candidate["latency_ms"] = self._apply_noise_floor(bench["latency_ms"])
            candidate["peak_mem_bytes"] = bench["peak_mem_bytes"]
            candidate["feasible"] = True
        finally:
            self.sandbox.write_target(self.file_source)
            self.sandbox.hygiene()
        return candidate

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _generation_picks(self, generation: int) -> List[Dict[str, Any]]:
        if self.config.search_strategy != "elite_offspring":
            picks = [self.router.select(generation) for _ in range(self.config.population)]
            return [
                self._decorate_pick(pick, slot=index + 1, role="baseline_immigrant")
                for index, pick in enumerate(picks)
            ]

        population = self.config.population
        offspring_count = (population + 1) // 2
        immigrant_count = 0 if population == 1 else max(1, population // 4)
        ast_count = max(0, population - offspring_count - immigrant_count)
        picks: List[Dict[str, Any]] = []
        slot = 1
        for _ in range(offspring_count):
            parent, reason = self._select_parent()
            role = "elite_offspring" if parent else "elite_slot_baseline"
            picks.append(
                self._decorate_pick(
                    self.router.select(generation),
                    slot=slot,
                    role=role,
                    parent=parent,
                    parent_reason=reason,
                )
            )
            slot += 1
        for _ in range(immigrant_count):
            picks.append(
                self._decorate_pick(
                    self.router.select(generation), slot=slot, role="baseline_immigrant"
                )
            )
            slot += 1
        transforms = deterministic_transforms(
            self.original_span.decode("utf-8", errors="replace")
        )
        for index in range(ast_count):
            selected = transforms[(generation + index - 1) % len(transforms)] if transforms else None
            transform_name, replacement = selected if selected else ("no_applicable_transform", None)
            receipt = json.dumps(
                {
                    "origin": "ast",
                    "role": "deterministic_transform",
                    "transform": transform_name,
                    "generation": generation,
                    "slot": slot,
                },
                separators=(",", ":"),
            )
            picks.append(
                {
                    "arm": f"ast:{transform_name}",
                    "step": slot,
                    "slot": slot,
                    "receipt": receipt,
                    "origin": "ast",
                    "transform": transform_name,
                    "replacement": replacement,
                    "parent_id": None,
                    "parent_code_hash": None,
                }
            )
            slot += 1
        return picks

    def _decorate_pick(
        self,
        pick: Dict[str, Any],
        *,
        slot: int,
        role: str,
        parent: Optional[Dict[str, Any]] = None,
        parent_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        decorated = dict(pick)
        decorated.update(
            {
                "slot": slot,
                "origin": "llm",
                "transform": None,
                "parent_id": parent.get("id") if parent else None,
                "parent_code_hash": parent.get("code_hash") if parent else None,
                "parent_code": parent.get("code") if parent else None,
            }
        )
        receipt = json.loads(str(pick["receipt"]))
        receipt.update(
            {
                "origin": "llm",
                "role": role,
                "slot": slot,
                "parent_id": decorated["parent_id"],
                "parent_policy": parent_reason,
            }
        )
        decorated["receipt"] = json.dumps(receipt, separators=(",", ":"))
        return decorated

    def _select_parent(self) -> tuple[Optional[Dict[str, Any]], str]:
        if not self._front:
            return None, "baseline_no_front"
        if self._evolution_rng.random() < 0.15:
            return self._evolution_rng.choice(self._front), "epsilon_front_uniform"
        if len(self._front) == 1:
            return self._front[0], "tournament_k2_singleton"
        contenders = self._evolution_rng.sample(self._front, 2)
        return (
            select_champion(contenders, self.config.primary_goal),
            "tournament_k2_primary_goal",
        )

    async def _baseline_lint_counts(self):
        """The original file's F821/F823 diagnostics, computed once per task so the
        gate stays baseline-relative (a pre-existing violation must not kill
        every mutant)."""
        if self._baseline_lint == "unset":
            self._baseline_lint = await violation_counts(self.file_source)
        return self._baseline_lint

    def _apply_noise_floor(self, latency_ms: float) -> float:
        """Improvements below the noise floor are erased (snapped to baseline)
        BEFORE sorting, so the router and the front agree on what counts."""
        baseline = float(self.baseline.get("latency_ms") or 0.0)
        if baseline <= 0:
            return latency_ms
        improvement_pct = (baseline - latency_ms) / baseline * 100.0
        if improvement_pct < self._noise_floor_pct:
            return baseline
        return latency_ms

    def _best_delta_pct(self, front: List[Dict[str, Any]]) -> Optional[float]:
        baseline = float(self.baseline.get("latency_ms") or 0.0)
        if baseline <= 0 or not front:
            return None
        best = min(float(c.get("latency_ms") or baseline) for c in front)
        delta = (baseline - best) / baseline * 100.0
        return delta if delta >= self._noise_floor_pct else None

    def _front_grew(self, new_front: List[Dict[str, Any]]) -> bool:
        old = {(c.get("latency_ms"), c.get("peak_mem_bytes"), c.get("diff_bytes"))
               for c in self._front}
        new = {(c.get("latency_ms"), c.get("peak_mem_bytes"), c.get("diff_bytes"))
               for c in new_front}
        return bool(new - old)

    @property
    def _current_fold(self) -> Optional[str]:
        if not self._front:
            return None
        return build_folded_state(
            goal=self.goal or f"optimize {self.focus_node}",
            target_path=self.target_rel,
            test_target=self.test_target,
            bench_command=" ".join(self.bench_argv),
            candidates=self._front,
            front_size=len(self._front),
            best_delta_pct=self._best_delta_pct(self._front),
            generation=0,
        )

    def _tests_excerpt(self) -> str:
        try:
            return (self.sandbox.work / self.test_target).read_text(
                encoding="utf-8", errors="replace"
            )[:6000]
        except OSError:
            return "(test source unavailable)"


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


def _byte_diff_size(before: bytes, after: bytes) -> int:
    """Changed byte count, not merely the length delta.

    A same-length replacement can still rewrite every byte; scoring it as a
    zero-byte diff would make the Pareto objective actively misleading.
    """
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
