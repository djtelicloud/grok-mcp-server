# `tests/test_subscription_transports.py` refactor plan (Loop 15)

Status: **Ready for supervisor** — plan only.  
Pairs with: `docs/design/subscription-providers-refactor-plan.md` (#353).

## Why not a mega rewrite

**~1235 LOC**, **~26** tests, 1 fake runner. Split Claude CLI vs sampling stacks to match provider modules.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1235 |
| Bytes | 42750 |
| Tests | ~26 |
| AST parse / compile | ~4 ms / ~3 ms |
| Branch nodes | 19 |
| Dense clusters | process_runner, sampling_*, claude_*, subscription_lane |

## Hive / swarm

Forge MCP disconnected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/providers/conftest.py` | `CapturingRunner` (+ shared fakes) |
| `tests/providers/test_cli_process_runner.py` | process_runner |
| `tests/providers/test_claude_cli_adapter.py` | claude_cli / environment |
| `tests/providers/test_sampling_bindings.py` | sampling_binding/descriptor/grant/ttl |
| `tests/providers/test_sampling_rejects.py` | sampling_rejects / request |
| `tests/providers/test_subscription_lane.py` | subscription_lane / attempt |
| `tests/test_subscription_transports.py` | shim ≤ 100 LOC |

## Migration order

conftest → process → claude → sampling → lane → shim. Move-only; pair #353.

## Risk

Sealed binding / digest assertions — no semantic edits.

## Non-goals

Authority model changes; landing `main`.
