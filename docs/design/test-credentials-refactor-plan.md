# `tests/test_credentials.py` refactor plan (Loop 29)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~804 LOC**, **~29** top-level tests. Split plane pin, preflight, research cross-plane cases.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 804 |
| Projected primary LOC | ~100 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~29 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/credentials/test_plane_pin.py` | auto exact pin / membership |
| `tests/credentials/test_preflight.py` | preflight persist session/telemetry |
| `tests/credentials/test_research_cross_plane.py` | research API-only cross |
| `tests/test_credentials.py` | shim ≤ 100 LOC |

## Migration order

plane pin → preflight → research → remaining → shim. Move-only.

## Non-goals

Credential policy changes; landing `main`.
