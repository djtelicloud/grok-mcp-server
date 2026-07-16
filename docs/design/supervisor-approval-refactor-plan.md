# `scripts/supervisor_approval.py` refactor plan (Loop 62)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 394 |
| Projected primary LOC | ~50 facade |
| % LOC change (primary file) | **−87%** |
| Hot | `decide_gate` ~54 · `GitHubClient` ~36 · `evaluate_pr` ~34 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/supervisor_gate.py` | decide_gate |
| `scripts/supervisor_github.py` | GitHubClient |
| `scripts/supervisor_evaluate.py` | evaluate_pr |
| `scripts/supervisor_approval.py` | CLI facade ≤ 50 LOC |

Move-only; no land/merge behavior change.
