---
okf_version: "0.1"
title: "Chat & Context Modes"
type: "topic"
description: "Detailed description of direct chat, stateful conversation, vision-capable analysis, and document-backed chats."
---

# Chat & Context Modes

UniGrok supports direct message completion, stateful conversation continuation, vision analysis, file ingestion, and structured critique.

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

## Available Tools

### 1. `chat`
Sends a direct text prompt to a Grok model.
- **Parameters**:
  - `prompt` (string, required): Message text.
  - `session` (string, optional): Local session name.
  - `model` (string, optional): Default `"grok-build-0.1"`.
  - `system_prompt` (string, optional): Injected instructions.
  - `enable_agentic` (boolean, optional): If `True` (default), runs in ReAct loop.

### 2. `stateful_chat`
Appends to a server-side conversation thread at xAI.
- **Parameters**:
  - `prompt` (string, required): Follow-up text.
  - `model` (string, optional): Default `"grok-4.3"`.
  - `response_id` (string, optional): Prior stateful ID to resume from.
  - `system_prompt` (string, optional): Initial system prompt.

### 3. `chat_with_vision`
Analyzes local image files or public image URLs.
- **Parameters**:
  - `prompt` (string, required): Text instruction.
  - `session` (string, optional): Session name.
  - `model` (string, optional): Default `"grok-4.3"`.
  - `image_paths` (array of strings, optional): Paths to local files (PNG/JPG).
  - `image_urls` (array of strings, optional): Public URLs.
  - `detail` (string, optional): `"auto"`, `"low"`, `"high"`.

### 4. `chat_with_files`
Infers across uploaded document files.
- **Parameters**:
  - `prompt` (string, required): Core request.
  - `file_ids` (array of strings, required): IDs returned by `xai_upload_file`.
  - `session` (string, optional): Session name.
  - `model` (string, optional): Default `"grok-4.3"`.

### 5. `grok_reflect` (Structured Critique)
Determines strict critique review parameters through structured output schemas.
- **Parameters**:
  - `subject` (string, required): Text or code content to review.
  - `criteria` (string, optional): Rules for critique.
  - `context` (string, optional): Additional baseline settings.
  - `model` (string, optional): Default `"grok-4.3"`.
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
