# Cloud Run deployment contract

This package has two independent production targets:

- `npm run build` produces the existing Sites/Vinext artifact.
- `npm run build:standalone` produces a conventional Next.js standalone server
  for the GitHub-authenticated contributor control app.

The standalone target does not replace the public Site. Deploy it at
`https://control.grokmcp.org`, then set the public Site's non-secret
`CONTROL_CENTER_ORIGIN` to that exact origin so `/control` links leave the
public runtime cleanly.

## Image contract

Build from this directory so `.dockerignore` is the build-context boundary:

```bash
docker build --file Dockerfile.cloudrun --tag unigrok-control:local .
docker run --rm --publish 8080:8080 unigrok-control:local
curl --fail http://127.0.0.1:8080/api/public/v1/project
```

The command above tests the public container contract only; it does not enable
GitHub login. Production GitHub mode requires the exact HTTPS configuration in
the next section. The final image:

- runs as numeric UID/GID `10001`, not root;
- contains only the standalone server, static assets, and traced runtime
  dependencies;
- listens on Cloud Run's `PORT` (default `8080`);
- never accepts build arguments or copied files containing application
  credentials; and
- exits before opening the service port when GitHub mode is selected without
  its complete runtime configuration; and
- exposes the existing anonymous `/api/public/v1/project` route as a shallow
  process/readiness probe. It does not contact GitHub and therefore does not
  turn a provider outage into a container restart loop.

