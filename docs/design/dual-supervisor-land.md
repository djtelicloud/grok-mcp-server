# Dual supervisor land: Codex + Cursor

**Status:** product law (docs) · **protection plan** below is maintainer-applied  
**Audience:** insiders, Cursor Automations, Codex integration sessions

## Problem

Parallel contributors open many Ready packets. Codex is the default integrator
and also the namesake of a **required** GitHub status (`Codex Approval`). That
makes Codex a queue bottleneck even when Cursor already has cloud **Bugbot**,
**Security Reviewer**, **Approver**, and merge-capable agents.

## Roles (who does what)

| Actor | Low/medium green (docs, rules, runtime, routing, auth, tests, dependencies) | High (credentials, land scripts, branch protection, release/deploy, public MCP final-answer path) |
| --- | --- | --- |
| Contributor agents | Draft PR → undraft when Ready | Same; never invent land authority |
| Cursor Bugbot | Findings on head | Findings on head |
| Cursor Security Reviewer | One security pass | One security pass; block on issues |
| Cursor PR Approver | Approve when green + low/medium risk | **Block** for human/Codex (comment once) |
| Cursor merger (cloud) | **May merge** when Codex is busy/out of credits and protection allows | **Never** |
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
  → Approver: low/medium? approve : block for Codex
  → Land owner:
       low/medium + Codex busy or out of credits → Cursor merger (if protection allows)
       high → Codex (Approval + land)
  → Live main
```

## Branch protection plan (maintainer)

**Live today (observed):** required contexts include `build (3.11)`, `build (3.12)`,
`Project Site`, `Control Cloud Run Image`, `evals-offline`, `docker`, and
**`Codex Approval`**; strict updates; 1 approving review + CODEOWNERS
(`@djtelicloud`).

**Failover target:** replace the globally required `Codex Approval` context with
the risk-aware `Supervisor Approval` context. Low and medium packets can pass
that status after Cursor Bugbot, Security Reviewer, Approver, and required CI
are green. High-risk packets still require an exact-head `Codex Approval`.

### Applied policy

| Option | Change | Effect |
| --- | --- | --- |
The `Supervisor Approval` workflow is required for every PR. It grants the
authorized Cursor failover path to declared low/medium packets only when the
exact head has green required CI plus Bugbot, Security Reviewer, and Approver
evidence. It delegates no high-risk authority.

**Do not** remove all required reviews or disable CODEOWNERS.  
**Do not** grant Cursor merger high-risk authority. Medium is explicitly part of
the authorized outage failover path, with the stronger exact-head gates above.

### Workflow note

`.github/workflows/codex-approval.yml` remains owner-dispatched and binds the
exact head for high-risk work. `Supervisor Approval` re-evaluates on PR changes
and completed checks so a provider outage does not strand green low/medium work
and no approval survives a new commit.

## Operator checklist

1. Contributor: undraft, CI green, risk label/body, exact HEAD.  
2. Cursor: Bugbot → Security → Approver (one action per head).  
3. If low/medium and Codex is busy/out of credits: merger uses Supervisor Approval.
4. If high-risk or Approver blocked: Codex Approval + land.
5. Owner removes finished scratchpad after Live.

## Non-goals

- Replacing Codex for release/deploy  
- Multi-pass Automations thrash  
- Treating SuperGrok/simulation chats as land authority  
- Promoting non-Grok providers to public final MCP output  
