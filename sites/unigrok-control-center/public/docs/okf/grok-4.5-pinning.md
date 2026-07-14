---
okf_version: "0.1"
title: "Model Pinning & Profiles"
type: "topic"
description: "Model selection profiles, aliases (planning/coding), live discovery, and API vs CLI routing paths."
---

# Model Pinning & Profiles

UniGrok supports runtime model pinning, live model catalog discovery, hyperparameter profiling, and keyless local CLI fallback paths.

> **Surface scope:** stable and Forge HTTP clients pin models through the
> public `agent(model=..., plane=..., fallback_policy=...)` contract. Trusted
> stdio tools expose additional model-bearing functions. A model appearing in
> this document or the generated source reference is not proof that it exists
> on either credential plane; use `grok_mcp_discover_self(include_models=true)`
> or the live provider catalogs.

## Model Profiles & Hyperparameters

Profiles are loaded from JSON files inside `.grok/hyperparams/` and control
settings such as temperature, top_p, thinking mode, and the system prompt.
For example, a current bundled reasoning profile declares:
```json
{
  "temperature": 0.4,
  "top_p": 0.95,
  "thinking_mode": true,
  "system_prompt_ref": "grok_adapter.md"
}
```

Current bundled profiles omit `reasoning_effort`, so the normalized value is
`null` (`none` for guard comparison). Do not infer an effort level from the
model slug or `thinking_mode`.

## API and CLI credential planes

### 1. CLI Plane (preferred compatible path)

The local default policy is `UNIGROK_PLANE_POLICY=cli_first`.

- Uses the service's Grok CLI grok.com OAuth session, not `XAI_API_KEY`.
- Readiness is a bounded, API-key-stripped `grok models` probe.
- Exact model availability comes from that authenticated catalog.
- Native CLI session IDs provide continuation for compatible calls.
- The CLI adapter does not expose every API ReAct/server-tool capability.

### 2. API Plane
Direct HTTPS connections to the xAI endpoint (`api.x.ai`).
- Uses `XAI_API_KEY` from the environment.
- Supports streaming, tool calling, and full structured output (critique JSON schemas).

Explicit `plane="cli"` or `plane="api"` requests are strict. Use
`fallback_policy="same_plane"` when crossing subscription and metered billing
boundaries is forbidden. `cross_plane` allows only bounded, compatible
recovery; it does not make a model available on a catalog where it is absent.

## Live Catalog Discovery & Fallbacks
UniGrok caches live catalog discovery. If API discovery fails, its bundled
directory supplies routing candidates, but execution still requires the
selected model to be available on the resolved credential plane. Current
cold-start capability defaults include:
- **Premier default**: `grok-4.5`
- **Default coding**: `grok-build-0.1`
- **Planning default**: `grok-4.5`
- **Planning fallback**: `grok-4.3`
- **Multi-agent research**: the available `grok-4.20-multi-agent*` catalog slug

Auto-routing does not equate "newest" with "best." It filters a bounded
capability-class candidate list, keeps a stable cold-start default, and only
lets fresh calibration or mature local telemetry promote a peer by the
configured quality margin. Explicit pins and environment overrides take
selection precedence. Strict `plane="cli"` and `plane="api"` requests validate
against that plane; `plane="auto"` may defer availability checks to execution.
