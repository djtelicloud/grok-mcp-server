# `tests/.../test_stage1_mock_harness.py` refactor plan (Loop 54)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 477 |
| Projected primary LOC | ~60 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~7 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/campaigns/.../stage1/test_mock_topology.py` | transport-free/private/resumable |
| `tests/campaigns/.../stage1/test_reviewer_blindness.py` | content without authority |
| `tests/campaigns/.../stage1/test_crash_takeover.py` | indeterminate/no-retry |
| `tests/campaigns/.../stage1/test_seed_binding.py` | wrong seed fails root profile |
| thin shim at current path | ≤ 60 LOC |

Move-only; no Stage-1 live gen.
