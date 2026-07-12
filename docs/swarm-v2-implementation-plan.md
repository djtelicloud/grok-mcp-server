# Swarm v2 implementation plan

Status: proposed; implementation has not started  
Reviewed: 2026-07-12 with Grok 4.5 on the CLI subscription plane  
Scope owner: Codex integration

## Executive decision

Swarm v2 is a productization of the existing verified funnel, not a general
optimizer research platform.

The user flow is:

```text
paste or open code
  -> analyze without an LLM
  -> choose one function and one goal
  -> establish tests and a stable benchmark
  -> run local evolutionary search
  -> compare original, parent, and champion
  -> copy the Best verified candidate or apply it locally
```

The rollout modes remain `off`, `dry_run`, and `active`. Evolution is a search
strategy, not a fourth authority mode:

```text
search_strategy = baseline_batch | elite_offspring
```

The public cloud may render recorded runs and perform explicitly client-side
static analysis. It must never execute, persist, or optimize pasted user code
on a server. Verified search and Apply remain local contributor capabilities.

### Grok review disposition

The plan adopts Grok's highest-leverage recommendations: keep authority and
search strategy orthogonal, ship deterministic analysis before evolutionary
search, preserve baseline immigrants, make genetic parents auditable, keep the
cloud surface execution-free, and defer metrics that cannot yet be measured
honestly. It deliberately adjusts two details from the review:

- the proposed `50/25/20` generation split is normalized to `50/25/25` so every
  generation has a complete, deterministic allocation;
- loop-invariant attribute aliasing is excluded from the first transform set
  because Python descriptors and mutation make a general purity claim unsafe.

## Why v2 is needed

v1 is an honest single-span Python optimizer, but its product and search limits
are material:

- input is a workspace-relative target plus a manually named focus node;
- there is no paste scratchpad or automatic function inventory;
- every LLM candidate is descended from the original span;
- elites affect folded prompt context but do not produce offspring;
- the UI has no candidate lineage;
- objective code analytics are limited to correctness gates, latency, peak
  memory, changed bytes, coverage, and benchmark stability;
- `diff_bytes` is useful for review cost but is not a maintainability metric;
- cloud has no read-only Swarm showcase or client-side paste analyzer.

v2 closes those gaps without weakening the v1 verification boundary: passing
the user's test target is still the definition of "verified," and weak tests
remain an explicitly surfaced limitation.

## Product contract

### Canonical local experience

1. **Paste or open code.** Accept a Python function or file up to 256 KiB.
2. **Analyze free.** Parse and inventory functions, then compute deterministic
   Tier A analytics without a model call or code execution.
3. **Choose a target.** Rank detected functions by complexity and size, while
   allowing manual selection.
4. **Choose a primary goal.** `latency`, `memory`, `size`, or `balanced`.
5. **Establish the oracle.** Tests are required for search. Examples may help
   scaffold a test or benchmark but never count as correctness evidence.
6. **Preflight.** Require passing baseline tests, non-zero focus coverage,
   import provenance, stage-budget compliance, and a stable benchmark.
7. **Search.** Run `baseline_batch` or `elite_offspring` locally under the
   existing `dry_run`/`active` authority ladder.
8. **Compare.** Show original, parent, and champion code; objective metrics;
   unified diffs; lineage; gates; and receipts.
9. **Take the result.** Copy or download the **Best verified candidate**.
   Workspace Apply remains `active`-only and re-verifies after writing.

The UI must never call a candidate "perfected," "optimal," or "semantically
proven."

### Cloud experience

The public page provides:

- the same renderer for a curated `unigrok-swarm-status-v2` fixture;
- a large paste editor whose analysis runs entirely in the browser;
- Python function inventory, LOC, nesting, and approximate cyclomatic
  complexity from a pinned client-side parser;
- an explicit label: **Client-side static preview - not verified**;
- export/download and a clear route to the local Forge.

The public page provides no Start Search, Apply, server upload, background
telemetry of pasted source, or native-performance claims. Browser/WASM timing
must not be compared with native Python benchmark results.

Boundary copy:

> Cloud: showcase and client-side preview. Verified search and Apply: local only.

## Scope and non-goals

| In v2 | Explicitly deferred |
|---|---|
| One Python function or method | Multi-function joint optimization |
| Pasted file or existing workspace file | Package-wide or multi-repository search |
| Deterministic Tier A analytics | Full security certification |
| Real parent/child lineage | Decorative genealogy with no parent policy |
| Baseline immigrants and elite offspring | Unspecified novelty search |
| Closed deterministic AST transforms | Arbitrary source-to-source plugins |
| User tests and benchmark as oracle | LLM-generated tests as proof |
| Existing latency/memory/diff Pareto front | Readability as a Pareto objective |
| Copy result and guarded local Apply | Auto-commit, PR creation, or cloud Apply |

