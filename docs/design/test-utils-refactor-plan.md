# `tests/test_utils.py` refactor plan (Loop 2)

Status: **Ready for supervisor** — plan only; move-only strategy.  
Depends on: `docs/design/utils-refactor-plan.md` (PR #342) module map.  
Lane: Cursor superiority loop · branch worktree `.worktrees/cursor/python-superiority-loop/`

## Why not a mega rewrite

`tests/test_utils.py` is **~7848 LOC**, **50** test classes, **~311** test functions, **~179** `src.utils` import lines, **~238** pytest marks. It must stay green while `src/utils.py` is sliced. This packet plans a **domain-aligned test split** paired with utils extract PRs — no assertion rewrites in the first waves.

## Baseline metrics (before)

| Metric | Value |
|--------|------:|
| LOC | 7848 |
| Bytes | 329984 |
| Test classes | 50 |
| Test functions | ~311 |
| AST parse / compile | ~29 ms / ~26 ms |
| Branch nodes | 213 |
| Imports from `src.utils` | ~179 lines |
| Hot coupling | AgentLoop, plane failover, session store, PathResolver |

## Hive review (index-diff)

Plane: CLI · mode: fast · fallback: same_plane · client: `cursor-forge` · model: `grok-composer-2.5-fast` · cost: $0

| Claim | Vote |
|-------|------|
| L1 Split by domain matching utils modules | GOOD |
| L2 Keep `src.utils` facade imports until extracts land | KEEP |
| L3 First: path / runtime / request_context tests | GOOD |
| L4 Move-only + shared fixtures; no assertion edits | KEEP |
| L5 Pair each utils extract PR with test move PR | GOOD |
| L6 AgentLoop / routing tests last | GOOD |
| L7 No single mega test rewrite | KEEP |

## Swarm gate

`start_code_swarm` refused while `UNIGROK_SWARM=off`. Loop continues on plan-PR path; swarm polish waits for Forge `dry_run` (or later leaf modules after utils extracts).

## Proposed test modules (target)

Projected: thin `tests/test_utils.py` shim (re-exports / shared fixtures) + focused files. Envelopes only.

| New test module | Source classes / concern (approx) | Projected LOC | Pairs with utils module |
|-----------------|-----------------------------------|--------------:|-------------------------|
| `tests/utils/test_path_resolver.py` | `TestPathResolver` | 80–150 | `src/path_resolver.py` |
| `tests/utils/test_request_context.py` | request-id / logging helpers | 100–200 | `src/request_context.py` |
| `tests/utils/test_caller_budget.py` | budget / cost hooks | 100–200 | `src/caller_budget.py` |
| `tests/utils/test_runtime_env.py` | `run_blocking`, runtime flags | 150–300 | `src/runtime_env.py` |
| `tests/utils/test_resilience.py` | circuit breaker / error class | 200–400 | `src/resilience.py` |
| `tests/utils/test_cli_plane.py` | CLI readiness / args / sessions | 300–600 | `src/cli_plane.py` |
| `tests/utils/test_session_store.py` | `TestGrokSessionStore`, history helpers | 400–800 | `src/session_store.py` |
| `tests/utils/test_agent_loop.py` | AgentLoop / tools / truncate / dispatch | 1200–2000 | `src/agent_loop.py` |
| `tests/utils/test_routing.py` | routing, failover, orchestrate, thinking | 1500–2500 | `src/routing.py` |
| `tests/utils/conftest.py` | shared fixtures | 100–250 | — |
| `tests/test_utils.py` | thin re-export / legacy entry | ≤ 200 | facade |

## Migration order

1. Add `tests/utils/conftest.py` + path/runtime/request_context moves (leaf).
2. Resilience + CLI plane test moves with matching utils extract PRs.
3. Session store tests.
4. AgentLoop + routing last.
5. Shrink `tests/test_utils.py` to shim; optional pytest collection alias.

**Rule:** move-only first wave; keep `from src.utils import …` until production modules exist.

## Risk

| Risk | Mitigation |
|------|------------|
| Collection / import path breaks | Keep `tests/test_utils.py` importing moved tests or use package layout with `__init__` |
| Fixture leakage across files | Central `conftest.py`; snapshot env keys (already used) |
| Diverging from utils extract order | Pair PRs; do not move tests before target module exists |
| Slow CI from path churn | One domain per PR; run focused node ids |

## Success criteria

- LOC of `tests/test_utils.py` trending down each paired PR.
- No assertion rewrites in move-only waves.
- Full `tests/test_utils.py` + new package still collect/pass after each slice.

## Non-goals

- Rewriting plane policy tests.
- Swarm-mutating test bodies before Forge swarm gate is on.
- Landing protected `main`.
