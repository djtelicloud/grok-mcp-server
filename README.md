<div align="center">

<img src="assets/hero.svg" alt="UniGrok · one Grok server, every IDE, zero pasted API keys" width="100%"/>

[![CI](https://img.shields.io/github/actions/workflow/status/djtelicloud/grok-mcp-server/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/djtelicloud/grok-mcp-server/actions)
[![Release](https://img.shields.io/github/v/release/djtelicloud/grok-mcp-server?style=flat-square&label=release)](https://github.com/djtelicloud/grok-mcp-server/releases)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?style=flat-square)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-black?style=flat-square)](https://modelcontextprotocol.io)
[![xAI Grok](https://img.shields.io/badge/xAI-Grok-000000?style=flat-square)](https://docs.x.ai/?utm_source=github&utm_medium=readme&utm_campaign=unigrok&utm_content=badge-docs)

<br/>

<img src="assets/control-center-demo.gif" alt="UniGrok Console — agent run with live tokens, cost, latency, route, and plane metadata" width="100%"/>

</div>

# UniGrok — Grok for every coding agent

**For vibe coders first.** One Grok gateway on your laptop. Point Cursor, Claude,
VS Code, Codex, or any MCP agent at it. Your xAI key stays on the server — never
in the IDE.

```text
http://localhost:4765/mcp
```

Current release: **v0.6.0**.

---

## You are a vibe coder (start here)

You want Grok inside your IDE. You do **not** need to contribute to this repo
or learn “planes” and “Forge.”

### Step 1 — Run UniGrok once on this machine

You need: **Git**, **[uv](https://docs.astral.sh/uv/getting-started/installation/)**,
**Docker Desktop**, and either an **[xAI API key](https://console.x.ai/)** or a
**SuperGrok / Grok CLI** login.

> [!WARNING]
> UniGrok is **not published on PyPI**.
> `pip install mcp-grok` installs an **unrelated project**; use this GitHub checkout.
> There is no real `npx unigrok` server yet — Docker (or `uv` + compose) **is** the install.

```bash
git clone https://github.com/djtelicloud/grok-mcp-server.git
cd grok-mcp-server
uv run python main.py init
docker compose up --build -d
```

**Pick one credential path:**

| Path | What to do |
|------|------------|
| **xAI API key** | Put the key in server `.env` as `XAI_API_KEY=…`, then `docker compose up -d` again if needed |
| **SuperGrok subscription** | After the image is up: `docker compose run --rm grok-cli-auth` |

Check ready:

```bash
curl --fail -s http://localhost:4765/readyz
```

You want `"status":"ready"`.  
(`/healthz` only means the process started — not that Grok can answer yet.)

When it’s ready, you can **close this folder** and work in your own apps with
`@grok`.

### Step 2 — Paste this to your coding agent

Copy the whole block into Cursor / Claude / VS Code / Codex and send it:

```text
Configure UniGrok MCP for this machine:

- Streamable HTTP URL: http://localhost:4765/mcp
- Send a stable X-Client-ID header for this IDE (e.g. cursor, claude-code, vscode, codex)
- Never put XAI_API_KEY in IDE MCP settings — credentials stay in UniGrok's server .env
- After connecting, call tools/list and grok_mcp_discover_self
- Prefer the UniGrok agent tool when I say @grok or want a second opinion
- When I ask for a multi-step Implementation Plan, get a UniGrok second opinion
  (agent mode thinking or reasoning) and improve the plan before showing it —
  only if I want that habit; do not silently spend metered API credits
- Do not invent a second MCP port, Forge, Swarm, or land workflow for ordinary use
```

`init` also prints ready-made IDE snippets if you prefer those.

### Step 3 — Prove it (60 seconds)

1. Restart the IDE’s MCP connection.
2. Ask: *Call UniGrok discover_self and tell me which credential planes are ready.*
3. Optional: open the local status UI → [http://localhost:4765/ui/](http://localhost:4765/ui/)

---

## What you get

- One shared Grok connection for every IDE on this machine  
- Keys stay server-side (API and/or CLI OAuth)  
- Optional status UI on this machine only  
- Opt-in: Grok can critique big plans before you see them  

Your app repos do **not** need UniGrok folders, contributor trees, or special
mounts.

---

## Safety (short)

1. Never put `XAI_API_KEY` in IDE MCP JSON.  
2. API calls can cost money; CLI subscription cost is not exposed the same way.  
3. UniGrok does not browse your project unless you pass `workspace_context`.  
4. Local UI is for **this machine’s owner** — not a public multi-user site.  
5. Never paste secrets into chat.

---

## More help (still public)

| I want… | Go here |
|---------|---------|
| More IDE setups | [docs/ide-setup.md](docs/ide-setup.md) |
| Agent knowledge (OKF) | [OKF on the site](https://grokmcp.org/docs/okf/index.md) |
| Smarter default recipes | [docs/public-intelligence/](docs/public-intelligence/) |
| Project site | [https://grokmcp.org](https://grokmcp.org) |
| Architecture deep dive | [architecture.md](architecture.md) |
| Security reporting | [SECURITY.md](SECURITY.md) |

### Where docs live

| You are… | Use |
|---|---|
| Installing / connecting an IDE | This README |
| An agent needing schemas | [OKF knowledge bundle](https://grokmcp.org/docs/okf/index.md) (also local `/docs/okf/`) |
| Public recipes | [docs/public-intelligence/](docs/public-intelligence/) |
| Changing UniGrok itself | [CONTRIBUTING.md](CONTRIBUTING.md) |

**Source of truth:** this repo’s `docs/okf/` + the site OKF.  
**GitHub Wiki (optional):** human-friendly **mirror only**, generated from OKF
(see [docs/wiki-okf-mirror.md](docs/wiki-okf-mirror.md)). Do not hand-edit the
wiki as product docs.

---

## Building UniGrok itself?

If you are an **insider** (write+ collaborator) working on the product —
Core vs Forge, dual ports, landing, Swarm, cloud control — use
**[CONTRIBUTING.md](CONTRIBUTING.md)**. That is not required for vibe-coder use.

## License

[MIT](LICENSE)
