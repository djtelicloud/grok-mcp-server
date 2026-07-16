# `src/tools/workspace_memory.py` refactor plan (Loop 120)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 151 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-77%** |
| Hot | `record_landed_outcome` ~43 · `recall_workspace_memory` ~27 · `explain_workspace_evidence` ~8 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `record_landed_outcome` |
| split module | concern from hot path `recall_workspace_memory` |
| split module | concern from hot path `explain_workspace_evidence` |
| `src/tools/workspace_memory.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
