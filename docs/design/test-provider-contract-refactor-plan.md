# `tests/.../test_provider_contract.py` refactor plan (Loop 49)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 533 |
| Projected primary LOC | ~70 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~19 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/campaigns/.../provider/test_transport_receipts.py` | transport receipt contracts |
| `tests/campaigns/.../provider/test_namespace_isolation.py` | mock/live/replay isolation |
| `tests/campaigns/.../provider/test_replay_integrity.py` | insecure/mismatch rejects |
| `tests/campaigns/.../provider/test_live_schema.py` | prose/wrong-schema before cache |
| thin shim at current path | ≤ 70 LOC |

Move-only.
