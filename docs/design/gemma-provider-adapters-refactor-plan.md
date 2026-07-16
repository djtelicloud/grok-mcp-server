# `evals/.../provider_adapters.py` refactor plan (Loop 40)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 623 |
| Projected primary LOC | ~80 facade |
| % LOC change (primary file) | **−87%** |
| Classes / funcs | 3 / 16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../provider_secret_guards.py` | secret reject helpers |
| `evals/.../provider_adapter_core.py` | ProviderAdapter (~363 LOC) |
| `evals/.../provider_adapters.py` | facade ≤ 80 LOC |

Move-only; secret-reject guards stay fail-closed.
