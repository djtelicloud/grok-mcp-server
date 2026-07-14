---
okf_version: "0.1"
title: "Agent Entrypoint Tool"
type: "topic"
description: "Surface-scoped guide to the stable HTTP agent and trusted stdio agent tools."
---

# Agent Entrypoint Tool

The `agent` tool is the headline unified entrypoint for UniGrok.

> **Surface scope:** the stable HTTP service at `:4765/mcp` exposes the public
> `agent` documented first below. It is workspace-neutral and cannot browse the
> IDE's files. Trusted stdio has a different `agent` signature and the broader
> source tool set; contributor Forge at `:4766/mcp` adds repository memory and
> Swarm tools to the stable HTTP surface. Always use live MCP `tools/list` as
> the deployed contract.

## Tool Signatures & Schemas

### Stable HTTP `agent`
Primary entry point for ordinary IDE calls to `:4765/mcp`.
- **Parameters**:
  - `prompt` (string, required): The goal or instruction.
  - `session` (string, optional): Context thread persistence.
  - `system_prompt` (string, optional): Additional system instruction.
  - `workspace_context` (string, optional): Deliberately selected project text
    supplied by the calling IDE; it grants no filesystem authority.
  - `workspace_label` (string, optional): Human-readable label for that text.
  - `mode` (string, optional): `"auto"` (default), `"fast"`, `"reasoning"`, `"thinking"`, or `"research"`.
  - `model` (string, optional): Enforce a specific Grok model ID.
  - `plane` (string, optional): starting credential plane — `"auto"`
    (server policy), `"cli"` (SuperGrok subscription), or `"api"` (metered
    developer API).
  - `fallback_policy` (string, optional): `"same_plane"` forbids crossing the
    billing boundary; `"cross_plane"` permits bounded recovery on the other
    xAI credential plane.
- **Response Shape (`AgentResult`)**:
  ```json
  {
    "response": "Final completed answer...",
    "text": "Human-formatted answer text with footer info...",
    "finish_reason": "final_answer",
    "cost_usd": 0.0125,
    "model": "grok-4.5",
    "profile": "grok-4.5",
    "tokens": 4096,
    "latency_sec": 4.25,
    "route": "agentic",
    "plane": "API",
    "reasoning_effort": null,
    "citations": [
      {
        "url": "https://example.com/source"
      }
    ],
    "why": "auto",
    "routing": {
      "v": 1,
      "route_class": "planning",
      "resolved_model": "grok-4.5",
      "why_detail": "reasoning_score",
      "features": {"reason_score": 4, "feature_hash": "9f5f1f12f612"},
      "candidates": [{"model": "grok-4.5", "rank": 0, "selected": true}],
      "evidence_source": "static",
      "catalog": {"source": "xai_api", "fallback": false}
    },
    "credentials": {
      "version": 1,
      "policy": "cli_first",
      "preferred_plane": "CLI",
      "effective_plane": "API",
      "service_usable": true,
      "notices": [
        {
          "id": "cli:needs_auth:missing",
          "plane": "CLI",
          "blocking": false,
          "prompt_user": true,
          "action_id": "authenticate_grok_cli"
        }
      ]
    },
    "degraded": false,
    "trace": null
  }
  ```

### Trusted stdio `agent`

The full stdio server also registers an `agent` whose required input is
`task`. It adds `require_reasoning_level` and MCP progress reporting, and it may
use local file/git/test tools within the resolved trusted workspace. An
explicit `WORKSPACE_ROOT` wins; ordinary local stdio otherwise resolves to the
UniGrok service root, never whichever IDE project happens to call it. That
signature is not the stable HTTP contract.

### Trusted stdio `grok_agent`
Dedicated tool wrapping a ReAct AgentLoop in a schema-enforced reflection review loop with strict iteration and budget constraints. It is not exposed by the stable or Forge HTTP service.
- **Parameters**:
  - `prompt` (string, required): Task description.
  - `session` (string, optional): Persistent conversation thread.
  - `model` (string, optional): Defaults to `grok-4.5`.
  - `system_prompt` (string, optional): Custom instructions.
  - `max_iterations` (integer, optional): Retries limit (max 10, default 5).
  - `cost_limit` (number, optional): Spending budget in USD (default 0.50).
- **Response Shape (`AgentResult`)**: Identical structure to `agent` output.

## Operational Modes

1. **Auto-routing (`auto`)**: Self-routes through bounded planning, coding,
   vision, and research capability classes. Compatible unpinned local work is
   CLI-first; explicit pins and API-native capabilities stay on API. The
   cached live catalog filters API candidates; mature local evidence can
   replace a stable default only after clearing the quality margin.
2. **Fast path (`fast`)**: Toolless single turn.
3. **Reasoning path (`reasoning`)**: Enforces planning model.
4. **Thinking path (`thinking`)**: Executes thinking loop with reflection review.
5. **Research mode (`research`)**: Selects the live Grok 4.20 multi-agent model,
   triggers fan-out (agent count 4 or 16), and collects inline sources in
   `citations`.

The `routing` object is the explanation source of truth. It never contains the
prompt: only bounded features, model slugs, evidence counts, catalog state, and
failover facts safe to persist in local telemetry and display in the UI.

The `credentials` object is the action source of truth. On first connection,
inspect `notices`, prompt once per notice id, and ask the user before running
installation, device-auth, or secret-configuration actions. Never request
`XAI_API_KEY` in chat or store it in the caller's project.

When CLI-first is active, the selected CLI slug comes from the authenticated
live catalog returned by the `grok models` readiness probe. Coding prefers
composer; reasoning prefers the CLI's reported default. Explicit API model
pins remain on API even when the same slug is also exposed by the CLI
subscription.
