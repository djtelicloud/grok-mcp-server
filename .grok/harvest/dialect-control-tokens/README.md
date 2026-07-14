# Dialect control-token library (harvest)

**Status:** Reference harvest for UniGrok silent-think. **Not** wired into the
MCP runtime. Public users never see this.

## What this is

Donor GenFunc name: **Native Latent IPC / Token Control Language**.

| File | Role |
| --- | --- |
| `dialect_matrix.json` | Per-family control tokens / prefills / locks |
| `dialect_compiler.py` | Compiles mission + payload into a control-token prompt |
| `PROVENANCE.txt` | Exact donor commits |

This is the library you meant by “control tokens”: not a pip package, not
OAuth, not CSS design tokens.

## How it works

`DialectCompiler.compile(family, mission, payload, force_tool|force_reasoning)`:

1. **system_lock + safety_bypass** — family-native structural prefix (PUA
   chars, chat-template specials, or harsh system text for Gemma).
2. **mission + compact JSON payload**
3. **assistant_force** prefill (start of assistant turn)
4. **tool_coercion_open** *or* **reasoning_start**

Visible completion is steered by the prefill. Intelligence cost stays in
internal thinking / small-model compute when paired with tiny max-out and
schema emit (see silent-think playbook).

## Families in matrix v1.1.0

`gemini`, `gemma`, `llama3`, `grok`, `mistral`, `openai_o1`.

Gemma dialect was evolved by `swarm_dialect_optimizer.py` (loop until dense
functional emit, plateau=3) — the harness that forced smaller models below
normal cost/fluff.

## Related donor pieces (not copied; recover from GenFunc git)

| Piece | Commit / path |
| --- | --- |
| Seed script | `5328eb7769d` `v13_dialect_seed.py` |
| Omni router wire | `c6921b45dd1` `.agent/brain/omni_router.py` |
| Lexical crucible | `20344a7451c` `lexical_crucible.py` |
| Dialect optimizer | `8d4f7b3a4fc` `swarm_dialect_optimizer.py` |

## Runtime cousins (not this harvest)

- **mlx_lm** `_process_control_tokens` / `_infer_thinking` — strips
  `<think>`…`</think>` etc. from local stream output.
- Provider APIs: `ThinkingConfig.include_thoughts`, thinking budget vs
  `max_output_tokens`.

## UniGrok port rules

1. Keep harvest under `.grok/harvest/` until a design PR lands a runtime
   module under `src/` with tests.
2. Prefer **safe** control surfaces for hosted paths: chat-template specials
   and documented thinking APIs — not experimental PUA “safety_bypass”
   tokens on public traffic.
3. Pair with tiny pydantic emit + silent-think receipts
   (`thought_tokens` / `completion_tokens`).
4. Insider / contributor only; public root-as-lava stays clean.

## Playbooks

- [silent-think-harness.md](../../playbooks/silent-think-harness.md)
- [donor-genfunc-deep-harvest.md](../../playbooks/donor-genfunc-deep-harvest.md)
