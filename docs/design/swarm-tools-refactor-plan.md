# `src/tools/swarm.py` refactor plan (Loop 18)

Status: **Ready for supervisor** — plan only.  
Contributor swarm MCP surface.

## Why not a mega rewrite

**~1155 LOC**, **31** functions. Mixes gating, launch, status, apply, export. Split by tool lifecycle; keep register facade.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 1155 |
| Bytes | 48746 |
| Functions | 31 |
| AST parse / compile | ~5 ms / ~4 ms |
| Branch nodes | 114 |
| Hot funcs | `plan_swarm_campaign` ~112; `_status_payload` ~110; `start_paste_swarm` ~95 |

## Hive / swarm

Forge MCP disconnected — plan path. `UNIGROK_SWARM=dry_run` already desired on Forge.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/tools/swarm_gate.py` | contributor/cloudrun/mode gates | 80–120 |
| `src/tools/swarm_paths.py` | target/bench path resolution | 80–120 |
| `src/tools/swarm_launch.py` | start_code/paste_swarm launch | 250–350 |
| `src/tools/swarm_status.py` | get/list status payloads | 200–300 |
| `src/tools/swarm_apply.py` | apply_winner / cancel | 150–220 |
| `src/tools/swarm_export.py` | export_swarm_narrow_pr / plan_campaign | 200–280 |
| `src/tools/swarm.py` | register + re-exports | ≤ 120 |

## Migration order

gate/paths → launch → status → apply → export → register. Tool names unchanged.

## Risk

Triple-gate security — no gate weakening; move-only.

## Non-goals

Changing Pareto engine; landing `main`.
