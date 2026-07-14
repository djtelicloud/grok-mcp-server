# Public vs Insider surfaces (product freeze)

- **Status:** Accepted product freeze for Wave-1 docs and onboarding
- **Date:** 2026-07-14
- **Decision owner:** Project maintainer (human sponsor), with Codex as
  integration authority for land/merge
- **Scope:** Audience split, surface trust zones, LIVE vs TARGET claims
- **Non-scope:** Provider/broker implementation, full Console rewrite, OAuth
  enforcement code changes (separate PRs)

This document is the **language source of truth** for what public docs and
stable `discover_self` may say. Live `tools/list` remains authoritative for
tool availability.

## 1. Terms

| Term | Meaning |
|---|---|
| **PUBLIC** | Vibe coders and IDE agents using UniGrok as a product |
| **INSIDER** | GitHub collaborators on `djtelicloud/grok-mcp-server` with write+ intent (admin, maintain, write) |
| **LIVE** | Behavior shipped and true today |
| **TARGET** | Intended end-state; must not be claimed as LIVE until enforced |

## 2. Duality matrix (must not collapse)

| Axis | PUBLIC story | LIVE reality |
|---|---|---|
| **Audience** | End user / IDE agent | Contributor developing UniGrok |
| **HTTP surface** | One primary endpoint `http://localhost:4765/mcp` | Stable Core `:4765` + contributor Forge `:4766` |
| **Credential plane** | API key **or** SuperGrok CLI (or both) — **public-critical** | Same dual planes + management key for Collections admin |
| **Transport** | Loopback Streamable HTTP MCP primary | Also trusted stdio for full contributor tool surface |

Phoneword mode-dial ports (if enabled) are **aliases of the same Core service**,
not a second product. They must never be sold as “another UniGrok to install.”

## 3. Authority split

| Authority | Owner | Notes |
|---|---|---|
| **Intent / semantic judgment** | Grok via UniGrok `agent` | Within credentials, spend, and granted tools |
| **Trust / mutation** | Hard code + human + Codex land/merge gates | Grok review is **advisory**, never merge authority |
| **Evidence** | Verified receipts, tests, CI | Unverified model text is not success |

Slogan for **stable/public**: *code is context and guardrails; Grok decides
semantics.* Forge can mutate the UniGrok checkout only under explicit
contributor gates — that is not the public product.

## 4. Surfaces

### 4.1 Stable Core (`:4765`) — PUBLIC + machine owner

**LIVE:**

- MCP `agent`, status, discovery, optional PR review tool
- Workspace-neutral (no automatic browse of the user’s app repo)
- Loopback Control Center at `/ui/` for **this machine’s** health, cost ledger,
  and optional playground
- Credentials stay in server env / CLI OAuth volume — never in IDE MCP JSON

**TARGET Console (local Core UI):**

- Observe health + paste next actions
- Not a second primary Grok chat for daily work (IDE MCP is primary)

**LIVE Console note:** the bundled UI may still call `agent` (legacy operator
playground). Label that as **legacy**; do not document it as the only chat path.

### 4.2 Contributor Forge (`:4766`) — INSIDER only

**LIVE:**

- Same MCP tools as Core **plus** contributor-only workspace memory / Swarm
  (when mode enabled)
- Mounts the **UniGrok** checkout, never arbitrary customer projects by default
- Not required for public install or public README

Public docs and **stable** `discover_self` prose must **not** instruct agents to
“start 4766,” enable Swarm, or land to main.

### 4.3 Cloud control (`control.grokmcp.org` / site control)

**LIVE:** GitHub OAuth control plane with live collaborator checks; no provider
secret forms; no laptop proxy of local MCP.

**TARGET:** Raise privileged Console entry to GitHub permission ∈
`{write, maintain, admin}` where product requires write-only insiders; fail
closed; short cache; never long-lived “is_insider” without recheck.

Cloud must **never** accept, store, or proxy `XAI_API_KEY`, CLI OAuth material,
or gateway secrets.

### 4.4 IDE MCP chat — sole primary chat path

**PRIMARY product chat** for PUBLIC and INSIDER daily work:

```text
IDE coding agent → UniGrok MCP `agent` @ http://localhost:4765/mcp
```

Browser tunnels for ChatGPT/remote MCP are **advanced private** surfaces, not
public Quick Start.

## 5. Onboarding rules

### PUBLIC

1. Outcome-first install: service up → `/readyz` → IDE points at `:4765/mcp`.
2. Credential: **API or CLI** (or both). Dual-plane economics stay visible.
3. Docker (when used) is **packaging**, not identity.
4. Caller projects need **no** `.agents` / UniGrok repo tree.
5. Optional skill packs that prefer `@grok` plan critique are **opt-in**,
   permissioned, and must not silently rewrite global agent config.
6. Never auto-inject “silent” paid plan critique without consent.

### INSIDER

1. Documented in `CONTRIBUTING.md` and this freeze — not the public README body.
2. Worktrees, land, Swarm, Forge, OKF deep dives, multi-agent coordination.
3. Visual / Console checks before draft PRs are encouraged; they do not replace
   tests or Codex land.

## 6. Security invariants (all waves)

1. No provider credentials in browser, `NEXT_PUBLIC_*`, IDE MCP configs, or skills.
2. Cloud Control never proxies local MCP or house xAI credentials.
3. Stable Core stays workspace-neutral for customer projects.
4. Write access ≠ Forge power; Forge stays local contributor gates.
5. Grok output never merges, pushes `main`, or runs cloud shell.
6. Generated skills are untrusted content; no default `curl | bash`.
7. Public status APIs stay sanitized (no other users’ sessions, no secret material).

## 7. Wave-1 freeze status (what this train may claim)

| Claim | Status |
|---|---|
| Public README is vibe-only; insider ops in CONTRIBUTING | Wave-1 docs goal |
| Stable discover prose does not teach Forge connect | Wave-1 onboarding goal |
| Console is observe+paste only | **TARGET** (legacy chat still LIVE) |
| GitHub write+ for all cloud Console entry | **TARGET** (verify LIVE gate separately) |
| Unified single Insider UI (swarm+control+core) | **Deferred** |
| Silent skill compiler into user repos | **Forbidden** |

## 8. Related docs

- [ADR 0001 cloud control plane](../adr/0001-cloud-control-plane-governance.md)
- [Threat model](../threat-model.md)
- [Authority inversion](authority-inversion.md)
- [IDE setup](../ide-setup.md) (insider dual-port detail)
- [CONTRIBUTING.md](../../CONTRIBUTING.md)
