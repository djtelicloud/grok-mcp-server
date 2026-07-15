# Private remote MCP deployment

`https://mcp.grokmcp.org/mcp` is the private, API-plane-only UniGrok resource.
It is not an anonymous inference endpoint and it never receives the local Grok
CLI OAuth volume. The Python gateway runs with `UNIGROK_RUNTIME=cloudrun`, which
disables the CLI plane and all local agent-tool execution.

**Live probes (re-verify):** `GET /healthz` and `GET /readyz` on the same host
are public process gates. Authenticated MCP and status routes return `401`
without a bearer. Hosted PR review wiring is
[design/hosted-review-p0.md](design/hosted-review-p0.md).

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
| `UNIGROK_CALLER_BUDGETS` | JSON daily cost caps keyed by authenticated OAuth subject |
| `UNIGROK_STATE_DIR` | `/tmp/uni-grok` unless a durable store is deliberately attached |

Inject `XAI_API_KEY` from a version-pinned Secret Manager resource. Do not set
`UNIGROK_API_KEYS` on the production OAuth service; a static bearer must not
become a hidden bypass around membership revocation. The service account needs
only access to that xAI secret and the normal logging/metrics permissions.

The gateway publishes RFC 9728 metadata without authentication. Every other
remote route is denied unless control-origin introspection returns an active
token containing the exact required scope. MCP `tools/call` requests are
classified before dispatch: `agent` requires `unigrok:invoke`,
`review_pull_request` requires `unigrok:review`, and status tools require
`unigrok:status`. `/v1` requires `unigrok:chat`.

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
timeout, CDN, and Cloud Armor contract. Repoint only the relevant URL-map
default service or path matcher to that backend. During propagation, each edge
then sees either the complete old backend or the complete replacement backend;
it never sees an in-place backend with a membership update still propagating.
Do not alter unrelated host rules that share the URL map.

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
