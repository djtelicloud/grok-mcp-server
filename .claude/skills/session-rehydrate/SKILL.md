---
name: session-rehydrate
description: >-
  Boot a new IDE/agent session into full UniGrok *product* context. Activate only
  when cwd is a UniGrok product checkout (or agent worktree), or the user
  explicitly asks for product rehydrate. Triggers: "rehydrate", "boot",
  "where were we", "session start", first message after IDE reset *inside*
  product. Do not run full product rehydrate for foreign apps on stable MCP.
---

# Session rehydrate (persist intelligence across chats)

New Grok/Claude/Codex sessions do **not** inherit prior chat transcripts.
Intelligence is rehydrated from **git + disk**, not model memory.

**Product-cwd gate (mandatory):** This skill is for **developing UniGrok itself**.
If the open project is a foreign app using only stable MCP (`:4765`), do **not**
run this full product boot (no land-status pipeline, no worktree hygiene lecture,
no "Ready for supervisor" multi-agent radio). Use `using-unigrok` +
`grok_mcp_discover_self` instead. Only continue if cwd is `…/grok-mcp-server`
(or a named agent worktree under it / provider worktree home), or the user
explicitly said **product rehydrate**.

## Communication discipline (same experience every session)

Do this for the whole session after rehydrate:

1. **Think and tool silently.** Prefer tool calls and internal planning over
   step-by-step narration to the user. No diffs, patches, or tool dumps in chat.
2. **One end-state answer by default.** Use brand + status + plain task title.
   The required Rehydrated block below is an explicit boot exception. For long
   or continuously monitored work, send only short updates at meaningful state
   changes or when the user requested live updates.
3. **No fake progress essays.** “I’m checking X… now Y…” is noise. Use tools;
   then speak once per meaningful state change or finish line.
4. **Human language only to the user:** Ready / Not ready / Live / Not live /
   Blocked / Who (**brand first**) + **plain task title**. Never lead with PR
   numbers. Do not dump git jargon unless they asked for git. “Done / pushed?”
   means **Ready for supervisor**, not a git lecture. Full map:
   `.agents/AGENTS.md` → Human language.
5. **UniGrok second opinions:** when calling MCP `agent` for hard product
   claims, prefer **CLI** + `mode=fast` for index-diff hive polls; keep
   **visible emit tiny**. Insider silent-think doctrine lives in private
   `../unigrok-intelligence/playbooks/silent-think-harness.md` (not public default).
6. **Exceptions:** the required Rehydrated block below, user-requested live
   updates or explanation, safety/permission prompts, and blocking questions.

This is **session law** when this skill or `.agents/AGENTS.md` is loaded — not
global Grok TUI settings alone. Start the agent **inside the product checkout**
so these rules load.

## Boot sequence (run once at session start)

### 0. Workspace (product-cwd gate)

- Prefer cwd: product checkout `…/grok-mcp-server` or a named agent worktree.
- **Foreign app / stable MCP only:** stop product rehydrate. Tell the user once
  that product boot is for UniGrok development; offer `discover_self` +
  `using-unigrok` for their app. Do not invent land/Forge/worktree workflows.
- If cwd is `$HOME` or unclear and the user asked for product rehydrate, **say
  so once** and either open the product folder or rehydrate from absolute
  product paths only when they confirm.

### 1. Product law

Read (skim) when available:

- `.agents/AGENTS.md` — multi-agent git, MCP endpoint, credentials boundary
- Root `AGENTS.md` if present
- Root `CLAUDE.md` if present

### 2. Continuity (private brain)

If the private repo is present at a sibling path or known clone:

```text
../unigrok-intelligence/codex/continuity/active-work-latest.md
```

Also useful:

```text
../unigrok-intelligence/harvest/index-diff-hive/   # recent hive receipts
../unigrok-intelligence/playbooks/index-diff-hive.md
../unigrok-intelligence/playbooks/parallel-ship-dag.md
```

If private paths are missing, continue with public product only; do not invent
hive/land authority for public installs.

### 3. Live gates

From product root:

```bash
./scripts/land-status
```

Note: visible main, worktrees, stable/forge readiness.
Primary shared checkout should stay on **clean `main`**. Implementation uses
**agent-prefixed worktrees** only — under `<repo>/.worktrees/…` or
`/tmp/unigrok-…` (or provider homes like `~/.gemini/…/worktrees`,
`~/.codex/worktrees`). Never Documents sibling clutter. Many leftover trees =
hygiene debt; remove **yours** before starting more.

