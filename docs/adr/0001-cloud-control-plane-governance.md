# ADR 0001: Public Project, Protected Control Plane, and Git Governance

- **Status:** Accepted target architecture; migration is not complete
- **Date:** 2026-07-11
- **Decision owner:** Project maintainer, with Codex as Git integration authority

## Context

UniGrok currently has a real local MCP gateway and Control Center, a public
website prototype, and a self-hosted GitHub review workflow. The local service
is useful while the maintainer's Mac is online, but it cannot be the
availability or trust anchor for a public open-source project. At the same
time, publishing the local administration surface or the Grok CLI OAuth session
would collapse boundaries between public documentation, contributor access,
provider credentials, and repository mutation.

The project needs one public identity and several deliberately separate trust
zones. GitHub must identify project contributors, Grok may produce advisory
review evidence, Codex must decide whether a change is acceptable, and only a
small deterministic component may turn an approved decision into a GitHub
mutation.

## Decision

### 1. Separate the public, contributor, inference, and local planes

| Surface | Audience | Authentication | Permitted data/actions |
|---|---|---|---|
| `https://grokmcp.org/` | Everyone | None | Product explanation, docs, onboarding, releases, public project status |
| `https://grokmcp.org/control` | Repository contributors and admins | GitHub sign-in plus repository authorization | Contributor dashboard, PR/review/deployment evidence, requests for approved operations |
| `https://mcp.grokmcp.org/mcp` | Approved MCP clients | OAuth 2.1 or an equivalent short-lived scoped credential | API-plane tools allowed by the granted scope |
| Local `http://localhost:4765/mcp` and `/ui/` | Trusted machine users | Loopback boundary plus optional UniGrok client token | Local Control Center, API plane, and the machine's authenticated CLI plane |
| GitHub controller/broker | GitHub App installation | Short-lived installation token | Only the allowlisted repository operations described below |

The public site is useful without an account. Public repository visibility or
a successful GitHub OAuth login is **not** contributor authorization. Access to
the protected control surface requires a current GitHub relationship to
`djtelicloud/grok-mcp-server` (collaborator, selected team, or explicit admin
policy) verified server-side. ChatGPT sign-in may personalize a ChatGPT
experience but does not grant project permissions.

Authorization is checked at the server boundary for every privileged request;
hiding a client-side route is not a security control. Authorization caches must
be short-lived and revocable. Admin-only operations remain a narrower role than
contributor access.

### 2. Keep cloud and local Grok credential planes distinct

The always-on service is API-plane-only and uses a server-side `XAI_API_KEY`.
The Grok CLI OAuth/OIDC session remains on the trusted local machine and is
never copied into Sites, a hosted runner, a cloud image, a repository secret,
or a contributor browser. The cloud service must not claim CLI-plane readiness
or silently cross into a personal subscription credential plane.

Cloud callers request `plane=api` with same-plane failure behavior when that
billing boundary matters. Local callers may use the existing dual-plane router.
Provider credentials never enter an IDE MCP configuration or browser bundle.

### 3. Publish project knowledge, not an anonymous inference gateway

Anonymous callers receive static or bounded public project information through
the website, documentation, `/llms.txt`, the project manifest, and a sanitized
public project/status API. The first production release does **not** expose a
no-auth model-backed MCP endpoint.

A future anonymous MCP may expose cached, public, read-only knowledge only if it
runs in an isolated service with no provider credentials, repository mutation,
private state, arbitrary outbound network, or access to the authenticated MCP.
Rate limits and abuse controls are mandatory. It must never become a proxy to
the real Grok inference service.

### 4. Make GitHub the contribution system of record

Contributions arrive through pull requests. Humans and IDE agents provide the
same minimum handoff evidence:

1. repository and pull request (or agent task branch/commit SHA before the PR
   exists);
2. exact head commit SHA;
3. changed paths and intent;
4. verification commands and results;
5. known risks, generated files, and credential-sensitive changes;
6. any advisory Grok review tied to that exact head SHA.

`CODEOWNERS`, branch protection, required CI, and current-head review checks are
enforcement inputs. Grok output, contributor comments, diffs, issue text, and
agent handoffs are untrusted evidence, not executable instructions.

Codex remains the decision authority for landing, merge, tag, and release. A
Grok approval is advisory and cannot satisfy a Codex approval gate. A future
GitHub controller records an explicit structured Codex disposition such as:

```json
{
  "repository": "djtelicloud/grok-mcp-server",
  "pull_number": 123,
  "head_sha": "<40-character commit>",
  "decision": "approve",
  "required_checks": ["<checks resolved from the protected branch ruleset>"],
  "expires_at": "<short-lived timestamp>"
}
```

The disposition is invalid if the PR head changes.

### 5. Separate decision from mutation

Codex does not hold or expose a permanent merge token. The target mutation path
is a narrowly scoped GitHub App broker using short-lived installation tokens.
It may mutate GitHub only after validating all of the following against fresh
GitHub state:

- the structured Codex disposition exists, is unexpired, and names the current
  head SHA;
- required CI and code-owner review gates pass for that SHA;
- the branch is mergeable under repository policy;
- the requested operation is in the broker's allowlist;
- the actor and source event are present in the append-only audit record.

The broker must not execute contributor code, accept shell commands from model
output, weaken branch protection, create arbitrary secrets, or reuse a token
outside its one operation. GitHub Actions jobs that read PR evidence continue
to run trusted default-branch code and never check out PR code under
`pull_request_target`.

