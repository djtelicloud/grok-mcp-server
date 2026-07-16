# `src/tools/git.py` refactor plan (Loop 87)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 258 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-86%** |
| Hot | `_run_git` ~17 · `_validate_patch_targets` ~16 · `_extract_patch_file_header` ~16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `_run_git` |
| split module | concern from hot path `_validate_patch_targets` |
| split module | concern from hot path `_extract_patch_file_header` |
| `src/tools/git.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
