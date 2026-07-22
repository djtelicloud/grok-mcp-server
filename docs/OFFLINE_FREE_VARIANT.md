# Free / offline / low-auth UniGrok variant

**Status:** Design Live · product path under Ground dogfood  
**Goal:** Useful IDE @grok loops without burning premium provider quotas; work when network/auth is limited.

## Modes

| Mode | Needs | Can do |
|------|--------|--------|
| **Full dual-plane** | Grok Build login and/or `XAI_API_KEY` | Full agent tools, hive max, dual-plane |
| **CLI-only** | Grok Build login | Subscription plane tools/thinking |
| **Offline helper** | Local `gemmagrok-local` :4777 (or future in-process local plane) | Chat/status thinking assist; no remote fallback |
| **Local plane binds** | Seeded offline models in SQLite local plane | Certified offline roles when ready_candidate |

## Hard rules

1. **Auth failure never fails over to local** (security)  
2. Offline/local is **opt-in** or explicit offline flag — not silent swap of @grok identity  
3. Public stranger install stays :4765; helpers optional after gateway  
4. C2 multi-provider auths **never** ship in free OSS path  

## IDE usage (quota save)

Route **thinking + sub-agent execution** through **@grok MCP** so Cursor/Claude/Codex do not each burn separate premium loops for the same work.

## GemmaGrok role

Dogfood local peer that becomes the **free offline helper** after benches + promote gate — still **under** @grok as tool/sub-agent, not a second public brand.
