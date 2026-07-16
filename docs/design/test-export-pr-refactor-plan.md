# `tests/test_export_pr.py` refactor plan (Loop 137)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 107 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-67%** |
| Hot | `test_export_swarm_narrow_pr_honors_primary_goal` ~48 · `test_export_swarm_narrow_pr` ~38 · `workspace` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_export_swarm_narrow_pr_honors_primary_goal` | extract hot path (~48 LOC) |
| split / `test_export_swarm_narrow_pr` | extract hot path (~38 LOC) |
| split / `workspace` | extract hot path (~9 LOC) |
| `tests/test_export_pr.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
