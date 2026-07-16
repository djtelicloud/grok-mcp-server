# `tests/test_generate_okf.py` refactor plan (Loop 143)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 92 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-62%** |
| Hot | `test_extracts_sync_async_classes_and_public_methods` ~33 · `test_render_is_deterministic_and_contains_async_public_tools` ~10 · `test_manifest_and_public_mirror_are_current` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_extracts_sync_async_classes_and_public_methods` | extract hot path (~33 LOC) |
| split / `test_render_is_deterministic_and_contains_async_public_tools` | extract hot path (~10 LOC) |
| split / `test_manifest_and_public_mirror_are_current` | extract hot path (~9 LOC) |
| `tests/test_generate_okf.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
