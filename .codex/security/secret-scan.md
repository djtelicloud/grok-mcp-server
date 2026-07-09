# Codex Secret Scan

Use this checklist when Codex changes `.codex/`, provider namespace files,
logging, telemetry, CLI command construction, or credential setup flows.

## Patterns

Scan for concrete secret values, not harmless prose:

- `XAI_API_KEY` environment assignments
- `OPENAI_API_KEY` environment assignments
- `Authorization: Bearer ...`
- OpenAI key prefixes such as `sk-proj-`, `sk-live-`, `sk-test-`, and
  `sk-svcacct-`
- GitHub personal access tokens beginning with `ghp_`
- xAI-style key tokens beginning with `xai-`

## Codex Actions

- Run `bash .codex/scripts/validate-codex-namespace.sh` after `.codex`
  changes.
- Use a focused `rg` scan over changed files before staging provider namespace
  or telemetry changes.
- If Computer Use sees a credential in app UI, do not copy it into repo files.
- If OpenAI credentials are needed, use the Codex OpenAI Platform key flow
  documented in `.codex/openai-platform/api-key-flow.md`.

## Keep Out

- Do not store copied `~/.codex/config.toml` values.
- Do not store Antigravity permission grants or absolute provider worktree
  write paths in `.codex`.
- Do not weaken scans to avoid false positives; refine patterns instead.
