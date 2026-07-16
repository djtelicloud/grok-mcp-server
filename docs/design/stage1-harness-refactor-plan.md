# `evals/.../stage1_harness.py` refactor plan (Loop 8)

Status: **Ready for supervisor** — plan only.  
Campaign: `gemma_needle_2000_v1`.

## Why not a mega rewrite

**~1644 LOC**. Types/helpers small; **`Stage1SafetyHarness` ~1123 LOC / 18 methods** is the split core. Keep campaign CLI/entry stable.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1644 |
| Bytes | 68553 |
| Classes / funcs | 8 / 5 |
| AST parse / compile | ~5 ms / ~5 ms |
| Branch nodes | 57 |
| Hot class | `Stage1SafetyHarness` ~1123 LOC |

## Hive / swarm

Forge MCP disconnected — plan path. Swarm later on leaf helpers after extract.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `evals/.../stage1_types.py` | errors, result dataclasses | 80–120 |
| `evals/.../stage1_manifest.py` | `_load_and_verify_manifest`, digests/ids | 120–200 |
| `evals/.../stage1_mock_executor.py` | `DeterministicMockRoleExecutor` | 200–250 |
| `evals/.../stage1_harness_core.py` | `Stage1SafetyHarness` sliced methods | 600–900 |
| `evals/.../stage1_harness.py` | thin entry/facade | ≤ 200 |

## Migration order

types → manifest helpers → mock executor → harness method groups (run / verify / persist) → facade. Pair campaign safety tests.

## Risk

Stage-1 live Needle still auth-gated — no behavior change in extracts. Move-only.

## Non-goals

Starting Stage 1 live gen; landing `main`.
