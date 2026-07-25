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

**Do not use** short-lived `ugtoken.` review tokens (`scripts/mint_mcp_service_token.py`) for standing
GitHub Copilot MCP config — those expire in ≤10 minutes and still need OAuth-style introspection.
GitHub needs a **static** service token on the public gateway.

### 1) Mint (local, once)

```bash
TOKEN="$(openssl rand -hex 32)"
echo "token (password manager + Agents secret only): $TOKEN"
echo "sha256 (optional hashed deploy): $(printf '%s' "$TOKEN" | shasum -a 256 | awk '{print $1}')"
```

Never commit the token. Never paste it into the PR.

### 2) Cloud Run deploy (project `agentixai-inc`)

Public MCP services (both regions share the same image/contract):

| Service | Region |
|---------|--------|
| `unigrok-remote-mcp` | `us-east1` (active path for `mcp.grokmcp.org`) |
| `unigrok-remote-mcp` | `us-central1` (warm standby) |

**Preferred:** Secret Manager + new revision (plaintext never in shell history longer than needed).

```bash
# Project
gcloud config set project agentixai-inc

# Create or add a secret version (name is public; value is secret)
printf '%s' "$TOKEN" | gcloud secrets create unigrok-copilot-service-token \
  --data-file=- --replication-policy=automatic 2>/dev/null \
  || printf '%s' "$TOKEN" | gcloud secrets versions add unigrok-copilot-service-token --data-file=-

# Grant the Cloud Run runtime SA access (adjust SA if your service uses a custom one)
PROJECT_NUMBER="$(gcloud projects describe agentixai-inc --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding unigrok-copilot-service-token \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Active region — env from secret + label (atomic new revision)
gcloud run services update unigrok-remote-mcp \
  --region=us-east1 \
  --update-secrets=UNIGROK_SERVICE_TOKENS=unigrok-copilot-service-token:latest \
  --update-env-vars=UNIGROK_SERVICE_TOKEN_LABEL=github-copilot

# Standby region — same digest/env contract
gcloud run services update unigrok-remote-mcp \
  --region=us-central1 \
  --update-secrets=UNIGROK_SERVICE_TOKENS=unigrok-copilot-service-token:latest \
  --update-env-vars=UNIGROK_SERVICE_TOKEN_LABEL=github-copilot
```

**Hashed-at-rest alternative** (gateway stores only SHA-256):

```bash
HASH="$(printf '%s' "$TOKEN" | shasum -a 256 | awk '{print $1}')"
# Put HASH in Secret Manager as unigrok-copilot-service-token-sha256
gcloud run services update unigrok-remote-mcp --region=us-east1 \
  --update-secrets=UNIGROK_SERVICE_TOKEN_SHA256=unigrok-copilot-service-token-sha256:latest \
  --update-env-vars=UNIGROK_SERVICE_TOKEN_LABEL=github-copilot
# Repeat for us-central1. GitHub still gets the plaintext TOKEN in Agents secrets.
```

### 3) Smoke (after revision ready)

```bash
# Public health still open
curl -fsS https://mcp.grokmcp.org/healthz

# Anonymous /mcp must 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://mcp.grokmcp.org/mcp \
  -H 'Content-Type: application/json' -d '{}'

# Service token must not 401 (may 406/400 on empty body — not 401)
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://mcp.grokmcp.org/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

Optional scopes (default = full public MCP capability):

```text
UNIGROK_SERVICE_TOKEN_SCOPES=unigrok:connect,unigrok:invoke,unigrok:review,unigrok:status,unigrok:chat
```

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
