# Changelog

All notable changes to UniGrok MCP will be documented in this file.

## [Unreleased]

## [0.5.2] - 2026-07-10

### Fixed
- **Live CLI model selection**: CLI-first routing now consumes the model list
  verified by the cached OAuth health probe. Reasoning prefers the live CLI
  default (`grok-4.5` currently), coding prefers
  `grok-composer-2.5-fast`, and the retired `grok-build` slug is no longer a
  fallback. A model name shared by API and CLI is forced through the plane
  selected in the routing receipt, so explicit API pins remain API pins.
- **Persistent auth-volume ownership**: The permission-gated device-auth
  action and Compose auth helper repair the dedicated volume as root, then
  drop to uid/gid 1000 before login. Existing root-owned volumes can refresh
  credentials without storing root-only tokens or manual Docker surgery.

## [0.5.1] - 2026-07-10

### Added
- **Credential-plane action contract**: Discovery, status JSON, `/runtimez`,
  public agent results, and the Control Center now expose the same non-secret
  CLI/API readiness, one-shot prompt notices, and permission-gated install,
  device-auth, and server-secret actions.
- **Safe blocked state**: When neither plane can run model work, `agent`
  returns an actionable local error instead of attempting doomed upstream
  calls or asking for secrets in chat.

### Changed
- Compatible unpinned local requests are CLI-first by default to favor the
  subscription allowance. Explicit/environment model pins and API-native
  thinking, vision, and multi-agent research retain API precedence.
- Control Center credential warnings distinguish the xAI service key from an
  optional UniGrok client token. Advanced organization billing setup no longer
  reads like a prerequisite; local API cost and CLI activity work without it.

## [0.5.0] - 2026-07-10

### Added
- **Explainable adaptive routing**: Replaced the two-fixed-alias illusion with
  bounded capability classes for planning, coding, vision, and research.
  Auto mode now filters a cached live catalog, cold-starts planning on
  `grok-4.5`, routes research to the available Grok 4.20 multi-agent model,
  and permits mature calibration or local telemetry to displace a stable
  default only after a 15-point quality margin.
- **Routing receipts**: Every unified-agent result, telemetry row, task-memory
  record, and session turn can carry the same versioned, prompt-free receipt:
  route class, bounded task features, selected model, candidates, catalog
  source, evidence source, pin source, and failover reason. The Control Center
  shows recent expandable receipts plus route-class and selection-reason
  summaries without exposing prompts.

### Changed
- The planning, vision, reflection, and stateful reasoning default is now
  `grok-4.5`; explicit model pins and environment overrides retain absolute
  precedence. Catalog discovery is shared behind a 15-minute cache and safely
  falls back to the bundled model directory when unavailable.
- Research mode now reaches the research capability class rather than being
  flattened into ordinary reasoning before selection.

## [0.4.2] - 2026-07-10

### Added
- **Usage truth and Control Center v0.4.2**: Replaced Markdown scraping with a
  structured MCP metrics view; added today/lifetime, plane, model, caller,
  latency, success, token-coverage, and billing-boundary displays; recorded
  provider-exact API usage and clearly labeled local CLI estimates; added an
  optional cached xAI Management API team-usage comparison without mixing it
  into SuperGrok subscription statistics; and hardened tablet/mobile layouts.
- **Grok Dial Plan**: Made `4765` (telephone-keypad `GROK`) the canonical
  stable endpoint and `4766` the contributor Forge endpoint. An optional
  Compose overlay adds `AUTO=2886`, `FAST=3278`, `REAS=7327`, `THNK=8465`, and
  `RSCH=7724` as mode-default aliases into the same stable process and state.
  Explicit `agent.mode` arguments take precedence, and `/runtimez` reports the
  dial observed for the current request.

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
