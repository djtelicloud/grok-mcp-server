# `tests/test_knowledge.py` refactor plan (Loop 25)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~1021 LOC**, **10** classes, **~51** tests. Split FTS/store, distill, context injection, tools.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1021 |
| Projected primary LOC | ~100 shim |
| % LOC change (primary file) | **−90%** |
| Classes / tests | 10 / ~51 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/knowledge/test_store_fts.py` | TestKnowledgeStoreFTS |
| `tests/knowledge/test_distill_job.py` | TestDistillJob |
| `tests/knowledge/test_context_injection.py` | TestKnowledgeContextInjection |
| `tests/knowledge/test_tools.py` | TestKnowledgeTools |
| `tests/test_knowledge.py` | shim ≤ 100 LOC |

## Migration order

FTS → distill → context → tools → remaining → shim. Move-only.

## Non-goals

Knowledge policy changes; landing `main`.
