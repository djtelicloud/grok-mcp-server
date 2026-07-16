# `scripts/land.py` refactor plan (Loop 55)

Status: **Ready for supervisor** — plan only. High-risk supervisor path — plan only, no behavior change.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 489 |
| Projected primary LOC | ~70 facade |
| % LOC change (primary file) | **−86%** |
| Classes / funcs | 1 / 23 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/land_lock.py` | directory_lock |
| `scripts/land_receipt.py` | write_receipt |
| `scripts/land_runtime.py` | runtime_changes |
| `scripts/land_core.py` | land orchestration |
| `scripts/land.py` | CLI facade ≤ 70 LOC |

Move-only; land semantics / gates unchanged. Codex owns live land.
