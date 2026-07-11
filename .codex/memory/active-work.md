# Codex Active Work

Last updated: 2026-07-11
Owner: Codex
Status: blocked on managed TLS activation and final public-invoker confirmation

This is the required project-scoped handoff for new Codex chats. Verify all
drift-prone values live before acting. Do not copy secrets or OAuth codes here.

## Current repository state

- Shared checkout and `origin/main` were last verified at
  `3ed02c21b8b0d4b84f2dbbdbbe89edba64c4255f`.
- `3182677` added the standalone Control Cloud Run image CI gate.
- `3ed02c2` made `Dockerfile.cloudrun` compatible with the Google Cloud Build
  Docker builder.
- GitHub Actions run `29161522243` completed successfully with all six jobs
  green, including `Control Cloud Run Image`.
- The shared checkout contains unrelated untracked user files. Preserve them.

## Cloud control deployment

- GCP project: `agentixai-inc`; region: `us-east1`.
- Cloud Run service: `unigrok-control-center`.
- Candidate revision uses the immutable image digest
  `sha256:0f12663e2c1083fd2ebd78aa55042fb2af66aea6f10b48c8986eae7bdeb05dff`.
- Runtime service account:
  `unigrok-control-center@agentixai-inc.iam.gserviceaccount.com`.
- Cloud Run ingress was last verified as `internal-and-cloud-load-balancing`.
- Anonymous Cloud Run invocation has not been granted. The candidate therefore
  remains closed to public traffic.
- The GitHub App is installed only for `djtelicloud/grok-mcp-server`. Required
  application secrets exist in Secret Manager as version-pinned references;
  never read or print their values.

## Edge state

- Production hostname: `control.grokmcp.org`.
- GoDaddy authoritative DNS and Google/Cloudflare public resolvers were last
  verified returning `136.69.127.81`.
- Global load-balancer resources use the `unigrok-control-center-*` prefix.
- Google-managed certificate `unigrok-control-center-cert` was still
  `PROVISIONING` at the last check.
- Cloud Armor policy `unigrok-control-center-edge` is attached to backend
  `unigrok-control-center-backend`.
- Cloud Armor priorities `100`, `200`, and `210` cover exact-host enforcement,
  GitHub auth throttling, and `/control` throttling. All three are preview-only;
  the default allow rule remains enforced.
- Cloud CDN must remain disabled.

## Remaining gates

1. Verify the managed certificate and domain status are both `ACTIVE`.
2. Immediately before changing IAM through browser Computer Use, obtain the
   required action-time confirmation to grant `roles/run.invoker` to
   `allUsers`. Do not infer that permission from an older approval.
3. Grant public invocation only after confirming ingress is still restricted to
   internal traffic plus Cloud Load Balancing.
4. Verify from an external path: public project API `200`, anonymous `/control`
   redirects to GitHub, invalid OAuth state is rejected, the approved owner can
   complete login, and protected responses are private/no-store.
5. After custom-domain checks pass, disable the Cloud Run default URL and prove
   the raw `run.app` hostname is unreachable.
6. Cut over the public Site only after production OAuth works. Keep the Site
   change independently reversible.
7. Reconcile changelog/version/release metadata only after deployment truth is
   complete; do not publish a release merely because CI is green.

## Safety posture

- Do not expose the raw Cloud Run URL, add permissive CORS, enable Cloud CDN, or
  broaden the GitHub App installation.
- Keep Cloud Armor custom rules in preview until real edge logs have been
  reviewed and thresholds are demonstrated safe.
- Preserve the previous healthy revision, image digest, and numeric secret
  versions for rollback.
