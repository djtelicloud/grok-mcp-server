# `src/swarm/transforms.py` refactor plan (Loop 108)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 193 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-82%** |
| Hot | `_AppendLoopToComprehension` ~79 · `_ComprehensionToAppendLoop` ~52 · `deterministic_transforms` ~27 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `_AppendLoopToComprehension` |
| split module | concern from hot path `_ComprehensionToAppendLoop` |
| split module | concern from hot path `deterministic_transforms` |
| `src/swarm/transforms.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
