# `src/utils.py` refactor plan (Loop 1)

Status: **Ready for supervisor** — plan only; no behavioral rewrite in this packet.  
Owner lane: Cursor superiority loop  
Baseline HEAD (plan branch): see PR  
Measured on worktree from `origin/main` @ `ea15d046` family tip.

## Why not a mega rewrite

`src/utils.py` is **~13.8k LOC**, **25** top-level classes, **202** top-level functions, **~1.4k** branch nodes, and is imported from **~44** Python sites. A single-shot rewrite is out of scope. This packet is a **module-split migration plan** with a thin re-export facade so callers and `tests/test_utils.py` (~7.8k LOC) stay green while slices land.

## Baseline metrics (before)

| Metric | Value |
|--------|------:|
| LOC | 13830 |
| Bytes | 590932 |
| Top-level classes | 25 |
| Top-level functions | 202 |
| AST parse time | ~53 ms |
| Compile time | ~42 ms |
| Branch nodes (If/For/While/…) | 1449 |
| Import sites (repo `*.py`) | ~44 |
| Full module import | blocked locally without deps (`aiosqlite`) — n/a |
| Hot-path note | `run_blocking`, plane/`_call_plane`, `AgentLoop`, `GrokSessionStore` dominate call volume |

## Swarm gate

Forge `start_code_swarm` refused: `UNIGROK_SWARM` is **off** (`can_use_swarm=false`). Paste `analyze_code_for_swarm` works. **Blocker for Loop 1 swarm polish:** set contributor Forge `UNIGROK_SWARM=dry_run` (or `active`) before running swarm on extracted leaf modules. Do not apply swarm to the monolith until it is sliced.

## Hive review (index-diff)

Plane: CLI · mode: fast · fallback: same_plane · client: `cursor-forge` · model: `grok-composer-2.5-fast` · cost: $0

| Claim | Vote |
|-------|------|
| L1 Split; no mega rewrite | GOOD |
| L2 Plane routing module | GOOD |
| L3 CLI session continuity module | GOOD |
| L4 Resilience / breakers module | GOOD |
| L5 PathResolver module | GOOD |
| L6 Thin re-export facade | KEEP |
| L7 Keep `test_utils` green via re-exports | KEEP |
| L8 Leaf-first migration order | GOOD |

## Proposed modules (target)

Projected end-state: **`src/utils.py` facade ≤ ~800 LOC** + **8–10** focused modules. Numbers are planning envelopes, not guarantees.

| New module | Approx. source span / concern | Projected LOC | Depends on |
|------------|-------------------------------|--------------:|------------|
| `src/path_resolver.py` | `PathResolver`, workspace/contributor gates | 250–400 | stdlib / env |
| `src/runtime_env.py` | runtime flags, scrubbed subprocess helpers, `run_blocking` | 400–700 | `path_resolver` |
| `src/request_context.py` | request ids, log filter/formatter | 200–350 | — |
| `src/caller_budget.py` | caller budgets / cost hooks | 200–350 | `request_context` |
| `src/resilience.py` | xAI error class + per-model circuit breakers | 800–1200 | `runtime_env` |
| `src/cli_plane.py` | CLI availability, args, oauth env, session locks | 900–1400 | `runtime_env` |
| `src/session_store.py` | `GrokSessionStore` (+ compaction helpers as needed) | 2500–3500 | `cli_plane`, storage |
| `src/agent_loop.py` | `AgentLoop`, tool registry, progress, reflection | 2000–2800 | `session_store`, routing |
| `src/routing.py` | `ModelResolver`, `RoutingAdvisor`, orchestrate/`_call_plane` | 1800–2500 | `cli_plane`, `resilience` |
| `src/knowledge_memory.py` | local knowledge + collections adapter seams | 1200–1800 | `session_store` |
| `src/utils.py` (facade) | re-exports for back-compat | ≤ 800 | all of the above |

Leave swarm state helpers colocated with `src/swarm/` when they are already consumed there; avoid a second ownership fight.

## Dependency edges (high level)

```text
path_resolver → runtime_env → {request_context, caller_budget, cli_plane, resilience}
cli_plane + resilience → routing
session_store → agent_loop → routing (orchestrate)
knowledge_memory ∥ agent_loop (shared session/store seams)
utils (facade) → re-export public names used by src/*, tests, scripts
```

## Migration order (PR slices)

1. **Leaf extract:** `path_resolver`, `request_context`, `caller_budget`, `runtime_env` — re-export from `utils`.
2. **Resilience + CLI plane:** `resilience.py`, `cli_plane.py` — keep `_call_plane` imports stable.
3. **Session store + compaction** — largest risk; one PR, green `tests/test_utils.py` subset + session tests.
4. **Agent loop + routing/orchestrate** — move last; highest coupling to HTTP/MCP tools.
5. **Facade shrink + import audit** — delete moved bodies from `utils.py`; optional follow-on test split.
6. **Swarm polish** — only after leaf modules exist and `UNIGROK_SWARM≠off`.

Each slice: draft PR, exact-head Ready for Codex, no land from Cursor.

## Risk

| Risk | Mitigation |
|------|------------|
| Circular imports | Facade + deferred imports inside functions where today |
| Silent behavior drift | No logic changes in extract PRs; move-only + re-export |
| Test coupling (`tests/test_utils.py` ~7.8k) | Keep importing `src.utils`; split tests only after facade stable |
| Hot-path regressions (`run_blocking`, plane calls) | Slice-local pytest + existing utils tests; no API shape change |
| Peer PR thrash | One Cursor worktree `cursor/python-superiority-loop`; no peer tree edits |

## Test impact

- **Immediate:** `tests/test_utils.py` stays pointed at `src.utils` re-exports — expect green if moves are pure.
- **Next queue file:** superiority Loop 2 targets `tests/test_utils.py` for its own clarity split **after** at least leaf extracts land (or in parallel only for test organization, not before facade exists).
- **Verify per slice:** `uv run pytest tests/test_utils.py -q` (or focused node ids) plus any touched plane/session tests.

## Success criteria for later implementation PRs

- `src/utils.py` LOC trending down each slice; facade ≤ ~800 when done.
- No public MCP tool schema changes.
- Import sites keep working without mass churn (facade).
- Swarm dry_run scorecard on at least one extracted hot function (`run_blocking` or a pure helper) once gate is on.

## Non-goals (this PR)

- Rewriting algorithms or dual-plane policy.
- Enabling `UNIGROK_SWARM` in production compose (maintainer/env decision).
- Landing protected `main`.
