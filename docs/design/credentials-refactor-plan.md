# `src/credentials.py` refactor plan (Loop 102)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 285 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-88%** |
| Hot | `build_credential_plane_contract` ~84 · `_cli_action` ~42 · `is_secret_environment_name` ~17 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `build_credential_plane_contract` |
| split module | concern from hot path `_cli_action` |
| split module | concern from hot path `is_secret_environment_name` |
| `src/credentials.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
