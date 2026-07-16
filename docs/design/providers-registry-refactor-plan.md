# `src/providers/registry.py` refactor plan (Loop 159)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 49 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-29%** |
| Hot | `build_provider_registry` ~33 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `build_provider_registry` | extract hot path (~33 LOC) |
| `src/providers/registry.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
