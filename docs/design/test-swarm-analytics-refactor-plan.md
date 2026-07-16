# `tests/test_swarm_analytics.py` refactor plan (Loop 149)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 81 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-57%** |
| Hot | `test_inventory_and_measured_metrics_are_stable` ~17 · `test_tool_refuses_cloud_and_does_not_require_workspace` ~8 · `test_parse_error_and_secret_warning_never_echo_secret` ~6 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_inventory_and_measured_metrics_are_stable` | extract hot path (~17 LOC) |
| split / `test_tool_refuses_cloud_and_does_not_require_workspace` | extract hot path (~8 LOC) |
| split / `test_parse_error_and_secret_warning_never_echo_secret` | extract hot path (~6 LOC) |
| `tests/test_swarm_analytics.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
