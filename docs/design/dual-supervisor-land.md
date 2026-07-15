# Dual supervisor land: Codex + Cursor

**Status:** product law (docs) · **protection plan** below is maintainer-applied  
**Audience:** insiders, Cursor Automations, Codex integration sessions

## Problem

Parallel contributors open many Ready packets. Codex is the default integrator
and also the namesake of a **required** GitHub status (`Codex Approval`). That
makes Codex a queue bottleneck even when Cursor already has cloud **Bugbot**,
**Security Reviewer**, **Approver**, and merge-capable agents.

## Roles (who does what)

| Actor | Low-risk green (docs, rules, smoke, pure tests) | Medium/high (billing, auth, planes, hydration budgets, credentials, land scripts, release) |
| --- | --- | --- |
| Contributor agents | Draft PR → undraft when Ready | Same; never invent land authority |
| Cursor Bugbot | Findings on head | Findings on head |
| Cursor Security Reviewer | One security pass | One security pass; block on issues |
| Cursor PR Approver | Approve when green + low-risk | **Block** for human/Codex (comment once) |
| Cursor merger (cloud) | **May merge** when Codex busy and protection allows | **Never** |
| Codex / project-admin | Optional when free | **Default:** exact-head review, Approval dispatch, `scripts/land`, merge, release/deploy |
| Grok | Advisory second opinion only | Advisory only — does not authorize merge |

**Hard rules**

1. **One land owner per PR head** — no Approver thrash + Codex thrash on the same SHA.
2. **Single-pass** Cursor Automations (see `.agents/AGENTS.md` and `.cursor/rules/cursor-automations-single-pass.mdc`).
3. **No admin bypass by default.** Human sponsor may authorize admin merge for a named head; do not make it habit.
4. **Grok review is advisory** (CONTRIBUTING).

## Risk classification (heuristic)

| Label | Examples |
| --- | --- |
| **low** | Markdown/docs, `.cursor/rules`, smoke checklists, pure tests with no production path change |
| **medium** | Runtime behavior, routing, planes, budgets/hydration, auth surfaces, dependency bumps with behavior |
| **high** | Credentials, `scripts/land`, branch protection, release/deploy, public MCP final-answer path |

Contributors should undraft Ready packets and state risk in the PR body
(`risk: low|medium|high`). Approver defaults to **block** when risk is unclear.

## Flow

```text
Ready undrafted PR
  → Bugbot (pass / findings)
  → Security Reviewer (once)
  → CI green (required suite)
  → Approver: low? approve : block for Codex
  → Land owner:
       low + Codex busy → Cursor merger (if protection allows)
       medium/high or Codex free for culture → Codex (Approval + land)
  → Live main
```

## Branch protection plan (maintainer)

**Live today (observed):** required contexts include `build (3.11)`, `build (3.12)`,
`Project Site`, `Control Cloud Run Image`, `evals-offline`, `docker`, and
**`Codex Approval`**; strict updates; 1 approving review + CODEOWNERS
(`@djtelicloud`).

**Gap:** Cursor Approver approval does **not** satisfy the `Codex Approval`
**status check**. Cursor can review; it cannot replace that check without a
protection/workflow change.

### Recommended options (pick one; apply outside this doc PR)

| Option | Change | Effect |
| --- | --- | --- |
| **A. Label-gated** (preferred) | Keep `Codex Approval` required for unlabeled/medium/high. For PRs labeled `risk:low` only, make `Codex Approval` optional **or** auto-dispatch owner workflow after green CI + Cursor approve | Unbottles docs queue |
| **B. Dual status** | Add optional `Cursor Approval` context for low-risk; keep Codex for everything else | Clearer bots; more wiring |
| **C. Status quo + process** | No protection change; Cursor only reviews; human/Codex lands all | Simplest; bottleneck remains |

**Do not** remove all required reviews or disable CODEOWNERS.  
**Do not** grant Cursor merger medium/high by default.

### Workflow note

`.github/workflows/codex-approval.yml` is owner-dispatched and binds the exact
head. Any automation that auto-dispatches for `risk:low` must re-run on every
new commit (stale approval is invalid).

## Operator checklist

1. Contributor: undraft, CI green, risk label/body, exact HEAD.  
2. Cursor: Bugbot → Security → Approver (one action per head).  
3. If low-risk and Codex busy: merger merges when protection allows.  
4. If medium/high or Approver blocked: Codex Approval + land.  
5. Owner removes finished scratchpad after Live.

## Non-goals

- Replacing Codex for release/deploy  
- Multi-pass Automations thrash  
- Treating SuperGrok/simulation chats as land authority  
- Promoting non-Grok providers to public final MCP output  
