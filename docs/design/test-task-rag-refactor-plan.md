# `tests/test_task_rag.py` refactor plan (Loop 16)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~1201 LOC**, **13** test classes, **~68** tests. Split by RAG concern (FTS, mirror, fusion, CLI, keys).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1201 |
| Bytes | 53774 |
| Classes / tests | 13 / ~68 |
| AST parse / compile | ~5 ms / ~4 ms |
| Branch nodes | 36 |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Classes / concern |
|------|-------------------|
| `tests/task_rag/test_fts.py` | FTS setup/retrieval, fallback parity |
| `tests/task_rag/test_config_mirror.py` | TaskRagConfig, TaskMemoryMirror |
| `tests/task_rag/test_fusion_semantic.py` | Fusion, SemanticRoute, GatherEvidence |
| `tests/task_rag/test_spawn_cli.py` | SpawnSyncTask, RagCli |
| `tests/task_rag/test_keys_e2e.py` | ManagementKeyWiring, Keyless, E2E |
| `tests/test_task_rag.py` | shim ≤ 100 LOC |

## Migration order

fts → config/mirror → fusion/semantic → spawn/cli → keys/e2e → shim. Move-only.

## Risk

DB/FTS fixtures — keep `safe_db` helpers shared via conftest.

## Non-goals

Changing RAG policy; landing `main`.