### 4. Runtime (optional quick)

```bash
curl -sf http://127.0.0.1:4765/readyz
```

Do not print secrets. Credential plane details via Control Center or
`grok_mcp_discover_self` when needed.

### 5. UniGrok MCP session key (optional)

For multi-turn `@grok` continuity this calendar day, reuse a project-qualified
session such as:

```text
djtelicloud-grok-mcp-server:ops:YYYY-MM-DD
```

Do not reuse a bare generic session key across unrelated repos.

### 6. Emit to user (only this)

A short **Rehydrated** block, then **required brand next steps**. Table alone
is incomplete — stopping after the summary is a product failure (observed when
Gemini/Antigravity and some Copilot hosts only printed the table).

#### 6a. Status table

| Field | Value |
|-------|--------|
| brand (you) | Grok / Codex / Claude / Gemini / Cursor / Copilot / … |
| cwd / branch | … |
| main / land-status | … |
| continuity | loaded / missing |
| open PRs / ready tasks | plain titles first (numbers only as footnotes) |

#### 6b. Next smartest steps (mandatory — your brand)

Immediately after the table, emit **1–2 concrete offers** you are uniquely
suited to do *now*, grounded in live gates (land-status, continuity, open
drafts, leftover **your** scratchpads, product gaps). Not generic “wait for
instructions.” Not someone else’s job.

**Format:**

```text
### Next smartest steps ([Your brand])
1. [plain English task title] — [why this brand / what benefit]
2. [optional second] — …
```

**How to pick (silent scan, then offer):**

1. Name **your brand** and its strengths from the map below.
2. Scan live product state for a **real problem or benefit** that map fits.
3. Prefer work that is ready, safe for a contributor, and human-readable.
4. If nothing fits, say so once and offer the single best hygiene or doc fix
   for **your** surface — never an empty next-action cell.

**Brand strengths map (use your row; do not invent peer authority):**

| Brand | Unique strengths | Typical smart offers after hydrate |
| --- | --- | --- |
| **Grok CLI** | UniGrok dual-plane, silent radio, MCP gateway, hive/index-diff habits, headless CLI sessions | Plane/routing truth, rehydrate/human-radio law, Control Center readiness, contributor product docs |
| **Codex** | Supervisor / `scripts/land` only when authorized; protected main; multi-agent coordination | Land Ready packets; prune safe orphans as supervisor; integration review; continuity ownership |
| **Claude Code** | Deep multi-file review, CLAUDE.md fidelity, finding real defects in code/docs | Code audit of recent Live work; fix issues it spots; skill/AGENTS consistency |
| **Gemini / Antigravity** | Large context, Google/Antigravity worktree homes, GEMINI.md surface, broad codebase read | Antigravity IDE setup fidelity; large-context architecture/docs consistency; `gemini/*` task worktrees under provider home |
| **Cursor** (local) | Multi-agent IDE, Bugbot/automations rules, hybrid local MCP | Cursor automation thrash rules; local UniGrok client id; PR approver single-pass discipline |
| **Cursor Cloud** | GitHub-only remote coding | **No** laptop secrets/tunnels; GitHub-only tasks; optional hosted twin only if connected |
| **Copilot / Kimi** (VS Code) | VS Code + Copilot instructions, fast in-editor edits | `.github/copilot-instructions.md` fidelity; VS Code MCP setup; plain-title human radio in VS Code |

**Forbidden:** table with only “next action: wait”; peer-brand work presented as
yours; supervisor land/merge as a non-Codex contributor; empty “I am ready”
with no opportunity.

Then wait for the user task — or continue if they already gave one.

## What this skill does *not* do

- Does not restore prior chat transcripts
- Does not land `main` (Codex / `scripts/land` only)
- Does not run Stage 1 live Needle gen without exact-head authorization
- Does not teach public users Forge/hive as product defaults

## Persistence checklist (end of meaningful work)

Before leaving a session that produced decisions:

1. Update private `active-work-latest.md` when continuity changed
2. Put exact head + “ready for supervisor?” on the PR
3. Leave primary checkout on clean `main`
4. Hive receipts (if any) under private `harvest/index-diff-hive/`
5. If this task is done (Live, abandoned, or new task assigned): remove **your
   own** finished worktree and prune. Do not leave disposable scratchpads.
