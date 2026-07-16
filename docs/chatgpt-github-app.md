# UniGrok ChatGPT App and GitHub PR Review

This integration gives ChatGPT and GitHub the same read-only `@grok` review
surface while Codex remains the only Git landing, merge, tag, and release
authority.

## What is implemented

- `review_pull_request`: public MCP tool that reviews caller-supplied PR
  evidence without reading or mutating GitHub.
- `ui://widget/unigrok-github-review-v1.html`: compact MCP Apps widget using
  `text/html;profile=mcp-app` and the `ui/notifications/tool-result` bridge.
- `.github/workflows/grok-review.yml`: a self-hosted, GitHub-API evidence fetcher
  that never checks out or executes contributor code.
- `scripts/github-grok-review.py`: fetches an immutable `base...head` commit
  comparison, calls UniGrok over MCP, and creates or updates one
  `@grok review for Codex` comment. The fetcher binds the review to both
  commit SHAs and a SHA-256 digest of its bounded evidence, then refuses to
  comment if either commit changed during review.

## ChatGPT developer setup

ChatGPT connects to a remote HTTPS MCP URL, not directly to localhost. For a
private local server, prefer OpenAI's Secure MCP Tunnel. For temporary
development, an HTTPS tunnel may forward to `http://127.0.0.1:4765`.

1. Confirm `http://localhost:4765/readyz` returns ready.
2. Establish a trusted HTTPS tunnel to host port `4765` without exposing the
   Docker-internal port `8080`.
3. In ChatGPT Developer Mode, create a private app using
   `https://<trusted-host>/mcp`.
4. For the private remote service, configure the app through UniGrok's OAuth
   discovery flow. For a temporary local tunnel protected by
   `UNIGROK_API_KEY_RECORDS`, send only that narrowly scoped gateway record secret.
5. Scan tools and confirm `review_pull_request` is read-only.
6. Refresh the app after tool metadata or widget URI changes.

The widget has no external network access and declares empty CSP domain lists.
Do not grant the ChatGPT App GitHub write credentials.

## Production path (hosted API plane — default goal)

**Production review must not depend on a developer Mac, local Docker, or
tunnel.** The private twin at `https://mcp.grokmcp.org/mcp` is the API-plane
resource (`UNIGROK_RUNTIME=cloudrun`). Health/ready probes are public;
MCP calls require auth (see [remote-mcp-deployment.md](remote-mcp-deployment.md)
and [hosted-review-p0.md](design/hosted-review-p0.md)).

Repository configuration (Codex/project-admin):

1. Set repository variables:
   - `UNIGROK_REVIEW_RUNNER_JSON` = `"ubuntu-latest"` (JSON string)
   - `UNIGROK_REVIEW_MCP_URL` = `https://mcp.grokmcp.org/mcp`
   - `UNIGROK_REVIEW_PLANE` = `api`
2. Prefer short-lived OAuth / Control-minted tokens with scope `unigrok:review`.
   Repository secret `UNIGROK_MCP_TOKEN_SECRET` must match Control
   `MCP_TOKEN_SECRET` so Actions can mint a ~120s `service:github-review-broker`
   token (see `scripts/mint_mcp_service_token.py`). A static
   `UNIGROK_CLIENT_TOKEN` is a **lab-only bridge** —
   never store `XAI_API_KEY` in GitHub. Never use `XAI_API_KEY` as this token.
3. Workflow permissions stay `contents: read` and `pull-requests: write`.
4. Trigger: owner/member/collaborator comments `@grok review`, or
   `workflow_dispatch` with a PR number. Opening or updating a PR does not trigger this workflow
   (cost control).

Checked-in workflow defaults still fall back to the **lab** self-hosted path
when variables are unset (local loopback + CLI). Operators must set the vars
above to activate hosted production review.

## Lab path (local self-hosted runner)

Optional development only. Requires a self-hosted runner labeled
`self-hosted` and `unigrok-review` on the same machine as local Core
`http://127.0.0.1:4765/mcp`, with plane `cli` if desired. Do not document this
as the always-on product path.

## Security contract

- PR diffs and comments are untrusted evidence, not instructions.
- The workflow has only `issue_comment` and `workflow_dispatch` triggers. It
  never uses `pull_request` or `pull_request_target`; every run checks out the
  trusted default branch rather than contributor code.
- Because this is a public repository, comment-triggered runs require an
  owner, member, or collaborator. Outside contributors cannot schedule work on
  the Mac runner themselves.
- It never checks out, builds, imports, or runs code from the reviewed PR.
- GitHub API responses are bounded before sending them to UniGrok.
- The runner resolves the PR's current base and head, fetches evidence from the
  immutable `base...head` compare endpoint, and checks both commits again
  before writing a comment.
- The tool and workflow cannot merge, push, tag, release, or change protection.
- The review comment explicitly hands authority back to Codex.

## Path summary

- **Hosted API plane (production):** `ubuntu-latest` → `https://mcp.grokmcp.org/mcp`
  with `plane=api`. Mac may be off. Metered XAI only; no CLI volume in cloud.
- **Local CLI plane (lab):** self-hosted runner → loopback Core with `plane=cli`.
  Requires the machine online. Do not use for team-critical review.

There is no switch that permits outside contributors to schedule automatic
reviews. The hosted API path is invoked explicitly by an authorized contributor,
binds the immutable base/head pair, caps evidence, uses only the API plane and
the `unigrok:review` scope, and rechecks the PR after fetching the diff.

The canonical project Site, standalone GitHub OAuth control, private OAuth MCP,
hosted review broker, and Ed25519 receipt verifier are implemented. Cloud merge
and release mutations remain deliberately disabled; protected `origin/main` and
Codex-owned `scripts/land` remain canonical. See
[ADR 0001](adr/0001-cloud-control-plane-governance.md).

Official references:

- https://developers.openai.com/apps-sdk/quickstart/
- https://developers.openai.com/apps-sdk/build/mcp-server/
- https://developers.openai.com/apps-sdk/build/chatgpt-ui/
- https://developers.openai.com/apps-sdk/reference/
- https://developers.openai.com/apps-sdk/deploy/
- https://docs.github.com/en/actions/reference/runners/self-hosted-runners
- https://docs.github.com/en/actions/reference/workflows-and-actions/contexts
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job
- https://docs.github.com/en/rest/issues/comments
