# Private remote MCP deployment

`https://mcp.grokmcp.org/mcp` is the **owner-operated insider** UniGrok Cloud Run
resource for **team members and cloud agents** (GitHub write+ / scoped tokens).
It is **not** a public multi-tenant SaaS for anonymous vibe users, and it is
**not** an anonymous inference endpoint.

## Same Docker, cloud mode

Cloud Run deploys the **same repository `Dockerfile` image** used for local
product builds (digest-pinned). Behavior is **not** a 1:1 laptop Compose clone:

| | Local Docker / Compose | Cloud Run (`UNIGROK_RUNTIME=cloudrun`) |
| --- | --- | --- |
| Image | Product `Dockerfile` | Same image family, digest-pinned |
| CLI OAuth volume | May attach for SuperGrok CLI plane | **Never** mounted |
| Default Grok spend | Machine `.env` / local CLI | **Owner** `XAI_API_KEY` from Secret Manager (**Live default**) |
| Optional teammate own keys | Each engineer’s local keys | Secret Manager JSON map bound to OAuth principal; owner default remains |
| Forge / git-write tools | Contributor laptop only | **Off** |

The gateway runs with `UNIGROK_RUNTIME=cloudrun`, which disables the CLI plane
and all local agent-tool execution. Hosted PR review wiring is
[design/hosted-review-p0.md](design/hosted-review-p0.md).

**Live probes (re-verify):** `GET /healthz` and `GET /readyz` on the same host
are public process gates. Authenticated MCP and status routes return `401`
without a bearer.

## Operator checklist (cloud agents)

1. Build the product image; record the Artifact Registry **digest**.
2. Deploy that digest with `UNIGROK_RUNTIME=cloudrun` and OAuth introspection
   pointing at Control (`control.grokmcp.org`).
3. Inject **owner** `XAI_API_KEY` only from Secret Manager (default spend path).
4. Do **not** put raw `XAI_API_KEY` in IDE MCP JSON, Cursor Cloud agent secrets,
   or GitHub as a substitute for owner SM injection.
5. Cloud agents authenticate with **scoped short-lived tokens** (or the approved
   Control mint path) — not the xAI provider key.
6. Confirm: health/ready `200`; `POST /mcp` without token → `401`.
7. Confirm cloud surface has **no** git-write / Forge mutation tools.
8. Optional: bind a write+ principal's own Grok credential through the reviewed
   Secret Manager map. Invalid maps fail closed; owner default remains only for
   a valid map with no entry for that principal.

## Runtime contract

Deploy the repository `Dockerfile` to a dedicated Cloud Run service and set:

| Variable | Production value or purpose |
| --- | --- |
| `UNIGROK_RUNTIME` | `cloudrun` |
| `UNIGROK_PUBLIC_MCP_URL` | `https://mcp.grokmcp.org/mcp` |
| `UNIGROK_OAUTH_AUTHORIZATION_SERVERS` | `https://control.grokmcp.org` |
| `UNIGROK_OAUTH_INTROSPECTION_URL` | `https://control.grokmcp.org/oauth/introspect` |
| `UNIGROK_OAUTH_SCOPES` | `unigrok:connect,unigrok:invoke,unigrok:review,unigrok:chat,unigrok:status` |
| `UNIGROK_ALLOWED_ORIGINS` | Exact reviewed browser origins only; omit when no browser client is approved |
| `UNIGROK_CALLER_BUDGETS` | JSON daily cost caps keyed by the full issuer-bound OAuth principal published by runtime attribution |
| `UNIGROK_STATE_DIR` | `/tmp/uni-grok` unless a durable store is deliberately attached |

Inject the **owner** `XAI_API_KEY` from a version-pinned Secret Manager resource
— this is the cloud twin **default** spend path. Do not set
`UNIGROK_API_KEY_RECORDS` or legacy `UNIGROK_API_KEYS` on the production OAuth
service; a static bearer must not become a hidden bypass around membership
revocation. The service account needs only access to that xAI secret and the
normal logging/metrics permissions.

Optional **per-insider** cloud credentials (write+ OAuth principal) may bind
their own xAI API key without replacing the owner default:

| Variable | Purpose |
| --- | --- |
| `XAI_API_KEY` | **Owner default** (Secret Manager) — always the fallback |
| `UNIGROK_PRINCIPAL_XAI_KEYS_JSON` | JSON map of OAuth principal → xAI API key |

