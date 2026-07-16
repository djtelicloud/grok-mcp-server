# `src/tools/faq.py` refactor plan (Loop 151)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 77 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-55%** |
| Hot | `lookup_unigrok_faq` ~57 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `lookup_unigrok_faq` | extract hot path (~57 LOC) |
| `src/tools/faq.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
