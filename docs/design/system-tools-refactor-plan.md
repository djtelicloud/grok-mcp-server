# `src/tools/system.py` refactor plan (Loop 9)

Status: **Ready for supervisor** — plan only.

## Why not a mega rewrite

**~1525 LOC**, **33** functions, no classes. Mixes status/discover/restart with search/file/test helpers. Split by tool family; keep `register_system_tools` facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1525 |
| Bytes | 66752 |
| Functions | 33 |
| AST parse / compile | ~6 ms / ~5 ms |
| Branch nodes | 114 |
| Hot funcs | `grok_mcp_status` ~265; `grok_mcp_discover_self` ~208; `_build_discover_bootstrap` ~128 |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/tools/system_status.py` | `grok_mcp_status`, runtime stats text | 280–350 |
| `src/tools/system_discover.py` | discover_self + bootstrap/request_context helpers | 350–450 |
| `src/tools/system_restart.py` | `grok_mcp_restart_container` | 100–130 |
| `src/tools/system_search.py` | `web_search`, `x_search` | 100–150 |
| `src/tools/system_workspace.py` | list/read files, local tests, RCE | 200–300 |
| `src/tools/system_models_files.py` | models list, xAI file upload/get | 120–180 |
| `src/tools/system_chat_history.py` | get/clear chat history | 50–80 |
| `src/tools/system.py` | `register_system_tools` + re-exports | ≤ 120 |

## Migration order

discover/status helpers → workspace/search → models/files → chat → restart → register facade. Tool names unchanged.

## Risk

Public MCP tool surface must stay identical. Move-only; no schema changes.

## Non-goals

Changing discover bootstrap contract; landing `main`.
