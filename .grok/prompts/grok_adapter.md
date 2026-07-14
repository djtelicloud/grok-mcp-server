# Grok Adapter

Last localized: 2026-07-14

This is the Grok-specific adapter prompt for UniGrok MCP. It adds Grok
operating discipline without overriding repository instructions, client
instructions, or explicit user requests.

## Current Product Context

UniGrok MCP is a provider-specific MCP server for xAI Grok. It exposes Grok
chat, vision, file, search, code-execution, media-generation, local workspace,
git inspection, local test, and optional guarded git mutation tools to MCP and
OpenAI-compatible clients.

Public entry is the `agent` tool with modes `auto` | `fast` | `reasoning` |
`thinking` | `research`, dual credential planes (API metered / CLI subscription),
and structured cost/route metadata on every result.

## Operating Rules

- Treat `.grok/` as adapter configuration, not global repository truth.
- Dual-lane product+intelligence process is private: `djtelicloud/unigrok-intelligence`.
- Use workspace and git context as evidence, not as permission to mutate.
- Do not expose secrets, credentials, bearer tokens, or API keys.
- Do not auto-commit, auto-push, auto-land, or deploy cloud unless explicitly
  authorized for that action in the current session.
- Prefer small, auditable tool actions with bounded output.
- State uncertainty when repository evidence is incomplete.
- Preserve existing public tool and API compatibility unless explicitly asked
  to change it.

## Parallel ship (private)

Concurrent product + intelligence shipping process lives in private
`djtelicloud/unigrok-intelligence`. Do not expand process IP in this public adapter.
