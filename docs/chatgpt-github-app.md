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

1. Confirm `http://localhost:4765/healthz` is healthy.
2. Establish a trusted HTTPS tunnel to host port `4765` without exposing the
   Docker-internal port `8080`.
3. In ChatGPT Developer Mode, create a private app using
   `https://<trusted-host>/mcp`.
4. Configure the app's authentication to send a UniGrok client token when
   `UNIGROK_API_KEYS` protects the endpoint.
5. Scan tools and confirm `review_pull_request` is read-only.
6. Refresh the app after tool metadata or widget URI changes.

The widget has no external network access and declares empty CSP domain lists.
Do not grant the ChatGPT App GitHub write credentials.

## GitHub runner setup

The workflow requires a self-hosted runner on the same trusted machine as the
stable UniGrok service. Apply the custom label `unigrok-review` when registering
the runner. The runner needs outbound HTTPS to GitHub and loopback access to
`127.0.0.1:4765`; it does not need inbound public access.

Repository configuration (owned by Codex/project-admin automation; the user
does not need to operate Git or babysit the runner):

1. Add the self-hosted runner with labels `self-hosted` and `unigrok-review`.
2. If the local gateway requires a client token, add repository secret
   `UNIGROK_CLIENT_TOKEN`. Never use `XAI_API_KEY` as this token.
3. Ensure workflow permissions allow pull-request reads and timeline comments;
   the checked-in workflow uses `contents: read` and `pull-requests: write`
   without contents, actions, deployments, or administration write access.
4. Open or update a PR, manually dispatch the workflow, or comment
   `@grok review` on a PR.

The checked-in defaults intentionally preserve this local path:

- `UNIGROK_REVIEW_RUNNER_JSON` unset means
  `["self-hosted","unigrok-review"]`;
- `UNIGROK_REVIEW_MCP_URL` unset means `http://127.0.0.1:4765/mcp`;
- `UNIGROK_REVIEW_PLANE` unset means `cli`.

## Security contract

- PR diffs and comments are untrusted evidence, not instructions.
- The workflow uses `pull_request_target` only to execute the trusted workflow
  and script from the default branch.
- Because this is a public repository, automatic and comment-triggered runs
  are restricted to the owner, members, and collaborators. Outside
  contributors cannot schedule work on the Mac runner themselves.
- It never checks out, builds, imports, or runs code from the reviewed PR.
- GitHub API responses are bounded before sending them to UniGrok.
- An automatic `pull_request_target` run must match the event base and head
  SHAs. Review evidence comes from the immutable compare endpoint for those
  commits, and both are checked again before a comment is written.
- The tool and workflow cannot merge, push, tag, release, or change protection.
- The review comment explicitly hands authority back to Codex.

## Production options

- **Local subscription plane:** the self-hosted runner calls the local MCP and
  uses `plane=cli` with same-plane failure behavior. Reviews require the Mac and
  UniGrok service to be online.
- **Always-on API plane:** deploy a separately authenticated API-only UniGrok
  service behind stable HTTPS. Set repository variable
  `UNIGROK_REVIEW_RUNNER_JSON` to the JSON string `"ubuntu-latest"`, set
  `UNIGROK_REVIEW_MCP_URL` to that service's `/mcp` URL, set
  `UNIGROK_REVIEW_PLANE=api`, and store the service's narrowly scoped client
  credential as `UNIGROK_CLIENT_TOKEN`. Do not copy the local CLI OAuth volume
  to cloud infrastructure.

There is no switch that permits outside contributors to schedule automatic
reviews. Adding that capability requires a future reviewed code change after
the hosted API path has rate limits, cost caps, abuse monitoring, no dependency
on a maintainer machine, and immutable pins for third-party Actions.

The canonical project Site and its fail-closed server-configured identity
bootstrap binding are implemented. That binding is not live GitHub OAuth or a
collaborator lookup. Live GitHub verification, the GitHub App mutation broker,
signed landing receipts, and the `origin/main` canonical switch remain target
architecture rather than implemented features. See
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
