---
okf_version: "0.1"
title: "Silent Team Check"
type: "topic"
description: "Low-cost advisory habit for pressure-testing non-trivial agent work with one cheap second opinion before answering."
---

# Silent Team Check

Silent team check is the token-efficient version of a hive-mind vote for this
repository's day-to-day IDE agents.

## Why this exists

UniGrok's stable HTTP path does not expose a local fan-out, merge, or vote
engine for ordinary IDE turns. When an agent wants extra pressure-testing, the
safe default is not to spawn a swarm. The safe default is to ask one cheap
reviewer first, then escalate only when the first check finds real risk or
disagreement.

## Default pattern

1. Use a silent team check for non-trivial plans, risky edits, ambiguous
   architecture, debugging, or review work.
2. Start with one cheap reviewer only:
   - UniGrok `agent(mode="fast")` or `agent(mode="reasoning")` when a Grok
     second opinion fits.
   - A rubber-duck or code-review pass when a local subagent is cheaper or
     better matched to the task.
3. Do not fan out by default. Escalate to at most one more reviewer only when:
   - the first check materially disagrees,
   - the change is high risk,
   - or the task crosses architecture, security, and release concerns.
4. Treat the check as advisory pressure-testing, not authority. Synthesize the
   result into the final answer instead of narrating internal debate unless the
   user asks.

## User-facing disclosure

For higher-risk tasks, a small footer such as `team-check: passed` or
`team-check: escalated` is enough. Do not add that footer to trivial lookups or
mechanical one-step fixes.

## Boundaries

- Skip the extra check when the cost would outweigh the benefit.
- Keep credentials and metered model decisions inside the normal UniGrok
  boundaries; never request `XAI_API_KEY` in chat.
- Prefer explicit `workspace_context` when the second opinion depends on repo
  state, because the stable lane is workspace-neutral.
