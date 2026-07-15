# Public pack v0 — Rehydrate with brand next steps

**Audience:** public installers and contributor agents  
**Pack id:** `rehydrate-brand-next-steps` · **version:** `v0`

Distilled from multi-brand UniGrok gym work. Safe to ship: no secrets, no private
memory, no competitive process IP.

## Problem this pack solves

New sessions often print a **status table only**, then wait. Strong brands
(Claude, Cursor, Grok) also offered real next work; weaker loads (some Gemini /
Antigravity and Copilot / Kimi sessions) stopped at the table. Humans need both
**where we are** and **what this brand should do next**.

## Rule (mandatory after boot summary)

1. Emit a short **Rehydrated** status table (brand, workspace, live gates, open
   work as **task titles**).
2. Immediately after, emit **Next smartest steps (Your brand)** with **1–2**
   concrete offers grounded in live state and **your** strengths.
3. Table-only rehydrate is incomplete.

**Task titles, not numbers**

- Lead with plain English task names.
- Ticket or PR numbers are optional footnotes at the end, never the lead.

## Format

```text
| Field | Value |
|-------|--------|
| brand (you) | … |
| workspace | … |
| live gates | … |
| open work | plain titles first |

### Next smartest steps ([Your brand])
1. [plain English task title] — [why this brand / what benefit]
2. [optional second] — …
```

Then wait for the human — or continue if they already gave a task.

## How to pick (silent)

1. Name **your brand**.
2. Recall what you are uniquely good at (map below).
3. Scan live state for a real problem or benefit that fits.
4. Prefer safe contributor work and human-readable titles.
5. If nothing fits, offer one hygiene or docs fix on **your** surface — never an
   empty “wait.”

## Brand strengths (use your row)

| Brand | Strengths | Smart post-hydrate offers |
| --- | --- | --- |
| **Grok** | Dual-plane MCP gateway, silent human radio, product truth | Plane readiness, rehydrate/radio law, Control Center checks |
| **Codex** | Supervisor integration when authorized | Land Ready packets; safe orphan cleanup; continuity |
| **Claude** | Deep multi-file review; finds real defects | Audit recent Live work; fix 1–2 issues it spots |
| **Gemini / Antigravity** | Large context; Google/Antigravity path | IDE setup fidelity; architecture/docs consistency sweeps |
| **Cursor** (local) | Automations / Bugbot discipline; local MCP | Single-pass automations; no thrash; client hygiene |
| **Cursor Cloud** | Remote GitHub coding | No laptop secrets, tunnels, or localhost UniGrok |
| **Copilot / Kimi** | VS Code speed; Copilot instructions | VS Code MCP setup; human-radio fidelity in-editor |

## Forbidden

- Status table with only “next action: wait”
- Claiming another brand’s job as yours
- Supervisor land/merge as an ordinary contributor
- “I am ready” with no opportunity
- Leading with ticket numbers instead of task titles

## Human radio still applies

After rehydrate, normal chat stays silent human radio:

`[Brand]: [Ready for supervisor | Live | Not live | Not ready | Blocked] — [plain task title].`

The rehydrate block is the explicit boot exception; everything after still
avoids diffs, tool dumps, and progress essays unless the human asks for detail.

## Related product paths (contributors)

- Shared rehydrate skill under `.agents/skills/session-rehydrate/`
- Brand surfaces: Gemini project rules, Copilot instructions, shared agent rules
- Install and human-radio packs in this folder for adjacent recipes
