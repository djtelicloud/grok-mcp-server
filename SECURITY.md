# Security Policy

## Local IDE threat model

Local Compose publishes only on loopback (`127.0.0.1`). By default the gateway
treats that loopback TCB as trusted: any process that can reach `:4765` may call
MCP tools. That is intentional for single-operator machines.

Operators who want a tighter local edge can set `UNIGROK_LOCAL_MCP_TOKEN` (or
`UNIGROK_LOCAL_MCP_TOKEN_SHA256`). When configured, protected surfaces require a
matching `Authorization: Bearer` header; `/healthz` and `/readyz` stay public for
probes. Put the same Bearer value in Cursor's `~/.cursor/mcp.json` headers — never
commit it.

Optional `UNIGROK_LOCAL_DAILY_BUDGET_USD` fails closed against aggregate telemetry
spend for the UTC day. Host header DNS-rebinding protection is enabled on the main
FastMCP transport (same class of control as GemmaGrok). Media URLs must be public
HTTPS and reject `.internal` / `.local` names; resolved non-global addresses are
rejected when DNS answers are available.

Onboarding may emit a Cursor `beforeMCPExecution` hook that auto-allows only
`agent` / `agent_result` (empty tool names ask). Gemini CLI's `trust:true`
alternative trusts the whole server — prefer per-tool grants, or call
`grok_mcp_onboard_client` with `safe_mode=true` to omit auto-approve hooks.

## Public runtime boundary

The default Docker deployment binds to `127.0.0.1`. Grok Build ACP runs inside a
disposable, empty directory with a temporary home. Project discovery, user
configuration, local files, Git, shell commands, edits, external MCP servers, memory,
subagents, and private intelligence are outside the public contract.

Provider credentials are isolated:

- The CLI subprocess receives only its Grok OAuth authentication path. Provider API
  keys, management credentials, and subordinate-provider credentials are removed.
- The API SDK receives `XAI_API_KEY` from the server environment. The key is never
  returned by status, discovery, model lists, errors, or tool results.

Public media inputs must be HTTPS URLs (no `.internal` / private literals). File
upload accepts caller-supplied base64 bytes and a plain filename; it never accepts a
local path. Remote code execution runs only in xAI's server-side sandbox.

API calls are metered. In local Compose, automatic routing prefers the Grok Build
subscription, but supplying `XAI_API_KEY` and leaving
`UNIGROK_ENABLE_METERED_API=true` authorizes bounded API routing fallback, specialists,
and recovery. Every attempt is receipted; set the kill switch false to prevent metered
execution. Prefer `UNIGROK_LOCAL_DAILY_BUDGET_USD` for a hard local ceiling. Failed/rejected attempt receipts contain only bounded billing metadata, and
Mission V2 checkpoints them by fenced lease generation so retries and restarts neither
erase nor double-count known spend.

Before SQLite persistence, structured payloads are recursively secret-redacted and
Mission V2 answer projections are bounded. Session turns are written only after
CommitDone. In persistent local Compose, terminal runtime rows expire under
`UNIGROK_STATE_RETENTION_HOURS`, while named sessions and remembered facts remain until
explicitly deleted. Redaction is defense in depth, not permission to submit secrets
through prompts or workspace context.

Do not expose the local Compose service directly to a LAN or the internet. The
supported owner-operated remote mode is a separate boundary enabled with
`UNIGROK_RUNTIME=cloudrun`: public TLS terminates upstream, while the gateway fails
closed on Control OAuth configuration, introspects every protected request, enforces
tool-specific scopes, rejects unapproved browser origins, authenticates before buffering
bounded MCP bodies, and namespaces state by the issuer-qualified OAuth principal.

Hosted inference may use an owner-default xAI credential. Provider file tools are more
restrictive and require a principal-bound xAI credential, preventing callers from
sharing the owner's file account. Optional exact-principal daily budgets fail closed on
ledger errors for configured callers. Client labels are telemetry only and never grant
authorization, state ownership, or budget identity.

The current hosted SQLite state directory is instance-local. Tenant isolation does not
make it durable across instance replacement or shared across scaled instances; do not
retry an unknown metered or mutating outcome blindly. Hosted release and rollback
operations are owner-only and are intentionally not part of this public clone.

## Reporting

Use GitHub private vulnerability reporting or a private Security Advisory. Do not
publish credentials, tokens, private prompts, or user data in a public issue.
