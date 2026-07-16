# `tests/.../test_provider_smoke.py` refactor plan (Loop 64)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 407 |
| Projected primary LOC | ~50 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~11 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/campaigns/gemma_needle_2000_v1/provider_smoke/test_live_replay.py` | live-then-replay call counts |
| `tests/campaigns/gemma_needle_2000_v1/provider_smoke/test_failure_receipts.py` | transport vs response receipts |
| `tests/campaigns/gemma_needle_2000_v1/provider_smoke/test_vertex_transport.py` | vertex SDK attempt |
| `tests/campaigns/gemma_needle_2000_v1/test_provider_smoke.py` | shim ≤ 50 LOC |

Move-only; live/replay assertions unchanged.
