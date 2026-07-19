# Security Policy

## Public runtime boundary

The default Docker deployment binds to `127.0.0.1`. Grok Build ACP runs inside a
disposable, empty directory with a temporary home. Project discovery, user
configuration, local files, Git, shell commands, edits, external MCP servers, memory,
subagents, and private intelligence are outside the public contract.

The two xAI credentials are isolated:

- The CLI subprocess receives only its Grok OAuth authentication path. Provider API
  keys, management credentials, and subordinate-provider credentials are removed.
- The API SDK receives `XAI_API_KEY` from the server environment. The key is never
  returned by status, discovery, model lists, errors, or tool results.

Public media inputs must be HTTPS URLs. File upload accepts caller-supplied base64
bytes and a plain filename; it never accepts a local path. Remote code execution runs
only in xAI's server-side sandbox.

API calls are metered. Automatic routing prefers the Grok Build subscription, but
supplying `XAI_API_KEY` and leaving `UNIGROK_ENABLE_METERED_API=true` authorizes bounded
API routing fallback, specialists, and recovery. Every attempt is receipted; set the
kill switch false to prevent metered execution. Failed/rejected attempt receipts contain
only bounded billing metadata, and Mission V2 checkpoints them by fenced lease generation
so retries and restarts neither erase nor double-count known spend.

Before SQLite persistence, structured payloads are recursively secret-redacted and
Mission V2 answer projections are bounded. Session turns are written only after
CommitDone. Terminal runtime rows expire under `UNIGROK_STATE_RETENTION_HOURS`; named
sessions and remembered facts remain until explicitly deleted. Redaction is defense in
depth, not permission to submit secrets through prompts or workspace context.

Do not expose this local service directly to a LAN or the internet. A remote
deployment requires separately reviewed TLS, authentication, authorization, origin
validation, rate limiting, request-size limits, and tenant isolation.

## Reporting

Use GitHub private vulnerability reporting or a private Security Advisory. Do not
publish credentials, tokens, private prompts, or user data in a public issue.
