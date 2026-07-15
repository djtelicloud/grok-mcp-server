# Public intelligence packs

## What this is

Contributor agents are the **real model gym**. Private intelligence
(`unigrok-intelligence`) holds process IP and raw harvest. Public users who
clone this repo get **only distilled, reviewed recipes** — never private memory
dumps.

```text
private gym → scrub + review → versioned public pack → clone ships smarter defaults
```

Stable MCP (`:4765`) stays **workspace-neutral**. Packs are **product files**,
not live SQLite or contributor workspace memory.

## What never ships public

- API keys, OAuth secrets, customer data
- Raw task-RAG / session databases
- Contributor workspace memory rows
- Unreviewed chat logs or gym traces
- Competitive silent-think / hive playbooks (private intelligence only)
- Continuous auto-sync of the private repo into public history

## Promote habit

After a contributor outcome is **Live**, ask once (human or coordinator):

> Promote a public pack / skill update from this win? yes / no

If yes: add or bump a pack under `docs/public-intelligence/packs/`, update the
manifest, and open a product PR. Never force-push private history public.

## Layout

| Path | Role |
| --- | --- |
| `public-intelligence-pack.schema.json` | Pack shape + scrub rules fields |
| `packs/manifest.json` | List of published packs and their machine metadata |
| `packs/v0-*.md` | Human-readable pack bodies |

## Related

- Public vs private git: [docs/design/public-private-git-split.md](../design/public-private-git-split.md)
- Human language for agents: [.agents/AGENTS.md](../../.agents/AGENTS.md)
- Insider capsules (not public consumer DB): [docs/okf/intelligence-capsule-v1.md](../okf/intelligence-capsule-v1.md)
