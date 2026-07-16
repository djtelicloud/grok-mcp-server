# `tests/test_xai_client_authority.py` refactor plan (Loop 74)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 334 |
| Projected primary LOC | ~45 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/xai_client/test_inference_ctor.py` | inference never uses management env |
| `tests/xai_client/test_management_factory.py` | aliases / thread-safety |
| `tests/xai_client/test_eval_wrap.py` | eval wraps inference not management |
| `tests/test_xai_client_authority.py` | shim ≤ 45 LOC |

Move-only.
