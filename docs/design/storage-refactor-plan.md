# `src/storage.py` refactor plan (Loop 75)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 342 |
| Projected primary LOC | ~40 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `SessionStoreProtocol` ~281 · `get_store` ~22 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/storage_protocol.py` | SessionStoreProtocol |
| `src/storage_factory.py` | get_store |
| `src/storage.py` | facade ≤ 40 LOC |

Move-only.
