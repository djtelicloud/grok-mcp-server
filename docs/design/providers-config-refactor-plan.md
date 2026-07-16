# `src/providers/config.py` refactor plan (Loop 133)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 118 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-70%** |
| Hot | `load_model_pins` ~27 · `configured_vertex_project` ~7 · `vertex_location` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `load_model_pins` | extract hot path (~27 LOC) |
| split / `configured_vertex_project` | extract hot path (~7 LOC) |
| split / `vertex_location` | extract hot path (~5 LOC) |
| `src/providers/config.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
