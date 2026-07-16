# `tests/test_completion_envelope.py` refactor plan (Loop 32)

Status: **Ready for supervisor** — plan only. Pairs with #374.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 733 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~31 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/completion_envelope/test_structured_schema.py` | nested defs/unions |
| `tests/completion_envelope/test_variants.py` | extras/discriminate |
| `tests/completion_envelope/test_evidence_policy.py` | evidence policy |
| `tests/completion_envelope/test_local_refs.py` | percent-decoding/rebasing |
| `tests/test_completion_envelope.py` | shim ≤ 90 LOC |

Move-only; pair #374 extracts.
