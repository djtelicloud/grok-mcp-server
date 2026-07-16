# `src/rag.py` refactor plan (Loop 23)

Status: **Ready for supervisor** — plan only.  
Pairs with: test_task_rag plan #359.

## Why not a mega rewrite

**~1001 LOC**. **`TaskMemoryMirror` ~278 LOC** plus fusion/backfill helpers. Split by RAG pipeline stage.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1001 |
| Projected primary LOC | ~150 facade |
| % LOC change (primary file) | **−85%** |
| Classes / funcs | 2 / 27 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/rag/mirror.py` | TaskMemoryMirror | 250–320 |
| `src/rag/fusion.py` | fuse_task_evidence | 80–120 |
| `src/rag/semantic.py` | gather_semantic_evidence | 80–120 |
| `src/rag/backfill.py` | `_rag_backfill` | 80–120 |
| `src/rag.py` | facade re-exports | ≤ 150 |

## Migration order

mirror → fusion → semantic → backfill → facade. Pair #359 tests.

## Non-goals

RAG policy changes; landing `main`.
