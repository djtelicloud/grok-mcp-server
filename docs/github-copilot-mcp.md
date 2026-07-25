# GitHub Copilot cloud agent + UniGrok public MCP

Connect **public** UniGrok (`https://mcp.grokmcp.org/mcp`) to **GitHub Copilot cloud agent**
and **Copilot code review** without OAuth.

## Why service tokens

GitHub repository MCP settings support remote `http` servers with bearer headers, but
**do not support remote OAuth**. UniGrok’s hosted public gateway is OAuth-first for IDEs.
Service tokens are the automation path for GitHub (and similar bots).

| Client | Auth |
|--------|------|
| Cursor / IDE OAuth | Control OAuth (unchanged) |
| GitHub Copilot cloud agent / code review | Service token (`Authorization: Bearer …`) |
| Local Docker loopback | No gateway auth (default) |

**Never** point GitHub Copilot at Sky/Space/Ground private seats or `localhost`.

## Operator: mint and deploy a token

```bash
# 1) Mint a long random secret (keep offline; never commit)
TOKEN="$(openssl rand -hex 32)"
echo "token (store in password manager): $TOKEN"
echo "sha256 (optional for hashed deploy): $(printf '%s' "$TOKEN" | shasum -a 256 | awk '{print $1}')"

# 2) Set on the public Cloud Run service (active region), e.g.:
# UNIGROK_SERVICE_TOKENS=$TOKEN
# or UNIGROK_SERVICE_TOKEN_SHA256=<sha256-hex>
# UNIGROK_SERVICE_TOKEN_LABEL=github-copilot
# optional scopes (default = full public MCP capability):
# UNIGROK_SERVICE_TOKEN_SCOPES=unigrok:connect,unigrok:invoke,unigrok:review,unigrok:status,unigrok:chat
```

Redeploy / revise so the env is live. `GET /healthz` stays public; `/mcp` requires the bearer.

## GitHub: Agents secret

In the **public** product repo (`djtelicloud/grok-mcp-server`):

1. **Settings → Secrets and variables → Agents**
2. New secret: `COPILOT_MCP_UNIGROK_TOKEN` = the plaintext token (not the SHA-256)

Only secrets/variables prefixed with `COPILOT_MCP_` are visible to MCP config.

## GitHub: MCP configuration JSON

**Settings → Copilot → MCP servers** — paste (no comments):

```json
{
  "mcpServers": {
    "unigrok-public": {
      "type": "http",
      "url": "https://mcp.grokmcp.org/mcp",
      "headers": {
        "Authorization": "Bearer ${COPILOT_MCP_UNIGROK_TOKEN}",
        "X-Client-ID": "github-copilot-cloud"
      },
      "tools": [
        "chat",
        "agent",
        "agent_result",
        "review_pull_request",
        "grok_mcp_status",
        "grok_mcp_discover_self",
        "list_models",
        "benchmark_status",
        "search_knowledge",
        "remember_fact"
      ]
    }
  }
}
```

Start with the allowlist above. Expand only after you trust autonomous tool use
(GitHub does **not** prompt for approval). Prefer read-only tools for code review.

## Validate

1. Save MCP configuration (JSON validates on save).
2. Assign an issue to **Copilot** → open session logs → **Start MCP Servers**.
3. Confirm `unigrok-public` tools appear.
4. Optional: request Copilot code review on a PR and check environment setup logs.

## Hard rules

- Public product only — no Space/Sky secrets.
- `PROMOTE` defaults stay product policy; this wire does not grant hierarchy promote.
- Rotate tokens by minting a new value, updating Cloud Run + the Agents secret, then
  revoking the old token from `UNIGROK_SERVICE_TOKENS` / hash list.
- Provider keys (`XAI_API_KEY`, Grok Build OAuth) remain server-side only.

## Related

- [remote-mcp-deployment.md](./remote-mcp-deployment.md) — hosted OAuth + env contract
- GitHub docs: [Configure MCP servers for your repository](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers)
