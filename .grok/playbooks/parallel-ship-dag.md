# Parallel ship DAG (Grok Build + UniGrok)

Human speaks product intent only. Agents own git. Codex owns land on `main`.

## Lanes

| Lane | Mission | UniGrok modes | Path ownership (examples) |
| --- | --- | --- | --- |
| **P — Public product** | Console, onboarding, hosted review wiring, README/OKF | `fast` glue · `reasoning` contracts · `thinking` multi-file | `mcp_ui/`, `docs/ide-setup.md`, `docs/chatgpt-github-app.md`, `docs/design/hosted-*`, workflows for review |
| **I — Intelligence** | AKE, Needle gates (fail-closed), provider adapters, evals | `research` gather · `thinking` synthesize · `reasoning` product implications | `src/providers/`, campaign packs, `evals/`, design under `docs/design/ake*` / needle |

Never mix P and I in one PR. Parallel = two worktrees / two branches / two draft PRs.

## UniGrok multi-model use (honest limits)

- Public MCP `agent` is **Grok-routed** (API or CLI plane). Modes: `auto` | `fast` | `reasoning` | `thinking` | `research`.
- Multi-provider adapters (OpenAI / Anthropic / Gemini) exist for **Grok-supervised** internal work; do not invent client-facing multi-provider chat.
- Fan-out pattern for hard decisions:
  1. `thinking` or `reasoning` — primary plan (CLI first when free).
  2. Optional second call `research` — only if facts need web/X.
  3. Optional deep-think `thinking` + `plane=api` — when subscription is wrong tool or user asks Deep-Think.
- Always log `cost_usd`, `plane`, `model` in PR notes when API spend is non-zero.

## Handoff triad (byte-safe agent bus)

Every finished lane must leave **all three** on GitHub:

1. **Draft PR** (agent-prefixed branch only)
2. **Exact head SHA** in body
3. **Notes:** changed paths, tests, risks, cost, `ready for Codex land? yes/no`

Codex lands only from that triad. Humans do not re-explain git.

## Cost caps (session defaults)

| Lane | Default posture |
| --- | --- |
| P | Prefer CLI plane; API only for plan critique or hosted twin work |
| I | Harder cap; no live Needle gen without explicit authorization |
| Review | Explicit `@grok review` / dispatch only; never auto on every push |

Hard-stop: when budget or scope slips, freeze lane, update PR notes, open no new API fan-out.

## Conflict rules

- Shared read of `main`; exclusive write by path table above.
- If lanes collide: I yields product claims; rebase on new exact head; re-handoff.
- Never silent overwrite of another agent’s branch.

## Never

- Auto-merge / push shared `main` / run `scripts/land` as Grok
- Tunnel-as-production
- Ask the human to run git commands
- Invent provider endpoints on the public agent tool
