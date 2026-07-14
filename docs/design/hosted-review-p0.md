# Hosted Grok PR review — P0 operator plan

- **Status:** Execution checklist (Wave following Console Health glass + docs policy)
- **Date:** 2026-07-14
- **Decision owner:** Human sponsor; Codex owns land/deploy gates
- **North star:** Production review never depends on a developer Mac, Docker, or tunnel.

## Live evidence (re-verify before each step)

| Probe | Expected (observed 2026-07-14) |
| --- | --- |
| `GET https://mcp.grokmcp.org/healthz` | `200` `{"status":"healthy"}` |
| `GET https://mcp.grokmcp.org/readyz` | `200` `status=ready`, `model_auth=true` |
| `POST https://mcp.grokmcp.org/mcp` (no token) | `401` + `WWW-Authenticate` resource metadata |
| `GET https://control.grokmcp.org/api/public/v1/project` | `200` project JSON |
| Local Mac powered off | Hosted review still works after P0 complete |

**Implication:** The API-plane Cloud Run twin is already up. P0 is **wire review to it**, not invent a new service.

## What P0 is / is not

| In scope | Out of scope |
| --- | --- |
| GitHub Actions → hosted MCP (API plane) | Tunnel to `127.0.0.1:4765` for production |
| Short-lived or scoped client auth for Actions | Putting `XAI_API_KEY` in GitHub Secrets |
| Cost caps + kill-switch vars | Auto-review every PR / multi-agent fan-out |
| Control broker smoke (server-side) | Control UI score invention |
| Docs: hosted path is production default | GenFunc-style in-container git hot-reload |

## Architecture (locked)

```text
@grok review  (OWNER/MEMBER/COLLABORATOR)
        │
        ▼
GitHub Actions  runs-on: ubuntu-latest
        │  immutable base...head evidence (scripts/github-grok-review.py)
        ▼
https://mcp.grokmcp.org/mcp   plane=api
  OAuth / scoped client token with unigrok:review
  XAI_API_KEY only in Cloud Run Secret Manager
        │
        ▼
Advisory PR comment → Codex lands (or not)
```

Control `POST /api/control/reviews` remains the insider broker (same twin).
Local self-hosted + CLI plane stays a **lab** path only.

## Operator steps (Codex / project admin)

### 1. Land remaining docs PR

- Draft **#109** (wiki not a product) rebased on post-#108 `main`.
- Exact head after rebase: verify with `gh pr view 109 --json headRefOid`.
- `./scripts/land` from a `codex/*` branch only.

### 2. Repository variables (hosted production)

Set on `djtelicloud/grok-mcp-server`:

| Variable | Value |
| --- | --- |
| `UNIGROK_REVIEW_RUNNER_JSON` | `"ubuntu-latest"` (JSON string) |
| `UNIGROK_REVIEW_MCP_URL` | `https://mcp.grokmcp.org/mcp` |
| `UNIGROK_REVIEW_PLANE` | `api` |

Optional kill-switch later: workflow `if` gated on `vars.UNIGROK_REVIEW_ENABLED != '0'`.

### 3. Client credential for Actions (temporary bridge)

Until OIDC → Control mint ships (P1):

- Prefer a **narrow** gateway client token accepted by the remote MCP OAuth/introspection design — **not** `XAI_API_KEY`.
- Store as repository secret `UNIGROK_CLIENT_TOKEN` only if the twin still accepts that bootstrap path.
- Production preference (docs): OAuth introspection; static client is break-glass / short rotation.

If the twin is pure OAuth and rejects static tokens, P0 uses **Control broker** from an authenticated insider session first, and Actions waits for P1 OIDC.

### 4. Cost controls (required before volume)

| Control | Suggested default |
| --- | --- |
| Trigger | Explicit `@grok review` or `workflow_dispatch` only (already true) |
| Plane | `api` only (no cross-plane) |
| Max reviews | Human discipline: 1–2 / PR / day until broker budgets land |
| Kill | Unset vars or set runner back to disabled |

Hard token budgets on the twin (`UNIGROK_CALLER_BUDGETS`) should be set in Cloud Run env when available.

### 5. Smoke tests

1. Laptop can sleep; use phone/other machine if needed.
2. On a draft PR: collaborator comments `@grok review` **or** dispatch workflow with PR number.
3. Expect one advisory comment bound to base/head SHA.
4. Confirm no merge/land action by the workflow.
5. Control UI may still show Grok review “Not connected” until UI snapshot work (P1) — **PR comment is the success metric for P0**.

### 6. Fail-closed checks

- Missing token → no comment, red job (acceptable).
- Twin 5xx → no invented score on Control.
- Outside contributor `@grok review` → workflow does not run (association gate).

## P1 (next, not this PR)

1. GitHub OIDC → Control short-lived `unigrok:review` mint (retire static client).
2. Un-hardcode Control `grokReview` snapshot from real broker/probe state.
3. Per-subject budgets + spend log in Control.
4. Optional: workflow default in-repo once vars proven (still var-driven).

## Explicit NO-GOs

- Production tunnel to local Docker / self-hosted Mac runner as the only path.
- CLI plane on Cloud Run.
- `XAI_API_KEY` in GitHub Actions secrets.
- Needle live generation until hosted review + budgets proven.
- GitHub Wiki as second docs tree.
- Grok auto-land of `main`.

## Related

- [remote-mcp-deployment.md](../remote-mcp-deployment.md)
- [chatgpt-github-app.md](../chatgpt-github-app.md)
- [ADR 0001](../adr/0001-cloud-control-plane-governance.md)
- [public-vs-insider-surfaces.md](public-vs-insider-surfaces.md)
