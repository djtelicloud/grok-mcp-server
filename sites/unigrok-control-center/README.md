# UniGrok Control Center Site Template

This directory is a reusable ChatGPT Site template for a UniGrok control center. It is deliberately separate from every deployed Site and contains no project ID, deployment ID, credential, secret, account allowlist, or personal account identifier.

Pull-request review state is not a deployment gate. A `changes_requested` review
affects that PR only. Repository adapters must supply a separate `releaseImpact`
value, and the Site may describe deployment as PR-blocked only when an approved
adapter explicitly returns `blocking` for a release-critical PR.

The template provides:

- dispatch-owned Sign in with ChatGPT for each viewer;
- an instructional connection wizard for local UniGrok and Secure MCP Tunnel;
- an idless Sites manifest that must be provisioned in each installer’s ChatGPT account;
- a responsive control-center interface with explicit runtime and credential boundaries;
- deterministic checks that reject copied Site identifiers and common secret formats.

It does not proxy localhost, store an xAI key, accept a tunnel credential, authenticate to GitHub, or claim that an unverified runtime is healthy.

## Security boundary

The root page calls `requireChatGPTUser("/")` on the server and is dynamically rendered for each request. Sites owns `/signin-with-chatgpt`, `/signout-with-chatgpt`, `/callback`, cookies, and identity-header injection. The application does not implement OAuth routes or a global login.

Sign in with ChatGPT establishes viewer identity. It does not choose the Site audience. Each installer must separately select the narrowest appropriate audience in Sites access settings.

For each signed-in request, the Sites dispatcher supplies the authenticated email and may supply an optional full name to the server. The server uses the email only to establish that the viewer is signed in. Only a normalized full name, or the neutral label `ChatGPT user` when no full name is available, reaches the client. The template retains neither value and sends neither value to UniGrok.

## Connection modes

### Local UniGrok

Local mode is for development on the same machine as UniGrok:

```dotenv
UNIGROK_CONNECTION_MODE=local
UNIGROK_LOCAL_BASE_URL=http://127.0.0.1:4765
```

Verify UniGrok from that machine:

```bash
curl -fsS http://127.0.0.1:4765/healthz
```

A deployed Site cannot reach the installer’s laptop through `localhost` or `127.0.0.1`. The wizard reports that boundary and never sends a browser request to the local service.

### Secure MCP Tunnel

Hosted deployments should use OpenAI’s Secure MCP Tunnel as the companion connection for ChatGPT:

```dotenv
UNIGROK_CONNECTION_MODE=tunnel
UNIGROK_TUNNEL_PROFILE=unigrok
```

The tunnel client runs beside UniGrok and makes an outbound connection. Configure and validate it in the same trust boundary as UniGrok, then select the Tunnel connection in ChatGPT Settings → Plugins. The Site stores only the non-secret profile label; tunnel credentials and account-scoped tunnel identifiers never enter this repository or the Site UI.

Follow the current [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## Local development

Requirements:

- Node.js `>=22.13.0`
- Git
- macOS or Linux for local development

From this directory:

```bash
cp .env.example .env
npm ci
npm run dev
```

The optional `npm run install:ci` command is a hardened Linux CI installer and
intentionally requires `flock`, `curl`, `sha256sum`, and GNU `timeout`. Ordinary
local setup uses the portable `npm ci` path above; `npm test` uses a bounded
cross-platform build runner when GNU `timeout` is unavailable.

The production root requires Sites-provided ChatGPT identity headers. Use the development-only `/preview` route for local visual testing. The route is unavailable in production builds.

Run the complete source-template gate before asking Sites to provision a project identity:

```bash
npm run lint
npm run check:template
npm run typecheck
npm test
```

`npm test` includes the strict `check:template` gate and therefore requires the repository manifest to remain idless.

## Deploy a separate Site in your own ChatGPT account

Do not attach this directory to an existing Site and do not copy a deployed `.openai/hosting.json` into it.

1. Clone or fork the repository and open it in ChatGPT Work while signed into the account and workspace that should own the new Site.
2. Ask Sites to create a separate checkout with this exact request:

   > @Sites Create a new Site named UniGrok Control Center in my current ChatGPT account using `sites/unigrok-control-center` as the source template. Create a separate Sites checkout, generate a new project identity for that Site, preserve the idless template in this repository, and stop after provisioning. Do not save a version or deploy it.

3. Confirm that the new Site’s generated project identity exists only in its separate Sites checkout.
4. In that derived checkout, run `npm run lint` and `npm run test:deployment`. The deployment gate requires a valid installer-owned project identity while applying the same secret and personal-identifier checks.
5. Configure the copied Site’s local `.env` values and hosted environment values in that Site’s settings. Never put installer-specific values in the open-source template or a prompt.
6. Preview the new Site and ask Codex to review the complete diff, identity boundary, environment contract, and deployment safety output.
7. Choose the narrowest Site audience. Audience controls and Sign in with ChatGPT are separate security layers.
8. After Codex reports a clean review, ask Sites to save a version without deploying it and inspect the saved version.
9. Approve a production deployment only after the saved version and audience are confirmed.

Every Sites deployment URL is production. See the current [ChatGPT Sites documentation](https://learn.chatgpt.com/docs/sites) and [Sites management guide](https://help.openai.com/en/articles/20001339-creating-and-managing-chatgpt-sites).

## Environment contract

| Variable | Purpose | Repository-safe value |
| --- | --- | --- |
| `GITHUB_REPOSITORY` | Optional public repository label and link | `example-org/grok-mcp-server` |
| `UNIGROK_CONNECTION_MODE` | Wizard state: `unconfigured`, `local`, or `tunnel` | `unconfigured` |
| `UNIGROK_LOCAL_BASE_URL` | Local-development metadata only | `http://127.0.0.1:4765` |
| `UNIGROK_TUNNEL_PROFILE` | Non-secret tunnel profile label | `unigrok` |

No variable uses a `NEXT_PUBLIC_` prefix. No credential variable is defined because this template does not need or accept a credential.

## Template identity

`.openai/hosting.json` contains only:

```json
{
  "d1": null,
  "r2": null
}
```

The missing `project_id` is intentional. Sites creates a new identity in the installer’s separate checkout. `npm run check:template` fails if a project identity or known private deployment marker is copied back into this directory.

After Sites provisions a separate installer-owned checkout, use `npm run check:deployment` or the complete `npm run test:deployment` gate there. Those commands require a valid generated `project_id`; they are not source-template checks.

## Review contract

Before a source-template commit or pull request:

1. inspect `git diff -- sites/unigrok-control-center`;
2. run `npm run lint`;
3. run `npm run check:template`;
4. run `npm run typecheck`;
5. run `npm test`;
6. request Codex review;
7. resolve all high- and medium-severity findings;
8. repeat the checks and review.

Before saving or deploying a separately provisioned Site, run `npm run lint` and `npm run test:deployment`, request another Codex review, resolve all high- and medium-severity findings, and repeat the deployment gate.

Do not publish the template or a derived Site merely because a build succeeds.