GitHub's CODEOWNERS syntax cannot name Codex as an abstract decision service.
During the transition, `@djtelicloud` is the CODEOWNER because it is the
connected project-admin identity through which Codex operates. This is a
platform mapping, not a requirement for the human owner to review Git commands
or babysit merges. After the GitHub App broker exists, Codex/project-admin
automation may move protected-path ownership to a dedicated bot team while
preserving a human break-glass identity.

### 6. Bind review evidence and landing receipts to immutable commits

The GitHub Grok review fetcher binds each comment to the PR head and base SHA
and a SHA-256 digest of the bounded evidence it sent for review. It refuses to
publish if the event SHA is already stale or if the head changes while Grok is
working. That proves which evidence was reviewed; it does not prove the review
was correct and does not authorize a merge.

The hosted receipt broker issues a signed receipt containing at least:

- repository identity, pull request number, base SHA, and landed head SHA;
- required-check conclusions and code-owner/Codex disposition identifiers;
- merge commit SHA and resulting `origin/main` SHA;
- actor, timestamp, broker version, and policy version;
- an asymmetric signature plus key id suitable for offline verification.

Receipt signing keys live in versioned Secret Manager resources, not the
repository, browser, or an agent prompt. The broker verifies the configured
Ed25519 public/private pair and publishes only its public JWK at
`https://control.grokmcp.org/.well-known/unigrok-receipt-keys`. Verification
fails closed for an unknown key, altered payload, stale head, missing exact-head
Codex approval, a failed check, or a merge commit outside the current default
branch ancestry. Cloud merge and release mutations remain disabled.

## Current state versus target state

This ADR intentionally does not pretend the migration already happened.

| Capability | Current implementation | Target before claiming it live |
|---|---|---|
| Private remote MCP | API-plane-only Streamable HTTP resource with RFC 9728 metadata, OAuth PKCE, short-lived scoped tokens, live introspection, and structured audit events | Live; anonymous inference remains prohibited |
| GitHub Grok review | Explicit hosted broker fetches an immutable base/head diff, rechecks it for races, and calls only the scoped API-plane `review_pull_request` tool | Live; advisory only and never automatic |
| Landing receipt | Local `scripts/land` JSON plus an admin-only Ed25519 receipt broker and published JWK | Live verification path; cloud merge and release remain disabled |
| Canonical integration branch | Protected `origin/main` through PRs; Codex synchronizes local `main` and records the local landing receipt | Unchanged until a separately reviewed cloud mutation broker exists |
| GitHub authorization | GitHub App OAuth identity plus a fresh repository-role check on every protected request and token introspection | Live |
| Codex approval | Owner-dispatched required status validates the exact open PR head without model usage | Dedicated project-admin App identity with signed dispositions |
| GitHub mutation broker | Deliberately disabled; GitHub protected merge remains the mutation boundary | Any future mutation path requires a separate reviewed change and signed receipt reconciliation |

The repository's `AGENTS.md` contract remains operative: every change reaches
`origin/main` through a PR, Codex records an exact-head disposition, only a
`codex/*` integration branch may run `scripts/land`, and local `main` is
synchronized to the protected merge. The hosted receipt service verifies
completed landings; it does not authorize or perform a merge. The connected
maintainer identity remains the documented project-admin mapping for
owner-authored agent PRs.

## Required GitHub repository policy

Codex/project-admin automation owns configuring and continuously verifying the
`main` ruleset. The user is not expected to perform routine Git or repository
settings work manually. The ruleset must:

- require pull requests and CODEOWNER review for protected paths;
- require the current CI checks and dismiss stale approvals on new commits;
- require conversation resolution and block force pushes/deletion;
- restrict bypass to documented emergency maintainers;
- prevent workflow files from receiving write credentials beyond their stated
  job permissions;
- pin third-party Actions to reviewed immutable commit SHAs before enabling
  automatic work for outside contributors;
- retain Actions and controller audit evidence for an appropriate period.

Repository settings are external state. Committing `CODEOWNERS` and workflows
does not prove that a ruleset is enabled; Codex must verify the connected
GitHub state, and the protected control surface must show configuration state
as unknown until GitHub confirms it.

## Rollout gates

1. **Foundation:** public/protected route split, GitHub authorization contract,
   CODEOWNERS, provenance-bound advisory reviews, and fail-closed remote MCP
   boundaries.
2. **Always-on review:** API-only cloud UniGrok, hosted workflow runner,
   short-lived client credential, and rate/cost limits. Any outside-contributor
   automatic trigger requires a separate reviewed change after abuse testing;
   no dormant opt-in switch ships in the local workflow.
3. **Read-only control:** GitHub App installation, repository-role checks,
   webhook verification/replay protection, live PR/CI/deployment views, and
   Codex-managed repository ruleset verification.
4. **Controlled mutation:** explicit Codex dispositions, deterministic broker,
   signed receipts, protected `origin/main`, and audited rollback procedure.
5. **Canonical switch:** after parallel-run reconciliation proves remote
   receipts and local `scripts/land` agree, declare protected `origin/main`
   canonical and update the operational agent contract in the same change.

## Consequences

- The project can remain available when the maintainer's Mac is offline without
  exporting the personal CLI credential plane.
- Public onboarding and machine-readable project knowledge stay frictionless.
- Contributors use familiar GitHub identity, while access can be revoked at the
  repository/team boundary.
- Review and mutation are more auditable but require a GitHub App, hosted
  secrets, rulesets, signature key management, and operational monitoring.
- Local landing receipts and hosted signed verification receipts have distinct
  schemas and producers; UI, audit logs, and docs identify which one produced a
  result.
