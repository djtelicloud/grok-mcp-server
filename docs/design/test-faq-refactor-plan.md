# `tests/test_faq.py` refactor plan (Loop 136)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 111 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-68%** |
| Hot | `test_faq_document_rejects_entries_without_keywords` ~14 · `test_agent_faq_lookup_returns_api_only_mode_plane_guidance` ~14 · `test_faq_document_parses_release_versioned_entries` ~10 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_faq_document_rejects_entries_without_keywords` | extract hot path (~14 LOC) |
| split / `test_agent_faq_lookup_returns_api_only_mode_plane_guidance` | extract hot path (~14 LOC) |
| split / `test_faq_document_parses_release_versioned_entries` | extract hot path (~10 LOC) |
| `tests/test_faq.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
