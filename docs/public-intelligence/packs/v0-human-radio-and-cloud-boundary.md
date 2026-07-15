# Public pack v0 — Human radio and cloud boundary

**Audience:** public installers and contributor agents  
**Pack id:** `human-radio-and-cloud-boundary` · **version:** `v0`

Distilled from real multi-agent gym work. Safe to ship: no secrets, no private
memory, no competitive process IP.

## 1. Talk to humans this way

| Human says | You mean | Say back |
| --- | --- | --- |
| Done? / pushed? / finished? | Is it with the coordinator? | **Ready for supervisor** / **Not ready** (+ one plain blocker) |
| Live? / shipped? | Is it the real product? | **Live** / **Not live** (+ one plain reason) |
| Who did this? | Which brand? | **Grok / Codex / Claude / Cursor / …** first — not “you wrote it” when an agent did |
| Clean up? | Too many folders | **Cleaned** / **Left X (why)** |

Do **not** lecture about branches, worktrees, rebases, remotes, or land unless
the human asked for technical detail.

**Example finish line**

> I finished building X. Tests passed. Sent to coordinator under PR #N — Title.  
> If the goal is A, next is B. If the goal is C, next is D.

## 2. Cursor Cloud vs laptop UniGrok

| Mode | UniGrok |
| --- | --- |
| Local Cursor / laptop agents | Shared gateway on the machine (default loopback MCP) |
| Cursor Cloud agents on GitHub | GitHub is enough to code. **No** laptop secrets, **no** tunnel, **no** `localhost` UniGrok |
| Hosted twin (optional later) | `https://mcp.grokmcp.org/mcp` — server holds the xAI key; agent only needs a proper connect path |

Never paste a full developer `.env` into Cursor Cloud secrets.

## 3. Scratchpads, not second homes

- One task → one temporary workspace under the product’s hidden scratch layout
  (or the provider’s own worktree home).
- When the task is Live, abandoned, or a new task starts → remove **your own**
  finished scratchpad.
- Never delete another agent’s live workspace or the primary product folder.

## 4. Always UniGrok ≠ auto public intelligence

Using UniGrok for planning helps **your** gym and routing. Public stable MCP
stays **workspace-neutral**. Only **reviewed packs and skills** in this repo
reach new public clones—not private SQLite or chat logs.

## 5. Promote habit (contributors)

After something is **Live**, ask once: promote a public pack/skill update?
If yes, scrub and open a product PR under `docs/public-intelligence/`.

## Silent final answer

Do all work off-chat. When finished, emit **only**:

`[Brand]: [Ready for supervisor | Live | Not ready | Blocked] — [plain task title].`

No diffs, logs, or “I’m checking…” in human chat. Expand only if asked.
