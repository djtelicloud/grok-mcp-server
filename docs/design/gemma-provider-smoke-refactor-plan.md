# `evals/.../provider_smoke.py` refactor plan (Loop 51)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 491 |
| Projected primary LOC | ~60 facade |
| % LOC change (primary file) | **−88%** |
| Classes / funcs | 4 / 16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../provider_smoke_binding.py` | UniGrokBinding |
| `evals/.../provider_smoke_io.py` | private dir / write receipt |
| `evals/.../provider_smoke_run.py` | run_smoke |
| `evals/.../provider_smoke.py` | facade ≤ 60 LOC |

Move-only; no Stage-1 live auth bypass.
