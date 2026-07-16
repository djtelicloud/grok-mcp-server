# `src/providers/subscription.py` refactor plan (Loop 10)

Status: **Ready for supervisor** — plan only.  
Pairs later with `tests/test_subscription_transports.py`.

## Why not a mega rewrite

**~1427 LOC**, **19** classes. Two stacks: Claude CLI process adapter and MCP client sampling. Split stacks; keep `build_subscription_registry` facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1427 |
| Bytes | 52913 |
| Classes / funcs | 19 / 16 |
| AST parse / compile | ~5 ms / ~3 ms |
| Branch nodes | 82 |
| Hot spans | `MCPClientSamplingAdapter` ~425; `ClaudeCLIAdapter` ~146; process runner helpers |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/providers/subscription_cli_process.py` | process runner, bounds, kill/reap | 200–280 |
| `src/providers/subscription_claude_cli.py` | ClaudeCLI* models + adapter | 280–400 |
| `src/providers/subscription_sampling_types.py` | sampling dataclasses / digests | 150–220 |
| `src/providers/subscription_mcp_sampling.py` | `MCPClientSamplingAdapter` + bindings | 400–550 |
| `src/providers/subscription.py` | registry builder + re-exports | ≤ 150 |

## Migration order

CLI process → Claude adapter → sampling types → MCP sampling → registry facade. Green subscription transport tests per slice.

## Risk

Delegation digests / sealed bindings are security-sensitive — move-only; no authority changes.

## Non-goals

Changing sampling authority model; landing `main`.
