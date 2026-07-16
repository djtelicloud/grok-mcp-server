# `src/providers/errors.py` refactor plan (Loop 163)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 39 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-49%** |
| Hot | `ProviderAuthorizationInvariantError` ~9 · `ProviderError` ~5 · `ProviderTransportError` ~2 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `ProviderAuthorizationInvariantError` | extract hot path (~9 LOC) |
| split / `ProviderError` | extract hot path (~5 LOC) |
| split / `ProviderTransportError` | extract hot path (~2 LOC) |
| `src/providers/errors.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
