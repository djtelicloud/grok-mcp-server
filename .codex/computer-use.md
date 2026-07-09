# Codex Computer Use

Use Computer Use only for Codex desktop tasks that require operating macOS app
UI directly.

## Allowed Uses

- Inspect a running Mac app when the user refers to visible UI.
- Click, type, press keys, or scroll after observing the app state.
- Use app UI when no connector, CLI, or browser route is more precise.

## Required Flow

1. Call `mcp__computer_use.get_app_state` before interacting with the app.
2. Use element identifiers from the accessibility tree when available.
3. Keep actions minimal and reversible.
4. Ask for explicit approval before destructive UI actions, credential entry,
   purchases, sends, deletes, or irreversible settings changes.

## Keep Out

- Do not use Computer Use to bypass Codex sandbox approvals.
- Do not copy secrets from app UI into repository files.
- Do not use app control when a Codex connector or local command gives a safer
  structured path.

