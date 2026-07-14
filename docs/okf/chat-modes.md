---
okf_version: "0.1"
title: "Chat & Context Modes"
type: "topic"
description: "Trusted-stdio chat, stateful conversation, vision, and file tools, with explicit HTTP scope."
---

# Chat & Context Modes

UniGrok's full trusted stdio server supports direct completion, stateful
conversation continuation, vision analysis, file ingestion, and structured
critique.

> **Surface scope:** none of the tools on this page are exposed by the stable
> HTTP service at `:4765/mcp`. Contributor Forge at `:4766/mcp` adds
> repository-memory and Swarm tools, not these chat tools. HTTP clients use the
> public `agent` entrypoint instead. Confirm every deployment with MCP
> `tools/list`.

## Schema contracts

All chat and context tools return `ChatResult` (inheriting from `BaseResult`) with the exception of `grok_reflect` which returns `ReflectionResult`.

### `ChatResult` Schema
```json
{
  "response": "Raw text answer...",
  "text": "Formatted answer with footers/citations...",
  "finish_reason": "final_answer",
  "cost_usd": 0.002,
  "model": "grok-build-0.1",
  "profile": "grok-build-0.1",
  "tokens": 400,
  "latency_sec": 1.15,
  "route": "fast",
  "plane": "API",
  "reasoning_effort": null,
  "citations": null,
  "response_id": "resp-12345",
  "session": "test-session"
}
```

## Trusted stdio tools

### 1. `chat`
Sends a direct text prompt to a Grok model.
- **Parameters**:
  - `prompt` (string, required): Message text.
  - `session` (string, optional): Local session name.
  - `model` (string, optional): Default `"grok-build-0.1"`.
  - `system_prompt` (string, optional): Injected instructions.
  - `agent_count` (integer, optional): `4` or `16`, only for a compatible
    Grok 4.20 multi-agent model.
  - `enable_agentic` (boolean, optional): If `True` (default), runs in ReAct loop.
  - `require_reasoning_level` (string, optional): `"low"`, `"medium"`, or
    `"high"`; enforced before inference execution. Live catalog discovery may
    occur first. Current bundled profiles
    declare no effort, so any value above `none` fails closed.

### 2. `stateful_chat`
Appends to a server-side conversation thread at xAI.
- **Parameters**:
  - `prompt` (string, required): Follow-up text.
  - `model` (string, optional): Default `"grok-4.5"`.
  - `response_id` (string, optional): Prior stateful ID to resume from.
  - `system_prompt` (string, optional): Initial system prompt.

### 3. `chat_with_vision`
Analyzes local image files or public image URLs.
- **Parameters**:
  - `prompt` (string, required): Text instruction.
  - `session` (string, optional): Session name.
  - `model` (string, optional): Default `"grok-4.5"`.
  - `image_paths` (array of strings, optional): Paths to local files (PNG/JPG).
  - `image_urls` (array of strings, optional): Public URLs.
  - `detail` (string, optional): `"auto"`, `"low"`, `"high"`.

Local paths resolve inside the trusted stdio workspace. An explicit
`WORKSPACE_ROOT` wins; ordinary local stdio otherwise uses the UniGrok service
root. Merely registering stable HTTP does not grant access to an IDE project.

### 4. `chat_with_files`
Infers across uploaded document files.
- **Parameters**:
  - `prompt` (string, required): Core request.
  - `file_ids` (array of strings, required): IDs returned by `xai_upload_file`.
  - `session` (string, optional): Session name.
  - `model` (string, optional): Default `"grok-4.5"`.

### 5. `grok_reflect` (Structured Critique)
Determines strict critique review parameters through structured output schemas.
- **Parameters**:
  - `subject` (string, required): Text or code content to review.
  - `criteria` (string, optional): Rules for critique.
  - `context` (string, optional): Additional baseline settings.
  - `model` (string, optional): Default `"grok-4.5"`.
- **Response Shape (`ReflectionResult`)**:
  ```json
  {
    "ok": true,
    "critique": {
      "verdict": "needs_changes",
      "summary": "Review summary details...",
      "strengths": ["Item A"],
      "issues": ["Item B"],
      "recommendations": ["Item C"],
      "next_action": "Modify file x",
      "confidence": 0.95
    },
    ...
  }
  ```
