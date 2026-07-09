# Codex OpenAI Platform API Key Flow

Use this only when a task needs an OpenAI API key or an OpenAI-backed app needs
credential setup from inside Codex.

## Codex Route

- Use `mcp__codex_apps__openai_platform._open_codex_api_key_setup` to open the
  Codex key target-selection flow.
- Use `mcp__codex_apps__openai_platform._list_openai_api_key_targets` only when
  target inspection is needed.
- Do not write hand-authored key creation steps when the Codex secure flow is
  available.

## Safety

- The raw key must never appear in tool output, `.codex/`, logs, or final
  responses.
- Confirm any local env-file destination before creating or writing a secret.
- Store only nonsecret process notes in this repository.

