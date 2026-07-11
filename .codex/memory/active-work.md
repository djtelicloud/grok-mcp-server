# Codex Active Work

Last updated: 2026-07-11
Owner: Codex
Status: production control live; Sites cutover version saved pending approval

This is the required project-scoped handoff for new Codex chats. Verify all
drift-prone values live before acting. Do not copy secrets or OAuth codes here.

## Current repository state

- The deployment implementation base was verified and pushed as
  `3ed02c21b8b0d4b84f2dbbdbbe89edba64c4255f`. Always resolve the current full
  `HEAD` and `origin/main` live; this tracked file cannot safely name the commit
  that contains its own latest update.
- `3182677` added the standalone Control Cloud Run image CI gate.
- `3ed02c2` made `Dockerfile.cloudrun` compatible with the Google Cloud Build
  Docker builder.
- GitHub Actions run `29161522243` completed successfully with all six jobs
  green, including `Control Cloud Run Image`.
- Project-continuity commit `6f24deb` is on `origin/main`; GitHub Actions run
  `29162512551` completed successfully.
- The shared checkout contains unrelated untracked user files. Preserve them.

## Cloud control deployment

- GCP project: `agentixai-inc`; region: `us-east1`.
- Cloud Run service: `unigrok-control-center`.
- Candidate revision uses the immutable image digest
  `sha256:0f12663e2c1083fd2ebd78aa55042fb2af66aea6f10b48c8986eae7bdeb05dff`.
- Runtime service account:
  `unigrok-control-center@agentixai-inc.iam.gserviceaccount.com`.
- Cloud Run ingress was last verified as `internal-and-cloud-load-balancing`.
- `allUsers` has `roles/run.invoker`; ingress remains restricted to
  `internal-and-cloud-load-balancing`.
- The Cloud Run default URL is disabled. Both known raw `run.app` hostnames
  return `404` while the custom-domain public API returns `200`.
- The GitHub App is installed only for `djtelicloud/grok-mcp-server`. Required
  application secrets exist in Secret Manager as version-pinned references;
  never read or print their values.

## Edge state

- Production hostname: `control.grokmcp.org`.
- GoDaddy authoritative DNS and Google/Cloudflare public resolvers were last
  verified returning `136.69.127.81`.
- Global load-balancer resources use the `unigrok-control-center-*` prefix.
- Google-managed certificate `unigrok-control-center-cert` is active and was
  externally verified for `control.grokmcp.org`, issued by Google Trust
  Services and valid from 2026-07-11 through 2026-10-09.
- The production HTTPS edge is live. Anonymous `/control` redirects to GitHub,
  invalid OAuth state returns `400`, and protected responses are private and
  `no-store`.
- The approved owner completed GitHub authorization at the production custom
  domain and received fresh sanitized repository evidence as
  `@djtelicloud · admin`.
- Cloud Armor policy `unigrok-control-center-edge` is attached to backend
  `unigrok-control-center-backend`.
- Cloud Armor priorities `100`, `200`, and `210` cover exact-host enforcement,
  GitHub auth throttling, and `/control` throttling. All three are preview-only;
  the default allow rule remains enforced.
- Cloud CDN must remain disabled.

## Remaining gates

1. Sites production environment revision `5` now includes the non-secret
   `CONTROL_CENTER_ORIGIN=https://control.grokmcp.org`.
2. Sites version `3` was built, source-pushed as site-only commit `668f56b`, and
   saved but not deployed. Obtain explicit approval for the public Sites
   deployment, then publish that exact saved version.
3. Verify `https://grokmcp.org/control` hands off to the production control
   origin and that the public home/metadata routes remain healthy. Keep the
   previous Sites version available for immediate rollback.
4. Reconcile changelog/version/release metadata only after deployment truth is
   complete; do not publish a release merely because CI is green.

## Safety posture

- Do not expose the raw Cloud Run URL, add permissive CORS, enable Cloud CDN, or
  broaden the GitHub App installation.
- Keep Cloud Armor custom rules in preview until real edge logs have been
  reviewed and thresholds are demonstrated safe.
- Preserve the previous healthy revision, image digest, and numeric secret
  versions for rollback.
