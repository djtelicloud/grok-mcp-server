---
name: using-unigrok
description: VS Code Copilot local skill for querying xAI Grok through the shared UniGrok MCP gateway. Use when you need Copilot-specific Grok workflows, per-task sessions, cross-repo context handoff, or Grok second-opinion review.
---

# VS Code Copilot Local UniGrok Skill

This is a Copilot-specific skill layer in the repository root `.copilot` tree.
Keep using `.github/skills/using-unigrok/SKILL.md` as the shared team skill for
cross-repo work; this local copy exists to tune Copilot behavior without
impacting other IDE ecosystems.

## Tool names

Prefer whichever registered namespace exists:

- `mcp__unigrok__agent`
- `mcp__grok__agent`

## Copilot calling profile

Use:

- `prompt` with complete user intent and constraints
- `mode=reasoning` for peer review / architecture checks
- `mode=research` for citation-grounded web/X synthesis
- stable `session` per task for continuity and lower follow-up cost
- `workspace_context` and optional `workspace_label` when the target repo is
  not this admin repository

Do not assume UniGrok can browse the caller's repo; send explicit context.

## Output expectations

When relevant, report: `response`, `model`, `route`, `plane`, `degraded`,
`cost_usd`, `tokens`, `latency_sec`, and `citations`.

## Safety boundary

- Never request `XAI_API_KEY` in chat or place it in repo files.
- Treat `credential_planes` state from tool output as authoritative.
- On failure, provide one concrete next step (`/healthz`, CLI auth, MCP
  registration, or header fix).

## Discovery note

Copilot auto-discovers project skills from `.github/skills`, `.agents/skills`,
and `.claude/skills`. Use this `.copilot` location as a Copilot-local
customization source you can register explicitly when needed.
