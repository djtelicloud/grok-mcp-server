# `tests/test_publish_okf_wiki_mirror.py` refactor plan (Loop 144)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 89 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-61%** |
| Hot | `test_build_mirror_prunes_stale_pages_and_covers_manifest` ~41 · `test_subscription_auth_precedes_readiness_check_in_public_docs` ~9 · `test_invalid_json_error_names_the_artifact` ~5 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_build_mirror_prunes_stale_pages_and_covers_manifest` | extract hot path (~41 LOC) |
| split / `test_subscription_auth_precedes_readiness_check_in_public_docs` | extract hot path (~9 LOC) |
| split / `test_invalid_json_error_names_the_artifact` | extract hot path (~5 LOC) |
| `tests/test_publish_okf_wiki_mirror.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
