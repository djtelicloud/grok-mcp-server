# Public pack — Install one-liner and any-project agents

**Audience:** public installers  
**Pack id:** `install-and-any-project` · **version:** `v0`

## Fastest install (LIVE target path)

UniGrok is a **small always-on service** on your machine, not a one-file IDE plugin.

```bash
git clone https://github.com/djtelicloud/grok-mcp-server.git
cd grok-mcp-server
uv run python main.py init          # .env + IDE paste blocks
docker compose up --build -d
```

Before the readiness check, choose one credential path: put the xAI developer
key in server `.env` (then restart Compose), or authenticate the CLI subscription
after the image is up:

```bash
docker compose run --rm grok-cli-auth
curl -sf http://localhost:4765/readyz
```

API users skip the CLI-auth command and run the same readiness check. CLI-only
installs must authenticate before `/readyz` can report a usable model plane.

Then paste the MCP block from `init` into your IDE (URL `http://localhost:4765/mcp`,
stable `X-Client-ID`, **never** put the xAI key in IDE JSON).

You can **close the clone** after the service is running. Daily work is in
**your** projects with `@grok` / the UniGrok `agent` tool.

`npx` is **not** the server today. A thin launcher may wrap Docker later; do not
`pip install mcp-grok` from PyPI (unrelated package).

## First agent connect (what actually loads)

1. IDE gets **tool list** only.
2. Good agents call **`grok_mcp_discover_self`** → bootstrap + OKF pointers.
3. OKF: https://grokmcp.org/docs/okf/index.md (also on the server under `/docs/okf/`).
4. **No** automatic full-wiki dump. Optional GitHub Wiki is an OKF **mirror** only.

## Any project (not the UniGrok clone)

You do **not** need the UniGrok `.agents/` tree in your app.

**Recommended:** install the small **using-unigrok** skill (or paste the habit
below) so agents use UniGrok without becoming UniGrok contributors.

### Optional habit (opt-in, not forced)

When the user says `@grok` or wants a second opinion:

1. Call UniGrok `agent` (start with `mode=fast` or `reasoning`).
2. For multi-step plans, get a UniGrok second opinion **if the user wants that
   habit** — do not silently burn metered API credits.
3. Hive / deep-think / parallel voting = **opt-in** for hard problems, not every
   turn in foreign apps.
4. Speak **Ready / Live / Blocked / Who (brand)** + plain task titles.

## Task titles, not numbers

Lead status updates with the provider brand, status, and a plain task title.
Ticket or PR numbers are optional link footnotes, never the story.

### Do not copy into foreign apps

- Full contributor `.agents/`, Forge, Swarm, land workflows
- Private intelligence playbooks
- Laptop secrets into Cursor Cloud

## Wiki

If the GitHub Wiki has pages, they are a **mirror** of OKF + public packs.
Source of truth remains the repo and the site.
