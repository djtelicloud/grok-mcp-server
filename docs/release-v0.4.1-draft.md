# Release Draft: v0.4.1

## Tag: `v0.4.1`
## Target: `main`
## Release Name: `v0.4.1 - Control Center Hardening and Public-Release Portability`

## Summary

UniGrok MCP v0.4.1 hardens the v0.4.0 Control Center and HTTP gateway for
public use. It improves streamed MCP response parsing, session continuity,
input bounds, restart gating, browser diagnostics, accessibility, and release
portability while keeping the public MCP endpoint and `agent` contract stable.

## Highlights

- Hardened HTTP request, message, file, upload, and media input bounds.
- Bounded idle time for direct upstream xAI streams.
- Improved MCP stream parsing, UI session continuity, readiness polling,
  authentication/rate-limit guidance, and live status rendering.
- Added safer Control Center restart gating, CSP/CORS behavior, reduced-motion
  support, and bounded OKF rendering.
- Removed machine-specific paths and switched documentation assets to
  repository-relative links.
- Aligned package, runtime, and UI versions at `0.4.1`.
- Included the Control Center, OKF bundle, Grok adapter profiles, and
  environment template in built wheels.
- Corrected installed `unigrok-mcp init` behavior so `.env` is created in the
  invoking project directory rather than the Python environment.
- Added `SECURITY.md` and clarified that WebMCP support targets an experimental
  W3C Community Group draft, not a W3C Standard.

## Compatibility

- Python 3.11 and 3.12.
- Docker Compose remains loopback-only by default.
- Existing `/mcp`, `/v1`, health, readiness, metrics, and UI routes remain
  unchanged.
- WebMCP features remain experimental and require a compatible browser build
  or bridge exposing `document.modelContext`.

## Release Checklist

- [x] Full pytest suite passes (`631 passed`).
- [x] Offline eval baseline passes (`12/12`).
- [x] Source compilation and package build pass.
- [x] Built wheel contains `mcp_ui/`, `docs/okf/`, `.grok/`, and `example.env`.
- [x] Docker Compose configuration and image health smoke test pass.
- [x] Medium/high static security scan passes.
- [x] Locked runtime dependency audit reports no known vulnerabilities.
- [x] Current tree and reachable history reviewed for committed credentials.
- [x] Public repository published as `djtelicloud/grok-mcp-server` (clean
  single-commit snapshot; this private repo remains the dev archive).
- [x] `v0.4.1` tag created from the verified `main` commit and pushed.
- [x] GitHub release published from these notes.
