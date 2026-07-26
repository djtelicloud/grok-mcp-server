# Public Repository Rules

- This repository is the public, workspace-neutral UniGrok core.
- The sacred public contract is the generic experience a stranger expects after
  cloning this repository and running the commands in `README.md` to use UniGrok
  Core on `:4765`. Every tracked file must support that contract or public
  contribution/verification of it.
- Preserve one `agent` harness with a zero-key local route when an operator-owned
  runtime is ready, optional Grok Build subscription and xAI API routes, live model
  discovery, explicit billing receipts, and bounded visible recovery.
- Never add private intelligence, subordinate-provider orchestration, IDE state,
  private multi-seat topology, provider coordination, raw experiments, credentials,
  or user data.
- Never add symlinks, nested repositories, copied `.git` directories, or worktree
  pointers.
- The repository root is the main checkout. IDE-created linked worktrees belong outside
  this checkout under the parent `grok-mcp/.worktrees/` directory and must not be moved
  manually.
- New public tools require a clear public-use reason, boundary tests, and verification
  from a clean container.
- Preserve CLI isolation in `unigrok_public.server`: disposable empty workspace,
  disposable configuration home, OAuth-only subprocess environment, and denied local
  file/shell/edit/MCP capabilities.
- Generic installs use only server-owned public configuration documented in
  `README.md` and `example.env`. Never commit a real `.env`.
- Pass only `XAI_API_KEY` into the API process. Never pass it to the CLI child, never
  print it, and never place it in an IDE MCP configuration.
- Do not hard-code or allowlist Grok language model ids. Discover CLI and API catalogs
  independently and validate explicit selections against the chosen live plane. Media
  model overrides remain provider-defined.
- Needle must remain visibly inactive until a real optional shadow/reflex runtime,
  tests, promotion boundary, and provenance receipts exist. Do not convert design or
  eval artifacts into a false runtime claim.
- Never commit `.env`, OAuth files, tokens, logs, sessions, caches, or generated state.
- Before claiming completion, run `uv run pytest -q`, `uv run ruff check .`, the
  public clone-boundary checks, build the image, verify `/healthz`, `/readyz`,
  `/runtimez`, compare MCP `tools/list` with self-discovery, and exercise every
  configured public route.
