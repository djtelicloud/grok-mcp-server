# Cursor Cloud → UniGrok twin

One secret. Remote MCP. No local server.

## Operator (you)

```bash
export UNIGROK_MCP_TOKEN_SECRET='…same as Control MCP_TOKEN_SECRET…'
export UNIGROK_SERVICE_NAME=cursor-cloud
export UNIGROK_SERVICE_SCOPE=unigrok:invoke
# optional TTL max 600
uv run python scripts/mint_mcp_service_token.py
```

Copy the printed token into Cursor Cloud secret: **`UNIGROK_ACCESS_TOKEN`**.

Token lasts **10 minutes**. Re-mint when it expires.

Requires Control Center deployed with `service:cursor-cloud` allowlist (this PR).

## Cursor Cloud agent paste

```
Secret UNIGROK_ACCESS_TOKEN is set.

Use MCP https://mcp.grokmcp.org/mcp
Header Authorization: Bearer $UNIGROK_ACCESS_TOKEN
Header X-Client-ID: cursor-cloud

Call grok_mcp_discover_self then agent(mode=fast, prompt="ping", plane=api, fallback_policy=same_plane).
Never ask for XAI_API_KEY. Do not land main. No Stage 1 live gen.
```

## MCP JSON (if the UI has a config field)

```json
{
  "mcpServers": {
    "unigrok": {
      "url": "https://mcp.grokmcp.org/mcp",
      "headers": {
        "Authorization": "Bearer ${UNIGROK_ACCESS_TOKEN}",
        "X-Client-ID": "cursor-cloud"
      }
    }
  }
}
```
