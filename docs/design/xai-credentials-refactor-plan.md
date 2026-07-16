# `src/xai_credentials.py` refactor plan (Loop 155)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 59 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-41%** |
| Hot | `_resolve_optional_xai_management_key` ~14 · `_xai_management_key_state` ~13 · `_require_xai_management_key` ~11 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `_resolve_optional_xai_management_key` | extract hot path (~14 LOC) |
| split / `_xai_management_key_state` | extract hot path (~13 LOC) |
| split / `_require_xai_management_key` | extract hot path (~11 LOC) |
| `src/xai_credentials.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
