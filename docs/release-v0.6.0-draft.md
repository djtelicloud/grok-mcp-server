# Release Draft: v0.6.0

## Tag: `v0.6.0`
## Target: `main`
## Release Name: `v0.6.0 - Hosted Contributor Control and Project Continuity`

## Summary

UniGrok MCP v0.6.0 adds a production, GitHub-authorized contributor control
plane without weakening the local-first product boundary. The stable MCP
gateway, xAI credential, Grok CLI OAuth session, task memory, and local
administration remain on the user's machine. The hosted surface exposes only
fresh, sanitized repository evidence to authorized contributors.

This release also makes Codex the explicit integration and release owner,
introduces project-scoped cross-chat handoffs, adds artifact-level Cloud Run CI,
and carries the adaptive routing, dual-plane model visibility, credential-plane
guidance, and telemetry improvements developed through the v0.5 series.

## Highlights

- Production contributor control at `https://control.grokmcp.org` with GitHub
  App authorization and fresh repository-role verification.
- Public `https://grokmcp.org/control` handoff to the protected application.
- Digest-pinned Cloud Run deployment behind managed TLS, load-balancer-only
  ingress, preview Cloud Armor controls, and disabled raw service URLs.
- Standalone container CI that validates public routes, non-root execution, and
  fail-closed GitHub configuration.
- Required project-scoped Codex active-work handoffs for reliable continuation
  across new chats.
- Clear separation between the stable local service, contributor workflows,
  public project Site, and optional hosted control plane.

## Compatibility

- Python 3.11 and 3.12 remain supported.
- The stable MCP endpoint remains `http://localhost:4765/mcp`.
- Existing API/CLI credential-plane behavior and local data remain compatible.
- Hosted contributor control is optional and does not make local MCP or Grok
  credentials public.

## Production Verification

- Full Python suite: `842 passed` before the version-bump commit.
- GitHub CI: all six jobs green, including `Control Cloud Run Image`.
- Managed TLS and DNS verified for `control.grokmcp.org`.
- Public project API returns `200`; anonymous control access redirects to
  GitHub; invalid OAuth state is rejected.
- Authorized owner login returns fresh sanitized repository evidence.
- Cloud Run default URL is disabled and both raw hostnames return `404`.
- Public Site home and discovery routes remain healthy; `/control` redirects to
  the production control origin.

## Release Checklist

- [x] Version alignment and lockfile refresh pass.
- [x] Full Python tests pass on the release-preparation commit.
- [x] Site tests and standalone image gate pass on the release-preparation
  commit.
- [x] Source distribution and wheel build successfully.
- [x] Wheel contents and package metadata checks pass.
- [x] Release-preparation commit lands to shared `main` and post-push CI is
  green.
- [ ] `v0.6.0` tag is created from the verified `main` commit and pushed.
- [ ] GitHub release is published from these notes.
