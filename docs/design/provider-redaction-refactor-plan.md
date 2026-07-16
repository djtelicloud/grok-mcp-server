# `src/provider_redaction.py` refactor plan (Loop 165)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 37 |
| Projected primary LOC | ~20 facade |
| % LOC change (primary file) | **-46%** |
| Hot | `capture_provider_redaction_snapshot` ~10 · `ProviderRedactionSnapshot` ~9 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `capture_provider_redaction_snapshot` | extract hot path (~10 LOC) |
| split / `ProviderRedactionSnapshot` | extract hot path (~9 LOC) |
| `src/provider_redaction.py` | facade ≤ 20 LOC |

Move-only. Leave PR #408 alone.
