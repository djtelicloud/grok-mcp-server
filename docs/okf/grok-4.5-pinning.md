---
okf_version: "0.1"
title: "Model Pinning & Profiles"
type: "topic"
description: "Model selection profiles, aliases (planning/coding), live discovery, and API vs CLI routing paths."
---

# Model Pinning & Profiles

UniGrok supports runtime model pinning, live model catalog discovery, hyperparameter profiling, and keyless local CLI fallback paths.

## Model Profiles & Hyperparameters

Profiles are loaded from JSON files inside `.grok/hyperparams/` and control settings like temperature, top_p, thinking mode, and default reasoning effort.
For example, a model's profile may declare:
```json
{
  "profile": "grok-4.5-reasoning-high",
  "temperature": 0.3,
  "top_p": 0.95,
  "thinking_mode": true,
  "reasoning_effort": "high",
  "system_prompt_ref": "default-coding.txt"
}
```

## API vs CLI Routing Paths

### 1. API Plane (Main Path)
Direct HTTPS connections to the xAI endpoint (`api.x.ai`).
- Uses `XAI_API_KEY` from the environment.
- Supports streaming, tool calling, and full structured output (critique JSON schemas).

### 2. CLI Plane (Local Fallback)
If the server environment lacks an xAI API Key but has a mounted `grok` CLI subscription binary:
- The router automatically detects the keyless state.
- Executes prompts through local subshell command calls to the `grok` CLI.
- Resets conversation context session IDs (`api_thread_id`) to prevent upstream state desynchronization.

## Live Catalog Discovery & Fallbacks
If the live xAI endpoint is unreachable or model listing fails, UniGrok falls back to a hardcoded list of supported IDs:
- **Premier default**: `grok-4.5`
- **Default coding**: `grok-build-0.1`
- **Standard reasoning**: `grok-4.3`
- **Multi-agent**: `grok-4.20-multi-agent`
