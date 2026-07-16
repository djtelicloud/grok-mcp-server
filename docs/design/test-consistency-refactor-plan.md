# `tests/test_consistency.py` refactor plan (Loop 157)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 52 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-33%** |
| Hot | `test_sweep_reads_files_and_calls_agent` ~17 · `workspace` ~12 · `test_sweep_handles_missing_files` ~7 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_sweep_reads_files_and_calls_agent` | extract hot path (~17 LOC) |
| split / `workspace` | extract hot path (~12 LOC) |
| split / `test_sweep_handles_missing_files` | extract hot path (~7 LOC) |
| `tests/test_consistency.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