Mutation testing, whole-project type checking, bytecode analysis, Linux hardware
counters, and deeper security scanners are candidates for later evidence tiers.
They do not block v2.

## Architecture invariants

1. Correctness is a feasibility constraint, never an LLM score.
2. Infeasible candidates never enter a Pareto front or become champion.
3. Analytics run before any model call and have no network dependency.
4. The existing cheapest-filter-first funnel remains in order.
5. Search authority remains `off -> dry_run -> active`.
6. `search_strategy` cannot increase file or cloud authority.
7. Pasted source is untrusted data in prompts and untrusted code in sandboxes.
8. Cloud never executes or persists pasted source.
9. Code is returned only for the champion/current front; lineage carries IDs,
   hashes, metrics, and receipts rather than every source body.
10. Every metric includes its producer and version; absent metrics stay absent.

## Deterministic analytics

Tier A exists to choose a target, expose blockers, and compare code. It is not
a second scoring system and does not expand the Pareto objectives.

| Signal | Implementation | Use |
|---|---|---|
| Parse and function inventory | Python `ast` plus existing span utilities | Target picker |
| LOC, branch points, nesting, cyclomatic complexity | Pure Python AST walker | Searchability and comparison |
| Full isolated Ruff report | Pinned Ruff subprocess, project config ignored | Informational hygiene delta |
| F821/F823 baseline multiset | Existing static gate | Candidate hard gate |
| Module-local import graph | Python `ast` | Scratch completeness/blockers |
| Simple dead-code indicators | Unused imports and unreferenced private span names | Simplify-arm evidence |
| Token duplication | Token-window hashes within the pasted file | Target ranking |
| Secret-pattern warning | Existing redaction patterns | Export and persistence guard |
| Focus coverage | Existing preflight path when tests exist | Oracle honesty |

Analytics results use a versioned, measured-only contract:

```json
{
  "format": "unigrok-swarm-analytics-v1",
  "source": "paste",
  "tooling": {"python_ast": "runtime", "ruff": "0.x"},
  "functions": [
    {
      "focus_node": "function:dedup",
      "span": [0, 160],
      "loc": 7,
      "cyclomatic_complexity": 3,
      "max_nesting": 2
    }
  ],
  "ruff": {"counts_by_code": {}},
  "risks": ["no_tests"],
  "searchability": {
    "ready": false,
    "blockers": ["missing_test_target", "missing_benchmark"]
  }
}
```

Metric values shown in comparisons use a common envelope:

```json
{
  "name": "cyclomatic_complexity",
  "value": 3,
  "unit": "count",
  "direction": "lower_is_better",
  "scope": "focus_node",
  "producer": "unigrok_ast_metrics",
  "producer_version": "1"
}
```

Informational metrics do not become gates or Pareto objectives merely because
they are available.

## Evolutionary search design

### Strategies

`baseline_batch` retains v1 behavior and remains the default. Candidate
`parent_id` is null and the source parent is the original span.

`elite_offspring` activates real genetic parents. For population sizes divisible
by four, each generation uses:

- 50% elite offspring produced by a CLI LLM arm;
- 25% baseline immigrants produced by a CLI LLM arm;
- 25% candidates from a closed deterministic AST transform set.

The default population of four therefore produces two elite offspring, one
baseline immigrant, and one deterministic candidate. Generation one has no
elite parent, so its elite slots use the baseline.

### Parent selection

When a front exists:

1. draw two front members;
2. select the better parent for the task's `primary_goal`;
3. with epsilon `0.15`, select a uniformly random front member instead;
4. record the selected parent and policy receipt.

The folded prompt emphasizes the selected parent plus at most the two most
relevant front members. It must not dump the complete lineage into every prompt.

### Champion selection

Champion selection never changes the Pareto front. It chooses one rank-zero
candidate for the primary CTA:

- `latency`: minimum latency, then memory, then diff size;
- `memory`: minimum peak memory, then latency, then diff size;
- `size`: minimum diff size, then latency, then memory;
- `balanced`: minimum equal-weight normalized distance to the ideal point over
  latency, memory, and diff size; candidate ID is the final stable tie-break.

The UI continues to show every rank-zero trade-off even when one champion is
selected.

### Deterministic transform plane

The initial closed set is deliberately small:

