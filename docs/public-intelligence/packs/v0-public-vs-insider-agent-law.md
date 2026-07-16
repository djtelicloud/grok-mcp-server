# Public pack — Public vs insider agent law

**Audience:** public installers and any agent connected only to stable MCP  
**Pack id:** `public-vs-insider-agent-law` · **version:** `v0`

Distilled from multi-brand gym work on audience split. Safe to ship: no secrets,
no private memory, no competitive process IP.

## Problem this pack solves

Users who only want Grok chat in **their** apps sometimes open the UniGrok
clone (or trigger **rehydrate**) and the agent starts acting like a UniGrok
**contributor** — land, worktrees, Forge, dual ports, multi-agent radio.

**Runtime tools on stable MCP are mostly fine.** The leak is **instruction
files and skills** when the agent’s cwd is the product repo or product boot runs
in a foreign app.

## Two audiences

| Audience | Should load |
|----------|-------------|
| **Vibe / stable MCP client** | README paste + optional `using-unigrok` + `grok_mcp_discover_self` |
| **Insider (building UniGrok)** | Full `AGENTS.md` / `.agents/*`, product rehydrate, land, worktrees |

## Rules for stable clients (`:4765` only)

1. Use **port 4765 only**. Do **not** invent **4766**, Forge, Swarm, land, or
   dual-runtime for ordinary installs.
2. Prefer **`using-unigrok`**. Call **`grok_mcp_discover_self`** first.
3. Honor gates: when `can_mutate_workspace` and `can_use_swarm` are **false**,
   **contributor workflows are disabled**.
4. Do **not** run product **session-rehydrate** outside a UniGrok product
   checkout (or an explicit “product rehydrate” request).
5. Status language for **their** apps: **Done / Blocked / plain English** — not
   multi-agent **Ready for supervisor** land radio unless they ship UniGrok.

## Task titles, not numbers

Lead updates with brand + status + plain task title. Ticket numbers are
optional footnotes, never the lead.

## What stays insider-only

- Full multi-agent land / worktree / draft-PR pipeline
- Product session-rehydrate brand next-steps map
- Private intelligence playbooks and hive process
- Forge / Swarm tooling on contributor surfaces

## Success check

| Situation | Pass |
|-----------|------|
| Foreign app + stable MCP | agent / discover only; no land or Forge story |
| UniGrok product checkout | full insider law available |
| Rehydrate in foreign app | no product land pipeline invented |
| Vibe status language | not forced “Ready for supervisor” |

## Related packs

- `install-and-any-project` — close the clone after install
- `human-radio-and-cloud-boundary` — Ready / Live / Blocked map for product work
- `rehydrate-brand-next-steps` — **product** rehydrate only (cwd-gated)
