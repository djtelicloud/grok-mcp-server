# `src/providers/google_common.py` refactor plan (Loop 139)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 101 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-65%** |
| Hot | `parse_generate_content_response` ~49 · `build_generate_content_payload` ~30 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `parse_generate_content_response` | extract hot path (~49 LOC) |
| split / `build_generate_content_payload` | extract hot path (~30 LOC) |
| `src/providers/google_common.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