Cloud Run does not derive service probes from the Dockerfile health check.
Configure HTTP startup, liveness, and readiness probes explicitly against
`/api/public/v1/project` on the container port. A successful startup probe must
mean the process is already safe to receive traffic. See the official
[Cloud Run health-check documentation](https://docs.cloud.google.com/run/docs/configuring/healthchecks).

## Runtime configuration

Keep the deployment target configurable. The immediate billed target is:

| Setting | Current value |
| --- | --- |
| GCP project | `agentixai-inc` |
| Cloud Run service | `unigrok-control-center` |
| Secret/resource prefix | `unigrok-control-center` |
| Region | Operator-selected `REGION`; use one region consistently for Cloud Run, Artifact Registry, and the serverless NEG |

The dedicated `unigrok-control-plane` project exists but cannot be linked to
billing until the account-level billed-project quota is raised. Migrate the
same digest-pinned service and versioned secrets there after billing is
enabled. Do not hardcode either project ID in the image or application source.
For scripts and operator commands, pass `PROJECT_ID`, `REGION`, and `SERVICE`
explicitly; default `SERVICE` to `unigrok-control-center` only in operator
automation.

Set these ordinary environment variables on the Cloud Run revision:

| Variable | Required value or purpose |
| --- | --- |
| `CONTROL_CENTER_MODE` | `github` |
| `APP_BASE_URL` | Exact HTTPS origin, normally `https://control.grokmcp.org`; no path or trailing slash |
| `GITHUB_REPOSITORY` | `djtelicloud/grok-mcp-server` |
| `GITHUB_REPOSITORY_ID` | Immutable numeric repository ID `1295814352` |
| `GITHUB_APP_ID` | Non-secret GitHub App identifier |
| `GITHUB_APP_CLIENT_ID` | Non-secret GitHub App client identifier |
| `GITHUB_APP_INSTALLATION_ID` | Installation restricted to this repository |

`NODE_ENV=production`, `HOSTNAME=0.0.0.0`, and the default `PORT=8080` are set
by the image. Cloud Run may override `PORT`.

Reference these values from Secret Manager as runtime environment variables:

| Environment variable | Secret contents |
| --- | --- |
| `GITHUB_APP_PRIVATE_KEY` | The GitHub App private key, including its PEM delimiters |
| `GITHUB_APP_CLIENT_SECRET` | GitHub App OAuth client secret |
| `AUTH_SESSION_SECRET` | At least 32 cryptographically random bytes, encoded for safe text transport |

In the current target project, use these Secret Manager resource names:

- `unigrok-control-center-github-private-key`
- `unigrok-control-center-github-client-secret`
- `unigrok-control-center-session-secret`

Use a dedicated Cloud Run service account with only
`roles/secretmanager.secretAccessor` on those three secrets. Do not grant it
project-wide Editor, repository administration, or Artifact Registry write.
The build identity may push images but must not read runtime secrets.

Pin an explicit Secret Manager version in each revision. Rotate by adding a
new secret version and deploying a new revision; do not paste secret values
into `gcloud`, Docker build arguments, Cloud Build substitutions, source files,
or GitHub Actions logs. Google documents both environment and volume-backed
Secret Manager references in [Configure secrets for Cloud Run](https://docs.cloud.google.com/run/docs/configuring/services/secrets).

The container entrypoint refuses to start GitHub mode when any required value
is absent, and the application rejects malformed security configuration.
Secret Manager references being present is not sufficient: test an actual
GitHub login, repository membership check, and session round trip before
assigning production traffic.

## Release sequence

1. Build the image in the intended GCP project and push it to a private
   Artifact Registry repository.
2. Resolve and record the image digest. Deploy by digest, never by a mutable
   tag.
3. Create a new Cloud Run revision with zero traffic, the dedicated runtime
   service account, the ordinary variables above, and version-pinned Secret
   Manager references. Give the candidate a temporary revision tag only for
   authenticated startup, readiness, invalid-state, and public-route smoke
   checks. A tag URL uses the raw `run.app` hostname and cannot complete the
   production OAuth/cookie round trip for `control.grokmcp.org`.
4. Use ingress `internal-and-cloud-load-balancing`. Grant unauthenticated Cloud
   Run invocation because the public landing and GitHub OAuth callback must be
   reachable; application authorization protects `/control` and private data.
5. Before DNS cutover, verify startup/readiness and invalid-state behavior
   through an authenticated operator-only candidate URL. Full GitHub OAuth is
   intentionally deferred until the production hostname reaches the candidate.
6. Build the load-balancer edge with Cloud CDN disabled. Attach a Cloud Armor
   policy to the serverless NEG backend in preview/log-only mode first, including
   exact-host enforcement and tested rate limits for `/auth/github/*` and
   `/control`.
7. Cut over the production hostname, then verify: the public project route is
   `200`; anonymous `/control` begins GitHub login; invalid state is rejected;
   an approved administrator reaches the dashboard; an unrelated or revoked
   GitHub account receives a real `403`; and protected responses are `private,
   no-store` with no shared-cache hit.
8. After the load balancer, certificate, DNS, and OAuth checks pass, disable the
   service's default URL with `gcloud run services update SERVICE --no-default-url`.
   Confirm the raw `run.app` hostname is unreachable before moving 100% traffic.
9. Preserve the previous healthy revision, digest, and numeric secret versions.

Do not add a permissive CORS policy. This is a same-origin web application,
and the OAuth callback is a top-level navigation rather than a cross-origin
browser API. If an API is introduced later, allow only the exact production
origin and review every response for contributor data.

## Custom domain

Keep `grokmcp.org` on Sites and use a separate `control.grokmcp.org` origin for
this service. The recommended edge is a global external Application Load
Balancer with:

1. a serverless network endpoint group for the Cloud Run service and region;
2. an HTTPS backend, URL map, and reserved global address;
3. a Google-managed certificate for `control.grokmcp.org`;
4. DNS records pointed to the reserved load-balancer address; and
5. Cloud Run ingress restricted to internal traffic plus Cloud Load Balancing.

Cloud CDN must remain disabled on this mixed authenticated backend. Do not use
`FORCE_CACHE_ALL`, and do not enable negative caching for auth paths. Cloud
Armor protects only traffic that traverses the load balancer, so disabling the
raw `run.app` URL is a release gate rather than optional hardening.

Set the GitHub App homepage to the public project and its callback URL to
`https://control.grokmcp.org/auth/github/callback`. Set `APP_BASE_URL` to the
same origin. Do not authorize wildcard callbacks, alternate preview domains,
or the raw Cloud Run hostname in production. After certificate issuance,
verify the DNS answer, TLS hostname, redirect target, secure cookie behavior,
and callback URL from an external network before updating the public Site's
`CONTROL_CENTER_ORIGIN`.

Treat the public Site cutover as a separate reversible step. Set
`CONTROL_CENTER_ORIGIN=https://control.grokmcp.org` and redeploy the Site only
after the custom-domain OAuth checks pass. To roll back the public entry point,
unset `CONTROL_CENTER_ORIGIN` and redeploy the previous known-good Site version;
do not point visitors at a raw Cloud Run URL.

## Rollback

Record the candidate revision, previous healthy revision, image digests, and
secret version numbers in the deployment receipt. If smoke checks, login, or
post-promotion monitoring fails:

1. route `100%` of traffic back to the recorded previous revision;
2. verify its public project route and a complete administrator login;
3. leave the failed revision at zero traffic for log inspection;
4. restore the previous GitHub callback only if the origin itself changed; and
5. revoke or disable any newly exposed secret version, then diagnose before a
   new revision.

Do not rebuild an old source tree as a rollback. Cloud Run revisions are
immutable, so traffic reassignment to the known revision is faster and keeps
its image and version-pinned secret references coherent.
