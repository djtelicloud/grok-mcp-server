# `tests/test_git_tools.py` refactor plan (Loop 124)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 148 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-76%** |
| Hot | `test_git_write_tools_work_when_enabled` ~19 · `test_git_apply_patch_allows_safe_dev_null_create` ~16 · `test_git_read_tools` ~15 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_git_write_tools_work_when_enabled` |
| split module | concern from hot path `test_git_apply_patch_allows_safe_dev_null_create` |
| split module | concern from hot path `test_git_read_tools` |
| `tests/test_git_tools.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
