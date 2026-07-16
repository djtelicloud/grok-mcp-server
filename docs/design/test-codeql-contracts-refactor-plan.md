# `tests/test_codeql_contracts.py` refactor plan (Loop 169)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 33 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-39%** |
| Hot | `test_markdown_renderer_does_not_apply_incomplete_scheme_filter` ~11 · `test_control_center_avoids_dynamic_selector_and_guard_html` ~7 · `test_land_status_does_not_log_runtime_exception_details` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_markdown_renderer_does_not_apply_incomplete_scheme_filter` | extract hot path (~11 LOC) |
| split / `test_control_center_avoids_dynamic_selector_and_guard_html` | extract hot path (~7 LOC) |
| split / `test_land_status_does_not_log_runtime_exception_details` | extract hot path (~5 LOC) |
| `tests/test_codeql_contracts.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