1. single-target `for` plus `append` to list comprehension;
2. list comprehension to explicit loop;
3. constant `if True`/`if False` branch reduction.

Transforms are attempted, never assumed safe. They receive `origin="ast"`, a
transform receipt, zero generation cost, and the full existing funnel. A failed
transform is discarded without an LLM heal call.

### Diversity and stopping

- Baseline immigrants are mandatory to resist elite monoculture.
- Candidate hashes stay unique per task.
- AST identity is checked against every seen candidate, not only the parent.
- Two generations without a new Pareto point trigger early stop.
- Stagnation is based on the complete Pareto front, not only the primary goal.
- Existing cost, timeout, concurrency, and generation bounds remain enforced.

## Storage and API contracts

### Migration v13

`swarm_tasks` gains additive columns:

- `search_strategy TEXT NOT NULL DEFAULT 'baseline_batch'`
- `primary_goal TEXT NOT NULL DEFAULT 'balanced'`
- `input_kind TEXT NOT NULL DEFAULT 'workspace'`
- `analytics_json TEXT`
- `champion_id TEXT`

`swarm_candidates.parent_id` already exists as a v2 reservation and becomes
active. Candidate rows additionally gain:

- `parent_code_hash TEXT`
- `origin TEXT NOT NULL DEFAULT 'llm'`
- `transform TEXT`

`arm_receipt` remains the detailed routing/transform receipt. Existing v12 rows
must read as baseline/workspace/balanced without rewrite.

### Tool surface

Add or extend these local tools:

```text
analyze_code_for_swarm(code, language="python") -> analytics-v1 JSON
start_code_swarm(..., search_strategy="baseline_batch", primary_goal="balanced")
start_paste_swarm(code, test_code, bench_code, ..., search_strategy, primary_goal)
get_swarm_status(task_id, view="text|json") -> status-v1 or status-v2
```

`analyze_code_for_swarm` is read-only, performs no execution or model call, and
accepts at most 256 KiB. `start_paste_swarm` is contributor-only, non-Cloud-Run,
and unavailable while `UNIGROK_SWARM=off`.

### Status v2

`unigrok-swarm-status-v2` adds:

- task `input_kind`, `search_strategy`, and `primary_goal`;
- `analytics` summary and tooling versions;
- candidate `parent_id`, `parent_code_hash`, `origin`, and `transform`;
- `champion_id`;
- original/parent/champion comparison metrics;
- unified diff from original and, when applicable, parent.

The renderer accepts v1 by normalizing missing v2 fields to baseline defaults.
Static exports remain replayable and cannot enable Apply.

### Scratch layout

Pasted search materializes only after explicit local search:

```text
<state>/swarm-paste/<task-id>/
  module_under_test.py
  tests/test_focus.py
  bench_focus.py
```

Missing tests or a benchmark leaves analytics usable but sets
`searchability.ready=false`. Examples may scaffold templates, but generated or
example-derived tests never satisfy the oracle without the same preflight.

Paste tasks are copy-only by default. Writing into a real workspace requires an
explicit target mapping and then uses the existing hash, front-membership, and
post-apply verification gates.

## Security and privacy

- Analysis parses source but never imports or executes it.
- Search treats pasted code as untrusted execution with the existing scrubbed
  environment, private HOME/TMPDIR, RLIMITs, process-group termination, and
  bounded output.
- The sandbox remains a containment layer, not a kernel boundary; portable
  network denial is still unavailable and must remain disclosed.
- Pasted source, tests, examples, folded state, and candidate source stay inside
  untrusted prompt fences with role-marker neutralization.
- Secret-pattern warnings block persistence/export of affected code fields;
  analytics may return bounded counts and blocker names, never the matched text.
- Bench commands remain `python <workspace-relative-script.py> [args...]`; no
  shell, `-c`, `-m`, absolute path, or traversal.
- Public browser analysis uses pinned local assets, no analytics request, and no
  source-bearing telemetry.

## Implementation sequence

Each phase is a separately landable protected-main PR. A later phase cannot
start until the earlier phase's exit gate is green.

### Phase 0 - contracts and compatibility

Deliver:

- migration v13 and protocol/store updates;
- strategy and primary-goal configuration;
- analytics-v1 and status-v2 typed builders/fixtures;
- v1-to-v2 renderer normalization;
- no search behavior change; default remains `baseline_batch`.

Exit gate:

- v5/v9/v11/v12 migration fixtures upgrade sequentially to v13;
- old rows and v1 exports render byte-stably;
- missing strategies/goals use documented defaults and unknown values are
  rejected;
