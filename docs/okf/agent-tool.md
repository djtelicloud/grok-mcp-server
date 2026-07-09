---
okf_version: "0.1"
title: "Agent Entrypoint Tool"
type: "topic"
description: "Detailed guide on using the unified UniGrok agent tool, modes, progress reporting, and multi-agent fan-out."
---

# Agent Entrypoint Tool

The `agent` tool is the headline unified entrypoint for UniGrok. It automatically manages model routing, plane failover, context injection, and local filesystem access (web search, X search, and sandboxed python execution).

## Tool Signatures & Schemas

### `agent`
Primary entry point for any agent task.
- **Parameters**:
  - `task` (string, required): The goal or instruction.
  - `session` (string, optional): Context thread persistence.
  - `mode` (string, optional): `"auto"` (default), `"fast"`, `"reasoning"`, `"thinking"`, or `"research"`.
  - `model` (string, optional): Enforce a specific Grok model ID.
  - `require_reasoning_level` (string, optional): `"low"`, `"medium"`, or `"high"`.
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
    "reasoning_effort": "high",
    "citations": [
      {
        "url": "https://example.com/source"
      }
    ],
    "why": "auto",
    "degraded": false,
    "trace": null
  }
  ```

### `grok_agent`
Dedicated tool wrapping a ReAct AgentLoop in a schema-enforced reflection review loop with strict iteration and budget constraints.
- **Parameters**:
  - `prompt` (string, required): Task description.
  - `session` (string, optional): Persistent conversation thread.
  - `model` (string, optional): Defaults to `grok-4.3`.
  - `system_prompt` (string, optional): Custom instructions.
  - `max_iterations` (integer, optional): Retries limit (max 10, default 5).
  - `cost_limit` (number, optional): Spending budget in USD (default 0.50).
- **Response Shape (`AgentResult`)**: Identical structure to `agent` output.

## Operational Modes

1. **Auto-routing (`auto`)**: Sel-routes based on prompt score.
2. **Fast path (`fast`)**: Toolless single turn.
3. **Reasoning path (`reasoning`)**: Enforces planning model.
4. **Thinking path (`thinking`)**: Executes thinking loop with reflection review.
5. **Research mode (`research`)**: Triggers multi-agent fan-out (agent count 4 or 16) with inline sources collected in `citations`.
