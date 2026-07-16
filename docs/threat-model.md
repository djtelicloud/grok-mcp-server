# UniGrok Threat Model

This document defines the security claims of the current `0.6.x` system. It is
an operational contract, not a claim that the service has completed an
independent penetration test.

## Trust zones

| Zone | Intended trust | Authority |
| --- | --- | --- |
| Stable local Core (`127.0.0.1:4765`) | One machine owner and their IDE clients | Grok inference, sessions, telemetry; no attached workspace by default |
| Remote Core | Authenticated organization members | API-plane inference limited by OAuth scopes and server policy |
| Contributor Forge (`127.0.0.1:4766`) | Trusted repository contributors | Explicit attached-workspace read, test, Git, and guarded mutation tools |
| Public docs/site | Anonymous readers | Read-only documentation and public manifests |

Forge is not a sandbox for hostile contributors or hostile repositories. Its
subprocess restrictions and secret-scrubbed environments reduce risk but do
not provide a kernel or network isolation boundary.

## Protected assets

- Server-held xAI, OpenAI, Anthropic, Google, and gateway credentials, plus the
  Docker-held Grok CLI OAuth session.
- OAuth subjects, gateway bearer keys, prompts, files, session history, and
  telemetry.
- Attached contributor workspace source and Git state.
- Routing/accounting evidence and operator-configured budgets.

Provider credentials stay server-side. They must not be copied into browser
storage, MCP client configuration, logs, workspace files, or model prompts.

## Identity composition

HTTP requests have an authenticated principal and optional client labels:

1. An active introspected OAuth identity becomes
   `oauth:<percent-encoded-issuer>:<percent-encoded-sub>` after exact issuer
   and audience validation.
2. Otherwise a matched configured gateway record becomes the explicit stable
   `http:key:<id>` identity from `UNIGROK_API_KEY_RECORDS`.
3. The deliberately unauthenticated loopback deployment uses the single
   `http:anon` trust domain.

`X-Client-ID` and `X-Caller` are caller-controlled labels. They are useful for
IDE telemetry but are not authentication. A session namespace is composed as
`principal : optional-client-label : logical-session`. HTTP budgets bind to
the principal; changing a label cannot select another budget or another
principal's session. Stdio has no HTTP principal and uses MCP `clientInfo` for
local attribution and optional budget policy.

Possession of the same static gateway record means possession of the same
principal. Record IDs survive secret rotation and JSON reordering, but must
never be reassigned to another person or service. Use OAuth when individual
revocation, membership, scopes, or per-user isolation matters.

## Main threats and controls

| Threat | Primary controls | Residual risk |
| --- | --- | --- |
| Provider credential theft | Server-only credentials, CLI environment scrubbing, secret redaction | Host/container compromise can still expose process credentials |
| Cross-user session access | OAuth subject or key alias in every HTTP session namespace | Parties sharing one static key share one principal by definition |
| Budget evasion by spoofed headers or ledger failure | HTTP budget owner comes from authenticated principal; configured caller caps fail closed when spend cannot be read | Durable atomic multi-instance reservations remain required for hosted hard caps |
| Unauthorized remote invocation | Fail-closed non-loopback configuration, OAuth introspection/scopes or gateway keys, TLS proxy | Proxy or introspection misconfiguration can weaken deployment |
| DNS rebinding/browser abuse | Host/origin validation, exact allowed origins, CSP | Operators can deliberately broaden origin policy |
| SSRF through public resources | Destination validation rejects private, loopback, and unsafe targets | Provider-side fetch behavior remains outside local enforcement |
| Oversized or malformed requests | Body limits, schema validation, bounded outputs | Application-level denial of service remains possible within limits |
| Contributor subprocess abuse | Separate Forge service, explicit workspace attachment, secret-scrubbed env, process/time/resource limits | No portable network denial or hostile-code sandbox guarantee |
| Destructive workspace mutation | Contributor-only tools, runtime and feature gates, path validation, Git landing contract | A trusted contributor process retains deliberate local authority |

## Deployment requirements

- Keep the default service on loopback unless remote access is intentional.
- For LAN/public binding, require OAuth or unique gateway credentials, trusted
  TLS termination, exact origins, request/rate controls, and secret rotation.
- Do not share one static gateway key among mutually untrusted people.
- Keep Forge, container restart, and Git-write capabilities disabled on remote
  Core deployments.
- Cloud deployments must use API-plane credentials only; the local CLI OAuth
  volume and attached workspace do not cross into Cloud Run.

## Out of scope for current claims

- Host, kernel, Docker daemon, or trusted reverse-proxy compromise.
- Malicious code executed deliberately by a trusted Forge operator.
- Availability or integrity failures inside xAI/Grok provider services.
- Legal, patent, regulatory, or formal compliance certification.
- Multi-tenant isolation when several users intentionally share one static
  gateway credential.

Report suspected boundary failures through the private process in
[SECURITY.md](../SECURITY.md).
