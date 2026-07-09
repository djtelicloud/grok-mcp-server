# Changelog

All notable changes to UniGrok MCP will be documented in this file.

## [0.4.1] - 2026-07-09

### Fixed
- **Control Center release alignment**: Updated the UI version identity and
  cache-buster assertions to match the v0.4.1 hardening release.
- **MCP response parsing**: Reused the shared stream parser when fetching the
  UI's MCP tool list so streamed JSON-RPC responses are handled consistently.
- **UI robustness and security**: Hardened session continuity, path scoping,
  proxy errors, restart gating, and live status metrics behavior.
- **Streaming resource bounds**: Added a configurable idle timeout to the
  direct xAI streaming proxy so stalled upstream connections cannot remain
  open indefinitely.
- **Public-release portability**: Removed developer-specific absolute paths,
  made container restart scoping configurable, and switched architecture
  assets to repository-relative links.
- **Release metadata**: Aligned package, runtime, and Control Center versions
  at v0.4.1 and included UI, OKF, adapter, and environment-template assets in
  built wheels, plus the advertised documentation routes and adapter profiles
  in the Docker image. Installed `unigrok-mcp init` now writes first-run files
  to the invoking project directory rather than `site-packages`.
- **Project security and standards accuracy**: Added a vulnerability-reporting
  policy and documented WebMCP as an experimental Community Group draft.
- **Repository hygiene**: Removed machine-specific paths from tracked agent and
  IDE templates and retired a duplicate GitHub Actions test workflow. CI now
  uses read-only token permissions, builds and inspects the wheel, and treats
  dependency audit findings as release-blocking.

## [0.4.0] - 2026-07-09

### Added
- **Premium Control Center UI v0.4.0**: Completely redesigned the static `/ui/` interface into a unified, high-performance console dashboard:
  - **Quick Test Console**: Direct sandbox to prompt the Grok Agent with selectable execution modes (`auto`, `fast`, `reasoning`, `thinking`, `research`), live message bubbles, and real-time latency indicators.
  - **Schema Explorer**: Interactive inspector to browse the JSON schemas of Grok-native results (`AgentResult`, `ChatResult`, etc.).
  - **Reasoning Guard Simulator**: Interactive simulator to test router blocking policies across different models and thresholds.
  - **OKF Browser**: Direct markdown renderer for zero-shot Open Knowledge Format files.
  - **WebMCP Manifest Inspector**: Client prober and manifest viewer for client-side tool registrations.
  - **Telemetry & Metrics Dashboard**: Real-time tracking of daily cost, latency averages, plane distributions, and raw telemetry reports.
  - **Onboarding (Self)**: Visual prober for `discover_self` zero-configuration onboarding payloads.
  - **RPC Wire Logs**: Right-side inspector panel detailing every low-level JSON-RPC request and response transaction in the session.

### Fixed
- **Client Payload Argument Mapping**: Corrected `app.js` to pass `prompt` instead of `task` when calling the `agent` tool, resolving a Pydantic validation error against the exposed FastMCP tool schema.

## [0.3.0] - 2026-07-09

### Added
- **Grok-Native Pydantic v2 Schemas**: Implemented strict, type-safe schema models (`BaseResult`, `ChatResult`, `AgentResult`, `ReflectionResult`, `MediaResult`, `SystemResult`) under `src/models/results.py`. Every gateway and tool function now returns these structured objects, enabling FastMCP to auto-generate JSON schemas and document type contracts.
- **Reasoning Guard & Enforcement**: Added `require_reasoning_level` parameters and logic to `agent` and `chat`. Enforces pre-flight checks against `.grok/hyperparams` reasoning levels (`none`, `low`, `medium`, `high`) to block and prevent fallback to low-intelligence models.
- **Zero-Shot OKF Bundle**: Created an agent-readable documentation bundle at `/docs/okf/` (spec v0.1) containing an index, manifest, and topic-specific files so that headless agents can auto-ingest the gateway capabilities.
- **WebMCP Self-Description Layer**: Integrated browser-native model context tool registrations (`get_schema`, `example_call`, `simulate_reasoning_guard`, `fetch_okf_bundle`) in the Test Console (`/ui/`) using `document.modelContext`. Exposes a static JSON manifest at `/.well-known/webmcp` (exempted from gateway auth).
- **`discover_self` Tool**: Added the `grok_mcp_discover_self` MCP tool, returning OKF manifest paths and WebMCP targets in a single call for zero-configuration agent onboarding.

### Fixed
- Test suite assertion updates across 607 tests, changing dictionary lookups (`res["field"]`) to Pydantic object attribute lookups (`res.field`).

---

## [0.2.0] - 2026-07-06

### Added
- Unified metadata tracking for all media generation tools.
- Real-time catalog listing using dynamic endpoints with fallback profiles.
- Defensive fallback warnings surfaced directly through downstream agent logs.
