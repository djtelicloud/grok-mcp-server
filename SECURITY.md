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

API calls are metered. Ordinary automatic routing prefers the Grok Build subscription,
and the default `same_plane` policy cannot cross the credential or billing boundary.

Do not expose this local service directly to a LAN or the internet. A remote
deployment requires separately reviewed TLS, authentication, authorization, origin
validation, rate limiting, request-size limits, and tenant isolation.

## Reporting

Use GitHub private vulnerability reporting or a private Security Advisory. Do not
publish credentials, tokens, private prompts, or user data in a public issue.
