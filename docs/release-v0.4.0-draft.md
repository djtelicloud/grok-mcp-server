# Release Draft: v0.4.0

## Tag: `v0.4.0`
## Target: `main`
## Release Name: `v0.4.0 - Premium Control Center UI Redesign & Payload Fix`

### Docker Image Tags
- `djtelicloud/grok-mcp:v0.4.0`
- `djtelicloud/grok-mcp:latest`

---

## Release Notes

This release introduces the all-new, high-fidelity **UniGrok Control Center UI v0.4.0** and resolves critical client-side tool payload compatibility issues.

### Key Capabilities & Enhancements

1. **Control Center UI Redesign (v0.4.0)**
   The `/ui/` interface has been completely transformed into a rich multi-tab interactive workbench for developers and agents alike:
   - **Quick Test Console**: A prompt sandbox featuring selectable execution modes (`auto`, `fast`, `reasoning`, `thinking`, `research`) and live conversation bubbles.
   - **Result Shape Guide**: Illustrative result-field examples; live MCP `tools/list` remains the authoritative schema source.
   - **Reasoning Guard Preview**: Local preview of reasoning-level checks; it does not execute or prove the live router path.
   - **OKF Browser**: Renders Open Knowledge Format documentation dynamically with YAML headers stripped.
   - **WebMCP Tester**: Manifest inspector and client bridge prober.
   - **Telemetry & Metrics**: Displays cost tracking, average latency, and raw Prometheus metrics.
   - **Onboarding (Self)**: Displays dynamic outputs from the onboarding helper (`discover_self`).

2. **RPC Wire Logs & Payload Inspector**
   A new real-time inspector displays low-level JSON-RPC transport frames (`tools/call` for `agent`, `discover_self`, etc.) and results, detailing token counts, latency, billing caller metadata, and circuit breaker status.

3. **Client Payload Alignment (Bug Fix)**
   Fixed a critical mismatch where `app.js` was passing `task` instead of `prompt` to the exposed FastMCP `agent` tool. The client now maps correct argument names, resolving validation issues on the console prompt channel.
