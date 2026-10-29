# GitHub Copilot CLI and VS Code

Both clients connect to the same local Streamable HTTP endpoint:

```text
http://localhost:4765/mcp
```

The paths and schemas below follow the current
[Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
and [VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

Keep `XAI_API_KEY` inside the UniGrok service. It is a provider credential, not an MCP
client credential. Local loopback needs no gateway credential unless the operator
enables the optional UniGrok bearer token described below.

## Safe onboarding first

Calling `grok_mcp_onboard_client` with `client=github_copilot` returns merge entries for
both clients. Its default `safe_mode=true` leaves tool approval interactive. UniGrok
does not write configuration files itself; preview the merge and preserve every
unrelated server or setting.

## GitHub Copilot CLI

Copilot CLI reads its user configuration from `~/.copilot/mcp-config.json`. A project
can instead use `.mcp.json` at the repository root; the project entry takes precedence.
Merge this `grok` entry into the existing `mcpServers` object:

```json
{
  "mcpServers": {
    "grok": {
      "type": "http",
      "url": "http://localhost:4765/mcp",
      "tools": ["*"],
      "headers": {
        "X-Client-ID": "github-copilot"
      }
    }
  }
}
```

Restart Copilot CLI, inspect the connection with `/mcp show`, and call
`grok_mcp_discover_self` before relying on the server.

If local bearer authentication is enabled, add this header and launch Copilot with the
original token in its environment:

```json
{
  "headers": {
    "X-Client-ID": "github-copilot",
    "Authorization": "Bearer ${UNIGROK_LOCAL_MCP_TOKEN}"
  }
}
```

Do not replace the placeholder with a literal token in either the user or project file.

## VS Code

VS Code project configuration lives at `.vscode/mcp.json`. Its native password input
keeps the token out of the JSON document:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "unigrok-service-token",
      "description": "Operator-issued UniGrok access token",
      "password": true
    }
  ],
  "servers": {
    "grok": {
      "type": "http",
      "url": "http://localhost:4765/mcp",
      "headers": {
        "X-Client-ID": "github-copilot",
        "Authorization": "Bearer ${input:unigrok-service-token}"
      }
    }
  }
}
```

When local bearer authentication is disabled, remove the `inputs` block and the
`Authorization` header. Reload MCP servers, then call `grok_mcp_discover_self`.

## Enable the optional local bearer token

Generate a random token and its SHA-256 digest outside the repository. Keep the original
token for the client and put only the lowercase digest in this checkout's ignored
`.env` file or an external secret store:

```bash
UNIGROK_CLIENT_TOKEN="$(openssl rand -hex 32)"
printf '%s' "$UNIGROK_CLIENT_TOKEN" | shasum -a 256
```

Set the printed digest as `UNIGROK_LOCAL_MCP_TOKEN_SHA256`, restart `grok-mcp`, and
provide the unprinted `UNIGROK_CLIENT_TOKEN` value through the VS Code password prompt.
For Copilot CLI, launch the client with that value assigned to its expected environment
name, for example `UNIGROK_LOCAL_MCP_TOKEN="$UNIGROK_CLIENT_TOKEN" copilot`. Do not
export that plaintext name in the shell used to start Compose, or Compose will also pass
the supported plaintext alternative into the server container. Plaintext container
environment inspection can expose it. Nonempty malformed token settings stop startup
instead of silently leaving the gateway open.

The token authenticates only the local MCP edge. UniGrok never sends it, or
`XAI_API_KEY`, to the isolated Grok Build CLI child.

## Approval behavior

Interactive tool approval is the safe default. If a user deliberately requests
`safe_mode=false`, Copilot CLI onboarding may suggest session flags scoped to
`grok(agent)` and `grok(agent_result)`. Review those flags before every use; do not grant
blanket trust to the whole server. VS Code continues to use its own approval UI.

For an owner-operated remote deployment, OAuth discovery is preferred. If the operator
instead issues a static service token, the onboarding plan returns the
`UNIGROK_CLIENT_SERVICE_TOKEN` Copilot environment placeholder and the VS Code
password-input pattern with the hosted URL. A service token is an MCP-edge credential,
never an xAI API key.
