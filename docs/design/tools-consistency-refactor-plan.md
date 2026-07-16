# `src/tools/consistency.py` refactor plan (Loop 141)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 98 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-64%** |
| Hot | `architecture_consistency_sweep` ~75 · `register_consistency_tools` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `architecture_consistency_sweep` | extract hot path (~75 LOC) |
| split / `register_consistency_tools` | extract hot path (~2 LOC) |
| `src/tools/consistency.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
