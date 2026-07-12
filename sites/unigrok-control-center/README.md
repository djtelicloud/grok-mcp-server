# UniGrok Project Site

This directory is the source for the canonical UniGrok public project site and
protected contributor control center.

It has three deliberately separate surfaces:

- `/` is public product documentation and contributor onboarding;
- `/llms.txt`, `/.well-known/unigrok.json`, and
  `/api/public/v1/project` expose public-safe project context to people and
  agents without authentication;
- the canonical `https://control.grokmcp.org/control` uses GitHub App OAuth
  plus a fresh server-side repository-role decision; the Sites `/control`
  route redirects there when `CONTROL_CENTER_ORIGIN` is configured.

The source is bound to the existing UniGrok Sites project. It is no longer a
reusable, idless installer template.

## Authentication is not authorization

The public Sites build retains a fail-closed SIWC/bootstrap fallback for
rollback only. Production control runs the same source as a standalone Next.js
service. It implements GitHub OAuth with PKCE, discards the GitHub user token
after identity lookup, stores only a signed HttpOnly identity session, and
performs a fresh installation-token collaborator check for every protected
request.

`/control` first calls `requireChatGPTUser("/control")`. A successful ChatGPT
sign-in identifies the viewer, but does not prove GitHub project membership.
The server then applies `getGitHubProjectAuthorization()` as an independent,
fail-closed check.

The legacy Sites fallback authorization adapter reads
`UNIGROK_GITHUB_IDENTITY_BINDINGS` from the hosted server environment. It is a
bootstrap mapping established by a project administrator, not GitHub OAuth and
not a live collaborator lookup. The canonical standalone origin performs live
verification. Missing, malformed, duplicate, oversized, or unmatched bindings deny
access and never render control-center data.

The value is JSON with a maximum of 100 exact bindings:

```json
[
  {
    "chatgpt_email": "contributor@example.org",
    "github_login": "contributor-login",
    "role": "contributor"
  }
]
```

Store real bindings only in the hosted server environment. Do not commit them.
Only the normalized display name, GitHub login, and role can reach the control
browser; the ChatGPT email is not serialized.

The standalone control service is also the OAuth authorization server for the
private API-plane MCP. Its dynamic clients are redirect-bound, authorization
codes require PKCE, access tokens are short-lived and scoped, and remote MCP
introspection rechecks live repository membership before every request.

## Public machine-readable contract

The public endpoints contain only stable project information, documentation
links, route boundaries, and truthful availability states. They do not expose
credentials, private runtime state, contributor data, inference access, or a
live health claim.

The private remote MCP is reported at `https://mcp.grokmcp.org/mcp` with OAuth
required. It is never presented as a public inference endpoint.
The example terminal on the public page is visibly labeled as an example local
command session, never as a live probe.

## UniGrok connection boundaries

The protected connection wizard remains instructional:

- local mode describes the loopback service at `http://127.0.0.1:4765`;
- tunnel mode describes OpenAI Secure MCP Tunnel as an outbound companion;
- the deployed Site never claims it can reach a contributor's laptop;
- no browser form accepts an xAI key, GitHub token, or tunnel credential.

The standalone control service supplies sanitized GitHub evidence. Hosted Grok
review is explicit, read-only, immutable-head-bound, API-plane-only, and
separately scoped. Pull-request review state remains separate from release
impact.

## Environment contract

| Variable | Purpose | Repository value |
| --- | --- | --- |
| `GITHUB_REPOSITORY` | Public repository label and links | `djtelicloud/grok-mcp-server` |
| `UNIGROK_CONNECTION_MODE` | Wizard state: `unconfigured`, `local`, or `tunnel` | `unconfigured` |
| `UNIGROK_LOCAL_BASE_URL` | Local-only loopback metadata | `http://127.0.0.1:4765` |
| `UNIGROK_TUNNEL_PROFILE` | Non-secret tunnel profile label | `unigrok` |
| `UNIGROK_GITHUB_IDENTITY_BINDINGS` | Server-held bootstrap project bindings | empty |

No variable uses a `NEXT_PUBLIC_` prefix. Real identity bindings are hosted
configuration, never source.

## Local development

Requirements: Node.js `>=22.13.0`, Git, and macOS or Linux.

```bash
cp .env.example .env
npm ci
npm run dev
```

The public root works locally. The production control route expects
Sites-provided ChatGPT identity headers. Use `/preview` for local visual work;
that route is unavailable in production builds.

## Verification and deployment

Run the complete deployment-source gate:

```bash
npm run lint
npm run check:deployment
npm run typecheck
npm test
```

`npm test` validates the bound manifest, typechecks, builds the Cloudflare
Worker artifact, and exercises public, protected, denial, authorization, and
machine-readable routes.

Before deployment:

1. inspect the full source diff;
2. verify anonymous access to `/` and all three public metadata routes;
3. verify anonymous `/control` redirects to dispatch-owned SIWC;
4. verify a signed-in but unbound viewer sees only the denial surface;
5. verify the configured owner binding reaches the control center without
   serializing the ChatGPT email;
6. confirm the hosted binding value and the Sites audience policy;
7. save and review a version before approving production deployment.

Every Sites deployment URL is production. See the current
[ChatGPT Sites documentation](https://learn.chatgpt.com/docs/sites) and
[Sites management guide](https://help.openai.com/en/articles/20001339-creating-and-managing-chatgpt-sites).
