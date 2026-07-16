# `tests/.../test_stage1_schema_safety.py` refactor plan (Loop 33)

Status: **Ready for supervisor** — plan only. Campaign gemma_needle_2000_v1.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 721 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~21 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/campaigns/gemma_needle_2000_v1/stage1/conftest.py` | `valid_envelope` helpers |
| `tests/campaigns/.../stage1/test_oracle_authority.py` | oracle receipt/authority |
| `tests/campaigns/.../stage1/test_blinded_review.py` | blinded review |
| `tests/campaigns/.../stage1/test_digest_rejects.py` | unknown pack / frozen digest |
| thin shim at current path | ≤ 90 LOC |

Move-only; no Stage-1 live gen.
