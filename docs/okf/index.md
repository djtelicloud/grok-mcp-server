---
okf_version: "0.1"
title: "UniGrok MCP Gateway"
type: "index"
description: "Zero-shot agent knowledge bundle for discoverability and usage of the UniGrok MCP server."
topics:
  - "agent-tool"
  - "copilot-agent-playbook"
  - "chat-modes"
  - "reasoning-guard"
  - "grok-4.5-pinning"
  - "media-imagine"
  - "metrics-tool"
  - "intelligence-payload-profiles-v1"
  - "faq"
  - "api-reference"
---

# UniGrok MCP Gateway

Welcome, Agent. This is the Open Knowledge Format (OKF) index for UniGrok, a
local-first gateway for xAI's Grok model family built for the Model Context
Protocol (MCP).

Use this bundle for routing, schema, and operational reference. It documents
more than one execution surface; it is not a substitute for MCP `tools/list`.

## Surface map

- **Stable HTTP (`:4765/mcp`)**: `agent`, `review_pull_request`,
  `grok_mcp_status`, `grok_mcp_discover_self`, and the disabled-by-default
  `grok_mcp_restart_container` maintenance helper.
- **Contributor Forge HTTP (`:4766/mcp`)**: the stable surface plus
  repository-scoped workspace-memory and Swarm tools.
- **Trusted stdio**: the full source tool registry, including direct chat,
  local workspace, media, knowledge, and research tools.
- **Generated API reference**: documented Python symbols, including internal
  and surface-specific functions. Presence there never implies MCP exposure.

Deployments can vary by version and mode. Treat their live `tools/list` as
authoritative.

## Navigation Map
- [Agent Entrypoint](agent-tool.md): Stable HTTP and trusted stdio agent contracts.
- [VS Code Copilot Agent Playbook](copilot-agent-playbook.md): Stable-lane habits, mode selection, context packaging, and self-verification for Copilot.
- [Chat & Context Modes](chat-modes.md): Trusted-stdio direct text, stateful threads, files, and vision tools.
- [Reasoning Guard & Level Enforcement](reasoning-guard.md): Trusted-stdio minimum-profile enforcement and its HTTP boundary.
- [Model Pinning & Profiles](grok-4.5-pinning.md): API vs CLI paths, fallback parameters, and Grok 4.5 capabilities.
- [Media Generation](media-imagine.md): Trusted-stdio API-plane image and video schemas.
- [Observability & Metrics](metrics-tool.md): Aggregated telemetry and status monitoring.
- [Insider Intelligence Payload Profiles](intelligence-payload-profiles-v1.md): Manifest-closed GNO envelopes, evidenced OptiBench cohorts, direct-Pareto preference pairs, and bounded Needle projections carried beside the immutable Capsule v1 format.
- [Verified FAQ](faq.md): Curated operational answers that Grok consults only when relevant.
- [Generated API Reference](api-reference.md): Deterministic signatures and docstrings for documented public Python APIs.
