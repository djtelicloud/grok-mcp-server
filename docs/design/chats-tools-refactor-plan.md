# `src/tools/chats.py` refactor plan (Loop 30)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~800 LOC**. Hot tools: `chat`, `grok_agent`, `chat_with_files`, `chat_with_vision`. Split by tool family; keep register facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 800 |
| Projected primary LOC | ~100 facade |
| % LOC change (primary file) | **−88%** |
| Classes / funcs | 2 / 15 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/tools/chat_basic.py` | chat |
| `src/tools/chat_agent.py` | grok_agent / agent wrapper |
| `src/tools/chat_media.py` | vision / files |
| `src/tools/chats.py` | register + re-exports ≤ 100 LOC |

## Migration order

basic → agent → media → register. Tool names unchanged.

## Non-goals

MCP schema changes; landing `main`.
