# `src/swarm/sandbox.py` refactor plan (Loop 80)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 296 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `SwarmSandbox` ~225 LOC |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/swarm/sandbox_bench.py` | parse_bench_line |
| `src/swarm/sandbox_runtime.py` | SwarmSandbox |
| `src/swarm/sandbox.py` | facade + SandboxError ≤ 35 LOC |

Move-only.
