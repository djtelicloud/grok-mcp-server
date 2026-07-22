# Public stranger surface (freeze)

**Status:** Ready  
**Who:** UniGrok public OSS only  
**Not this product:** Space-Command, Sky-Command, private C2

## What a stranger installs

One local UniGrok teammate on **Ground port only**:

| Item | Public value |
|------|----------------|
| MCP URL | `http://localhost:4765/mcp` |
| Ready check | `http://localhost:4765/readyz` |
| Control Center | `http://localhost:4765/ui/` |
| Install path | Docker + `docker compose` (see root README) |
| Credentials | Grok Build login and/or optional `XAI_API_KEY` in **container env only** |

## What strangers never get

Do **not** document, ship, or auto-wire these into the public install:

- Space-Command (`:4769`, `@spacegrok` / `@spacecommand`)
- Sky-Command (`:4768`, `@skygrok`)
- Forge private product plane (`:4766`) as a C2 requirement
- Management / org admin keys
- Sky orchestration recipes, Ground supervision freezes, sponsor C2 sessions
- Heartbeat / swarm / 15-minute C2 automations

Public UniGrok is a **single-node teammate**. Private C2 (Space → Sky → Ground) is a **separate product stack**.

## Intelligence tiers (do not blur)

| Tier | Audience | Content |
|------|----------|---------|
| **Starting intelligence** | Public installers | Only **promotion-gateway** sanitized learnings from dogfood (`@grok` / helper patterns) that improve core UniGrok |
| **Contributor / forge** | GitHub forge insiders | May use supervised **@skygrok** — not part of stranger install |
| **Paid higher intelligence** | Paying customers | Advanced layers **forever private** — never OSS default |

Strangers never install Sky/Space. Custom models (e.g. Gemma helpers) reach public only as **opt-in tools under @grok** after gateway — not as C2 brains.

## Stranger success test

1. `docker compose up -d grok-mcp`
2. `curl --fail --silent http://localhost:4765/readyz` → ready
3. IDE MCP points only at `:4765/mcp`
4. One `@grok` turn works
5. No Space/Sky ports required

## Leak check (must stay true)

- README install steps mention **4765 only** for the default product.
- No public doc requires David’s C2 repos (`docker-grok-sky-command`, `docker-grok-space-command`).
- SECURITY.md public boundary still holds (no shell/Git/project authority by default).

## Next (not this freeze)

- Optional nicer launcher (`npx @djtelicloud/unigrok`) — later
- SpaceCommand UI — private product, after C2 loop policy is settled
- TD-001 Ground trickle test — sponsor-gated, not public

Recorded: 2026-07-21 · TerminalGrok after Space direction (public install / no C2 leak)
