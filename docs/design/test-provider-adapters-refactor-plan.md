# `tests/test_provider_adapters.py` refactor plan (Loop 21)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~1061 LOC**, **~30** tests. Split by provider adapter family (OpenAI, Gemini/Vertex, registry).

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1061 |
| Bytes | 36625 |
| Tests | ~30 |
| AST parse / compile | ~4 ms / ~3 ms |
| Branch nodes | 18 |
| Dense | gemini/vertex endpoints, openai normalize, registry inert |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/providers/test_openai_adapter.py` | openai endpoint/refusal/length |
| `tests/providers/test_gemini_vertex_adapter.py` | gemini/vertex ADC/endpoints |
| `tests/providers/test_adapter_registry.py` | registry inert/complete/secret-free |
| `tests/test_provider_adapters.py` | shim ≤ 100 LOC |

## Migration order

openai → gemini/vertex → registry → shim. Move-only.

## Risk

Receipt/secret-free assertions — no weakening.

## Non-goals

Adapter behavior changes; landing `main`.
