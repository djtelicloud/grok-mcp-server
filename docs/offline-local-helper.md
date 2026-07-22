# Optional local-model helper

UniGrok includes an experimental, default-off MCP helper for an operator-owned
local model runtime. It is useful when a model and all of its runtime assets are
already present on the machine and remote Grok planes are unavailable.

This first stage is deliberately narrow:

- it is a separate MCP server named `gemmagrok-local`, normally on
  `http://127.0.0.1:4777/mcp`;
- it exposes only `chat` and `status`;
- it accepts only a loopback or Docker-host runtime origin and disables proxy
  inheritance and redirects;
- it receives no Grok login, xAI API key, workspace, shell, file, or web access;
- it never becomes an automatic fallback for the public `@grok` server.

The main `grok-mcp` service still uses only its documented CLI and API planes.
An authentication failure remains a denial; it does not route into this helper.

## Prerequisite: a staged local runtime

Start an OpenAI-compatible local runtime that serves both:

```text
GET  /v1/models
POST /v1/chat/completions
```

The runtime must be reachable on the same machine. The helper rejects remote
hosts, URL credentials, redirects, and runtime URLs with extra paths. If the
runtime advertises more than one model, select one exact live-discovered id with
`GEMMAGROK_MODEL_ID`; the helper does not contain a model allowlist or download a
replacement.

For a real no-internet run, pre-stage the image, Python dependencies, model,
tokenizer, and any adapters before disconnecting. This helper does not itself
certify or seal those external assets.

## Start the default-off Compose profile

Assuming the local runtime listens on host port `8081`:

```bash
export GEMMAGROK_MODEL_ID='<exact id from the local /v1/models response>'
docker compose --profile offline up -d gemmagrok-local
curl --fail --silent http://127.0.0.1:4777/readyz
```

The service is not started by a normal `docker compose up -d grok-mcp`. Change
the helper port with `GEMMAGROK_PORT` and the host runtime port with
`GEMMAGROK_RUNTIME_PORT`.

You can also run it directly without Docker:

```bash
GEMMAGROK_RUNTIME_URL=http://127.0.0.1:8081 \
GEMMAGROK_MODEL_ID='<exact local model id>' \
uv run python -m unigrok_public.gemmagrok_peer
```

## Connect an IDE explicitly

Add a second MCP entry without credentials. For example:

```json
{
  "mcpServers": {
    "gemmagrok-local": {
      "url": "http://127.0.0.1:4777/mcp",
      "headers": {
        "X-Client-ID": "cursor-gemmagrok-local"
      }
    }
  }
}
```

Use this named helper only when you explicitly want a local answer. Keep the
normal `grok` entry on port `4765`; the two identities and their readiness are
independent.

## Current promotion boundary

This stage proves a useful local MCP surface, not production failover routing.
Before any future automatic or `failover=local` path can ship, it still needs a
separate shadow-mode change with outage classification, an authorization-deny
test, sealed-asset verification, bounded request eligibility, and rollback
receipts. Hosted Cloud Run does not gain a local model from enabling this
Compose-only helper.
