# Python superiority — Codex reclassify (draft #475)

Private continuity mirror for Codex. Live scoreboard:
`unigrok-intelligence/codex/continuity/python-superiority-loop.md`.

## Codex rules applied

From draft [#475](https://github.com/djtelicloud/grok-mcp-server/pull/475) (HOLD `4994892133` + prior reclassify ask):

1. Tiny modules, tests, fixtures, already-adequate → **skip** / no change.
2. Proposed facade larger than the original → **not** a refactor candidate.
3. One file → many → **one bundle** (same entry point; total LOC; e2e latency; peak memory). Never sum/average per-file %.
4. Measured wins require correctness oracle + independent before/after latency & peak memory.
5. **HOLD**: no swarm retry, no new plan PRs, do not publish #408 until CONTINUE after #476 Live + Forge refresh.

## Honest scoreboard (2026-07-16)

| KPI | Count |
|---:|---:|
| Measured (oracle + bench) | **0** |
| Swarm-ready (held) | **1** (`src/swarm/pareto.py` · `fast_non_dominated_sort`) |
| Refactor-plan kept (LOC≥300, facade would shrink) | **77** |
| Skip: tiny / facade≥original / fixture-init-target | **124** |
| Skip: untracked / not on main | **5** |
| Skip: private scratch | **4** |
| Skipped total | **133** |
| Inventory | 210 |

**PROJECTED** facade LOC (kept plans only): 86,835 → 12,346 (−85.8% primary-file target). Labeled **PROJECTED** — not measured performance.

## Bundles

Treat src+test (and related stage1 sets) as one concern when measuring. Named bundles in the tracker: `utils`, `provider_broker`, `http_server`, `gemma_stage1`, `intelligence_payloads`, `subscription`, `mcp_session_guard`, `mcp_sampling`, `swarm_engine`, `completion_envelope`, `workspace_memory`, `semantic_evals`, `metrics`.

## Gates

- #408 metrics PR: **still draft / unpublished**
- Swarm retry: **blocked** until Codex CONTINUE after #476
