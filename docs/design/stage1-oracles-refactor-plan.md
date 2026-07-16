# `evals/.../stage1_oracles.py` refactor plan (Loop 79)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 297 |
| Projected primary LOC | ~40 facade |
| % LOC change (primary file) | **−87%** |
| Hot | `ExecutableOracleRegistry` ~137 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `evals/.../oracle_registry.py` | ExecutableOracleRegistry |
| `evals/.../oracle_receipts.py` | attest/verify_effect_receipt |
| `evals/.../oracle_digests.py` | expected_result_digest_oracle |
| `evals/.../stage1_oracles.py` | facade ≤ 40 LOC |

Move-only.
