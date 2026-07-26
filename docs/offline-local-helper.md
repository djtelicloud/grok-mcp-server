# Local model routes

UniGrok Core supports a zero-key local route inside the normal `@grok` service on
port `4765`. It also includes a smaller, optional named helper for callers that want
one specific local model directly.

Neither route receives a Grok login, xAI API key, workspace, shell, file, or web
authority. Local results carry `billing_class: local_runtime` and `cost_usd: 0`.

## Integrated zero-key route

On Docker Desktop, enable Docker Model Runner and pull the pinned README model:

```bash
docker desktop enable model-runner
docker model pull ai/gemma3:4B-Q4_K_M
docker model run --detach ai/gemma3:4B-Q4_K_M
```

Then start UniGrok normally:

```bash
docker compose up -d grok-mcp
curl --fail --silent http://127.0.0.1:4765/readyz
curl --fail --silent http://127.0.0.1:4765/runtimez
```

The container automatically probes Docker Model Runner through
`model-runner.docker.internal`, Docker Desktop's private container endpoint. When no
remote route is ready, the discovered Gemma model supplies the bounded router and text
path. Requests without a funded local role, including cloud-only media and search, fail
closed instead of escaping to a paid service or fabricating a result.

Set `UNIGROK_LOCAL_AUTO=off` to disable automatic probing. For another
OpenAI-compatible loopback runtime, set `UNIGROK_LOCAL_RUNTIME_URL` to its base URL.
The runtime must expose model discovery and chat completions and must remain on the
same machine; remote, credentialed, and non-HTTP URLs are refused.

Docker Model Runner's endpoint is unauthenticated. Leave host-side TCP support
disabled unless you separately secure it. For a genuinely offline run, pre-stage the
image, model, tokenizer, and dependencies before disconnecting.

## Optional named helper

The default-off `gemmagrok-local` service is separate from `@grok`:

- MCP URL: `http://127.0.0.1:4777/mcp`
- tools: `chat` and `status`
- route: exactly one live-discovered model selected with `GEMMAGROK_MODEL_ID`
- recovery: none; it never escapes to a remote provider

Assuming an OpenAI-compatible runtime listens on host port `8081`:

```bash
export GEMMAGROK_MODEL_ID='<exact id from the local /v1/models response>'
docker compose --profile offline up -d gemmagrok-local
curl --fail --silent http://127.0.0.1:4777/readyz
```

To run the helper directly:

```bash
GEMMAGROK_RUNTIME_URL=http://127.0.0.1:8081 \
GEMMAGROK_MODEL_ID='<exact local model id>' \
uv run python -m unigrok_public.gemmagrok_peer
```

Connect it as a second MCP server only when you explicitly want that local model.
The normal `grok` entry remains `http://127.0.0.1:4765/mcp`; the two identities and
readiness checks are independent.
