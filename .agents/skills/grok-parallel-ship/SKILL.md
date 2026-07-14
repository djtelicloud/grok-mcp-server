---
name: grok-parallel-ship
description: Parallel dual-lane shipping for UniGrok contributor work—public product and intelligence—using UniGrok agent modes, git-DAG handoffs, and Codex as sole land gate. Activate when the user wants both product and intelligence, multi-agent velocity, or to keep Codex as a boring lander.
---

# Grok parallel ship

Use with repository playbook
[`.grok/playbooks/parallel-ship-dag.md`](../../../.grok/playbooks/parallel-ship-dag.md)
and adapter [`.grok/prompts/grok_adapter.md`](../../../.grok/prompts/grok_adapter.md).

## Roles

| Actor | Owns |
| --- | --- |
| Human | Product intent, budgets, ship/no-ship policy |
| Grok Build (and peer IDE agents) | Implementation in agent-prefixed worktrees, draft PRs, advisory review |
| UniGrok `agent` | Multi-mode Grok reasoning/research (and internal provider adapters when routed) |
| Codex | `scripts/land`, protected `main`, release/deploy gates |

## Activate dual lanes

1. Confirm two non-overlapping path sets (P vs I).
2. Create two worktrees / branches (`grok/*` or other agent prefix).
3. For hard design forks, call UniGrok:
   - Lane P: `mode=thinking` or `reasoning`, prefer CLI
   - Lane I: `mode=research` then `thinking`, budget-capped
4. Never ask the human to run git. Leave the **handoff triad** on each PR:
   URL + exact head SHA + tests/risks/cost + land readiness.

## UniGrok modes (do not invent provider APIs)

- `fast` — single-turn cheap
- `reasoning` — multi-step product/intel analysis
- `thinking` — reflected deep critique / architecture
- `research` — web/X grounded fan-out (costlier)

Public tool stays Grok-routed. Multi-provider adapters are not a second chat product.

## Cost

- Prefer `plane=cli` + `fallback_policy=same_plane` for free-compatible work.
- API plane only when required (hosted twin, Deep-Think, metered models).
- Record non-zero `cost_usd` in PR notes. Hard-stop on budget.

## After Codex lands

Recall/record workspace memory only against the landed full SHA (see
`unigrok-workspace-memory` skill). Do not treat draft-PR SHAs as landed truth.