Example map (store the whole JSON as one Secret Manager secret; never commit):

```json
{
  "oauth:https%3A%2F%2Fcontrol.grokmcp.org:github%3A123456": "xai-teammate-key"
}
```

Rules:

- Only principals with kind `oauth:` use the map; `http:anon` and labels never do.
- Every map key must be the full canonical
  `oauth:<percent-encoded-issuer>:<percent-encoded-sub>` principal emitted by
  the gateway, and its decoded issuer must exactly match a validated
  `UNIGROK_OAUTH_AUTHORIZATION_SERVERS` entry. Bare subjects, unbound subjects,
  unlisted issuers, and client labels are rejected fail-closed.
- Missing map entry → **owner default**.
- The binding applies to both MCP agent execution and the authenticated
  `/v1/chat/completions` proxy; neither path may silently switch a mapped
  principal back to owner billing.
- A configured map that is malformed, oversized, duplicated, or contains an
  invalid entry fails closed instead of silently shifting spend to the owner.
- Key rotation creates a new random process-local client generation instead of
  reusing the stale client. Rotate Secret Manager values through a new Cloud Run
  revision so retired clients close with the old process. Execution receipts
  report only `owner_default` or `principal`, never a key.
- Never put raw keys in IDE MCP JSON or public clone workflows.
- Prefer Secret Manager / KMS-class injection of the JSON blob; never browser
  paste for unauthenticated callers.

The gateway publishes RFC 9728 metadata without authentication. Every other
remote route is denied unless control-origin introspection returns an active
token containing the exact required scope. MCP `tools/call` requests are
classified before dispatch: `agent` requires `unigrok:invoke`,
`review_pull_request` requires `unigrok:review`, and status tools require
`unigrok:status`. `/v1` requires `unigrok:chat`.

Control must perform a fresh GitHub `write` / `maintain` / `admin` collaborator
check when issuing a user authorization code and again at user-token
introspection. Before shifting traffic, prove a valid write+ token succeeds, a
read-level principal is denied, and a previously valid revoked member is denied.

`X-Client-ID` and `X-Caller` remain optional reporting labels. They never own a
remote budget or top-level session namespace. OAuth `sub` is the authenticated
principal for both; a caller cannot escape its budget or enter another
subject's session namespace by changing either header.

## Deployment and rollback

Build once, resolve the Artifact Registry digest, and deploy that digest to a
zero-traffic revision. Verify public health and OAuth metadata, then verify a
missing token, wrong scope, revoked member, valid member, and stale PR head
before shifting traffic. The custom hostname must traverse the existing global
load balancer with Cloud CDN disabled and Cloud Armor rate rules enabled.

For regional recovery, deploy the same resolved digest and runtime contract to
an equivalent Cloud Run service in each region. Give each service its own
regional serverless NEG and attach only verified, functionally equivalent
regions to the global backend. A load balancer with multiple serverless NEGs
routes by proximity, so never attach regions running different reviewed
digests.

For a bounded manual failover, prefer an atomic URL-map change over removing a
NEG from the active backend. Stage and verify the replacement region first,
then create a separate backend with its own verified NEG and the same protocol,
timeout, disabled Cloud CDN setting, and Cloud Armor policy. In one validated
URL-map configuration update, repoint every route that selects the old backend:
the relevant default service, path-matcher default service, and any associated
path rules. During propagation, each edge then sees either the complete old
backend or the complete replacement backend; it never sees an in-place backend
with a membership update still propagating. Do not alter unrelated host rules
that share the URL map.

After the URL-map update reports success, require repeated public health,
readiness, metadata, and unauthenticated challenge probes, plus request-log
evidence that the replacement region served the public hostname. Restore the
previous URL-map backend immediately if any contract fails. Keep the old
backend, NEG, service, and known revision intact until the replacement has
remained healthy. Removing them is cleanup after the observation window, not
part of the cutover. In-place removal of the old NEG is only acceptable after
the active URL map no longer references that backend; a successful backend API
update alone is not proof that every edge has finished propagating it.

Keep the previous revision at zero traffic. Roll back by moving 100% traffic to
that known digest; do not rebuild old source. Disabling the service or removing
the `mcp.grokmcp.org` host rule fails closed without affecting the public site,
local MCP, or protected control origin.
