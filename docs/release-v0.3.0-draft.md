# Release Draft: v0.3.0

## Tag: `v0.3.0`
## Target: `main`
## Release Name: `v0.3.0 - Grok-Native Schemas & Discoverability Layer`

### Docker Image Tags
- `djtelicloud/grok-mcp:v0.3.0`
- `djtelicloud/grok-mcp:latest`

---

## Release Notes

This major release upgrades **UniGrok MCP** into a fully self-describing, zero-configuration agent-native gateway for xAI's Grok 4.5/4.3 model family. 

### Key Capabilities Introduced

1. **Strict Grok-Native Schemas (`Pydantic v2`)**
   Every core gateway and tool response now enforces a strict, typed return shape (`ChatResult`, `AgentResult`, `ReflectionResult`, `MediaResult`, and `SystemResult`), guaranteeing that client integrations get compile-time validation, structured metadata (costs, latency, tokens), and zero shape drift.

2. **Router Reasoning Guard**
   Exposes a new `require_reasoning_level` parameter on the `agent` and `chat` tools. Pre-flight checks prevent runtime execution fallback to low-intelligence fallback models if the target model's profile falls below the required cognitive threshold.

3. **Experimental WebMCP Self-Description**
   Visiting the console UI at `/ui/` now registers native tools (`get_schema`, `example_call`, `simulate_reasoning_guard`, and `fetch_okf_bundle`) under `document.modelContext`. Additionally, a static JSON discovery manifest is exposed at `/.well-known/webmcp` (fully exempted from token auth).

4. **Zero-Shot OKF (Open Knowledge Format) Bundle**
   A structured knowledge base is available at `/docs/okf/` complete with an index and topic files. Heading-less agent swarms can mount/ingest this directory to instantly learn tool signatures and billing policies.

5. **`discover_self` head-less Onboarding**
   Headless agents can invoke `grok_mcp_discover_self` to receive the entire manifest of OKF documentation paths and WebMCP targets in one JSON payload.