- full suite, evals, OKF, Docker, and site checks pass.

### Phase 1 - paste inventory and Tier A analytics

Deliver:

- `src/swarm/analytics.py` pure deterministic analyzer;
- `analyze_code_for_swarm` read-only tool;
- large local editor, function picker, goal picker, metrics cards, and actionable
  searchability blockers;
- browser-only cloud preview using a pinned parser and no source upload;
- no paste execution yet.

Exit gate:

- 256 KiB input cap and malformed/secret fixtures are covered;
- nested functions and methods receive stable focus-node identities;
- analysis tests prove zero model, network, import, and execution calls;
- local and cloud UI visibly distinguish analysis from verification.

### Phase 2 - elite offspring, lineage, and champion

Deliver:

- parent selection policy and recorded lineage;
- 50/25/25 generation allocation;
- parent-aware prompts and bounded folded state;
- goal-specific champion selection;
- original/parent/champion comparison UI;
- workspace tasks first; paste execution remains disabled.

Exit gate:

- baseline candidates have null parents;
- later elite generations contain valid prior front parent IDs;
- immigrants remain present across deterministic seeded runs;
- dry-run Apply refusal and active re-verification remain unchanged;
- fixture comparison shows no more than 1.5x model cost for equal generation and
  population bounds unless a documented quality gate justifies it.

### Phase 3 - deterministic AST candidates

Deliver:

- closed transform registry;
- transform and origin receipts;
- AST candidates through the full funnel at zero model cost;
- dedupe against every seen candidate.

Exit gate:

- each transform has positive, no-op, rejection, and semantic-regression tests;
- unsafe transforms never bypass tests;
- at least one golden fixture admits a zero-cost feasible candidate.

### Phase 4 - paste-to-search end to end

Deliver:

- scratch materialization;
- test/benchmark editors and templates;
- preflight and search on paste tasks;
- Copy Best Verified Code and patch download;
- explicit optional mapping to a workspace file for guarded Apply.

Exit gate:

- missing tests/bench block search with actionable copy;
- a full real-model dry run completes from pasted code;
- scratch source cannot escape its task directory;
- copy output equals the stored champion bytes;
- workspace mapping preserves the existing stale-hash and rollback guarantees.

### Phase 5 - public showcase and production closeout

Deliver:

- public read-only Playground route;
- recorded v2 fixture and client-side paste analysis;
- local Forge handoff documentation;
- route-by-route browser verification and production publication.

Exit gate:

- browser network capture proves pasted source is never transmitted;
- public Start Search and Apply controls do not exist;
- the same fixture renders equivalently in public and local viewers;
- public project metadata and OKF docs describe the boundary accurately.

## Acceptance suite

The following are definition-of-done tests, not optional follow-up:

1. Preflight refuses zero focus coverage.
2. Unstable benchmarks refuse unless explicitly allowed.
3. Infeasible candidates never enter the front or become champion.
4. Signature changes die at the signature stage.
5. Duplicate and AST-identical candidates are discarded without evaluation.
6. `baseline_batch` candidates have no genetic parent.
7. `elite_offspring` candidates reference a valid earlier elite.
8. Seeded population allocation preserves offspring, immigrant, and AST slots.
9. Two stagnant generations and configured budgets stop search.
10. LLM and AST receipts identify their origin and parent policy.
11. `dry_run` refuses Apply.
12. `active` applies only a current front member and restores on failed re-test.
13. Cloud Run and non-contributor runtimes refuse search and Apply.
14. Function inventory covers nested functions and class methods.
15. Secret-like paste never appears in persisted analytics or status exports.
16. Missing tests and benchmarks produce explicit blockers.
17. Tier A analysis makes no model, network, import, or execution call.
18. Live and static status-v2 payloads use the same renderer.
19. Comparison shows original, parent when present, and champion.
20. UI copy uses **Best verified candidate** and never claims perfection.
21. Primary-goal champion selection is deterministic and front-only.
22. Child environments omit xAI, GitHub, and other recognized secrets.
23. Bench parsing rejects shell, `-c`, `-m`, absolute, and escaping paths.
24. Prompt-injection fixtures remain fenced as untrusted source.
25. Cloud paste analysis transmits no source bytes.
26. Paste scratch paths and outputs remain task-local.

## Final cut line

Call the release v2 only when Phases 0 through 4 and acceptance tests 1 through
24 are complete. Phase 5 is the production showcase gate. Do not delay v2 for
mutation testing, bytecode metrics, whole-project types, multi-span rewriting,
novelty search, or hardware counters.
