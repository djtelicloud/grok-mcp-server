# `tests/test_swarm_ast.py` refactor plan (Loop 128)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 128 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-73%** |
| Hot | `TestExtractSpan` ~51 · `TestByteReplacement` ~16 · `TestSignatureFingerprint` ~14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `TestExtractSpan` | extract hot path (~51 LOC) |
| split / `TestByteReplacement` | extract hot path (~16 LOC) |
| split / `TestSignatureFingerprint` | extract hot path (~14 LOC) |
| `tests/test_swarm_ast.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
