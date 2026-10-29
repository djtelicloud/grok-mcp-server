# Security Policy

## Public runtime boundary

The default Docker deployment binds to `127.0.0.1`. Grok Build ACP runs inside a
disposable, empty directory with a temporary home. Project discovery, user
configuration, local files, Git, shell commands, edits, external MCP servers, memory,
subagents, and private intelligence are outside the public contract.

Provider credentials are isolated:

- The CLI subprocess receives only its Grok OAuth authentication path. Provider API
  keys, local MCP bearer tokens, management credentials, and subordinate-provider
  credentials are removed.
- The API SDK receives `XAI_API_KEY` from the server environment. The key is never
  returned by status, discovery, model lists, errors, or tool results.

Local loopback access is anonymous by default. An operator may require a bearer token
with `UNIGROK_LOCAL_MCP_TOKEN_SHA256` (preferred server-side form) or
`UNIGROK_LOCAL_MCP_TOKEN`. Nonempty malformed settings fail startup rather than silently
disabling authentication. Clients keep the original token in a password-input or
process-environment facility; it must not be committed to client or project JSON.

Public media inputs undergo strict, network-free lexical validation: they must use
HTTPS and a public-domain-shaped host, with ambiguous authority syntax, credentials,
fragments, IP literals, and conventional local/private suffixes rejected. The optional
`UNIGROK_MEDIA_ALLOWED_DOMAINS` list narrows acceptance to named domains and their
subdomains. This is not a DNS or DNS-rebinding guarantee: UniGrok does not dereference
the URL, and xAI resolves it later inside the provider's network boundary. File upload
accepts caller-supplied base64 bytes and a plain filename; it never accepts a local
path. Remote code execution runs only in xAI's server-side sandbox.

API calls are metered. In local Compose, automatic routing prefers the Grok Build
subscription, but supplying `XAI_API_KEY` and leaving
`UNIGROK_ENABLE_METERED_API=true` authorizes bounded API routing fallback, specialists,
and recovery. Every attempt is receipted; set the kill switch false to prevent metered
execution. Failed/rejected attempt receipts contain only bounded billing metadata, and
Mission V2 checkpoints them by fenced lease generation so retries and restarts neither
erase nor double-count known spend.

`UNIGROK_LOCAL_DAILY_BUDGET_USD` enables an aggregate local UTC-day spend-admission
guard. Hosted operators can use exact-principal limits through
`UNIGROK_CALLER_BUDGETS`. Admissions reserve ledger capacity atomically, known provider
cost is reconciled idempotently, and missing or unknown cost fails closed for later
admissions. These are admission guards, not provider-enforced hard ceilings: one call
can settle above its reservation or the remaining daily amount. Provider-side account
controls remain necessary when a strict financial ceiling is required.

Each guarded scope accepts at most 10,000 new reservation IDs per UTC day and then
fails closed; exact replays are checked before that ceiling. Pending or unknown-cost
reservations are never retention-pruned. Known reconciled rows remain through their UTC
day and the configured state-retention window, after which their exact replay evidence
is pruned along with the finalized row.

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
OAuth introspection is bounded per process, coalesces duplicate token-and-scope checks,
streams at most 16 KiB under an absolute five-second deadline, and returns temporary
unavailability with `Retry-After` when its capacity is full. Deploy an upstream per-client
rate limit as well; scaled processes do not share this short-lived admission state.

Hosted inference may use an owner-default xAI credential. Provider file tools are more
restrictive and require a principal-bound xAI credential, preventing callers from
sharing the owner's file account. Optional exact-principal daily guards fail closed on
ledger errors and unresolved cost for configured callers. Their SQLite reservation is
atomic within one instance, but horizontally scaled instances do not share a ledger.
Client labels are telemetry only and never grant authorization, state ownership, or
budget identity.

The current hosted SQLite state directory is instance-local. Tenant isolation does not
make it durable across instance replacement or shared across scaled instances; do not
retry an unknown metered or mutating outcome blindly. Hosted release and rollback
operations are owner-only and are intentionally not part of this public clone.

## Reporting

Use GitHub private vulnerability reporting or a private Security Advisory. Do not
publish credentials, tokens, private prompts, or user data in a public issue.
