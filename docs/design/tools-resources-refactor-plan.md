# `src/tools/resources.py` refactor plan (Loop 86)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 266 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-87%** |
| Hot | `register_resource_primitives` ~168 · `_workspace_git_summary` ~24 · `_read_agent_doc` ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `register_resource_primitives` |
| split module | concern from hot path `_workspace_git_summary` |
| split module | concern from hot path `_read_agent_doc` |
| `src/tools/resources.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
