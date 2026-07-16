# `tests/test_agent_attribution.py` refactor plan (Loop 90)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 247 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-86%** |
| Hot | `test_legacy_invalid_trailer_before_merge_base_does_not_block` ~20 · `_commit` ~17 · `_git` ~16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_legacy_invalid_trailer_before_merge_base_does_not_block` |
| split module | concern from hot path `_commit` |
| split module | concern from hot path `_git` |
| `tests/test_agent_attribution.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
