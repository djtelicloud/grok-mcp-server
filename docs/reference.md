# UniGrok 1.1 technical reference

The README is the public quick start. This page contains the complete tool surface and
runtime contract for agents, integrators, and advanced users.
Known limits of the current release are tracked in [Known limits](known-limits.md).

## How an IDE agent should drive `agent`

Unless a paragraph is explicitly marked hosted, persistence and CLI-first routing below
describe local Compose. The hosted pilot is API-only and its SQLite state is
instance-local.

- **Default:** call `agent` with just `task`. The router picks route, effort, and
  recovery; hard reasoning auto-engages the deep harness; a typo fix never pays for a
  swarm.
- **Long autonomy:** set `UNIGROK_AUTONOMY=true`. Unfinished agent quanta may return
  `status=continue` with `continue_token` (re-invoke `agent`); `agent_result` still
  works while running. File/media/chat jobs use the `pending` poll contract and never
  return `continue`. Optional
  `acceptance` freezes CommitDone criteria; thin answers become `needs_continuation`,
  not success. Process default is **off**; **Docker compose live default is on**.
- **Mission controller v2:** set `UNIGROK_MISSION_V2=true` (requires autonomy). Adds
  durable `verifying` CommitDone, fenced leases, sealed artifact hashes with redacted
  projections, shadow governor/council receipts, and A0′/A0 task-class literal
  CommitDone (`UNIGROK_TASK_CLASS` / `UNIGROK_VERIFY_LITERAL`). Candidate text never
  counts as acceptance evidence. Verification mode is frozen with the mission:
  ordinary answer generation uses structural checks, while requests to run/prove an
  outcome require independent evidence. Process default is **off**; **Docker compose
  live default is on**. Inspect `/runtimez` → `autonomy`.
- **Context pack (named sessions):** set `UNIGROK_CONTEXT_PACK=cpu` (or `hive`, which
  currently maps to `cpu`). After each completed turn the gateway inventories history,
  runs five heuristic persona votes, lead-merges keeps/don’ts, then seals one
  **prefrontal** working-buffer sentence (≤2 hive loops) and an optional untrusted
  **`pfc_absent`** foresight sibling. Next turn injects pack → raw tail → prefrontal →
  `pfc_absent` → current request instead of the full transcript dump. Receipts land on
  `context_pack` (`prefrontal`, `pfc_loops`, `pfc_absent`, …). Process default is
  **off**; **Docker compose live default is `cpu`**. Inspect `/runtimez` → `autonomy.context_pack`.
- **De-overfitting doctrine:** freeze only near-physics envelopes; treat cognition
  weights, timeouts, and pools as versioned posteriors. See [DEOVERFIT.md](DEOVERFIT.md).
- **WASM × dogfood (design only):** sandbox agents that **run** code, not ones that
  **think**. No wasm runtime in the shipping gateway; today’s untrusted local exec is
  the host dogfood script. Guest ABI and trigger conditions:
  [WASM_DOGFOOD.md](WASM_DOGFOOD.md).
- **`level`** (optional, explicit rung): `none` · `minimal` · `low` · `medium` ·
  `high` · `xhigh` (one call at that native Grok effort) → `max` (silent deep harness)
  → `ultra` (parallel hive: draft → persona votes → merge). Setting `level` skips
  auto-routing. Use `ultra` when you want an artifact adversarially reviewed and are
  fine spending a few cents of API voters; on hard tasks it is typically 2–5× faster
  than a single high-effort call. `voters` (int) overrides the hive reviewer count.
- **`depth`** (optional, compatibility): `auto` (default) · `deep` (silent
  deep-reasoning harness) · `hive` (draft → persona votes → merge). Prefer `level` —
  `max` and `ultra` engage the same harnesses; `depth` remains for existing callers.
- **Receipts:** every result reports `resolved_plane`, `cost_usd`, `orchestration.route`,
  `fallback_reason`, `resolved_depth`; hive adds `hive.stages`, optional cleanup adds
  `final_polish`, and rejected or failed billed responses appear in `incurred_attempts`.
  Mission V2 adds cumulative, restart-durable `mission_billing`. Relay cost/plane to the
  user; do not hide metered spend.
- **Media:** ask `agent` in natural language ("generate an image of …"); it routes to the
  image/video specialist on the API plane and returns an `images` list of `{url}`. Without
  an API key the gateway returns an honest "needs `XAI_API_KEY`" message — it never
  fabricates a media link.
- **Verify:** after checking an answer, call `record_benchmark_result` with the
  `telemetry_id` so the run counts as verified.

## Handling the response

Every `agent` and metered-tool result is a **status envelope** — branch on `status`
before reading `text`. On any non-`complete` result, `text` is a progress/status
message, not the answer.

| `status` | Meaning | What the caller must do |
|----------|---------|-------------------------|
| `complete` | Terminal answer. With autonomy on, also `committed: true`, `gaps: []`. | Use `text`. Optionally `record_benchmark_result(telemetry_id)`. Do not re-invoke. |
| `pending` | Work outran its initial sync window; the provider job keeps running server-side, detached. | Poll `agent_result(job_id)` with the **same** `job_id` (use the `poll` hint). Never start a duplicate tool call. |
| `continue` | Autonomy only. Either the same quantum is still running (`poll` / `awaiting_inflight`) or Mission V2 sealed a rejected draft with named `gaps`. | If still running, poll or retry the same token later. For gaps, re-invoke `agent(continue_token=...)`; the gateway generates the repair quantum. Do not send a new task. |
| `error` | Terminal failure; `text` carries the redacted reason. | Do not retry. If `stop_reason: "FailDone"` / `terminal_reason: "unrepairable_gaps"`, the gap is unrepairable — never reattach. |
| `lost` | A non-Mission durable worker stopped before a terminal result was recorded (`stop_reason: "Interrupted"`). | The provider-side outcome is unknown. Inspect provider state before retrying a metered or state-changing operation. |

Which statuses a surface returns:

- **`agent`** with autonomy on returns `complete` / `continue` / `error`; its detached
  in-flight envelope is `continue` with `poll=true`. With autonomy off it may return
  `pending`. Mission V2 restart recovery returns `continue`, never `lost`.
- **Metered/durable tools** (`web_search`, `x_search`, `remote_code_execution`,
  `chat_with_vision`, `chat_with_files`, `generate_image`, `generate_video`,
  `extend_video`, and the `xai_*` file ops including `xai_delete_file`) return
  `complete` / `pending` / `error` only — they **never** return `continue`; you see
  `lost` only by polling their `job_id` after an interruption.

Terminal Mission V2 reattach is read-only: the same token returns canonical durable
truth without running another model quantum.

**Do not:**

- Re-issue a metered tool on `pending` — the first job is still running detached, so a
  second call bills the xAI API twice. Poll `agent_result(job_id)` instead.
- Re-fire `agent` on `continue` without `continue_token` — a bare call mints a new job,
  a fresh `acceptance_hash`, and new routing work (with possible API spend), discarding
  the ledger.
- Treat `committed: false` (or any `pending`/`continue`) as done — only `status:
  complete` is finished. Read `gaps`; never surface a `proposed_text` as final.
- Spin blindly on `continue` — read the named `gaps`, reattach serially with the same
  token, bound caller retries, and stop on terminal `error` / `FailDone`.
- Fire `continue_token` in parallel — reattach serially; on `awaiting_inflight` /
  `claim_blocked`, wait briefly and retry the same token.
- Hardcode model, plane, or effort — the tool exposes no such knobs. Shape only with
  `depth` / `level` / `voters` / `acceptance` / `disable_tools`.

**Do:**

- Relay `cost_usd` and `resolved_plane` (plus `hive.stages` when present) to the user.
- Pass a stable `session` across related calls for continuity (redacted turns persist
  in local SQLite; it also seeds `memory_scope`).
- Set `acceptance` on long tasks to give the verifier distinctive terms — richer gap
  enumeration and fewer premature completions.
- For an outcome-sensitive task, attach a real test/log/review observation through
  `caller_evidence`; do not claim that the candidate answer itself proves the outcome.
- Note `xai_delete_file` takes `confirm_delete=true` and carries no `continue_token`,
  but a slow delete can still return `pending` — poll `agent_result(job_id)`, do not
  re-issue the delete.

## Verification and caller evidence

Mission V2 freezes `structural` or `independent_evidence` verification when the mission
is created. A normal explanation, plan, or generated artifact can commit from structural
checks. A task that says to run, test, verify, or prove an external outcome requires an
independent evidence class.

The optional `caller_evidence` input is available only when autonomy and Mission V2 are
enabled:

```json
{
  "caller_evidence": [
    {
      "reference": "test-run:concurrency-42",
      "observation": "The independent concurrency suite completed successfully."
    }
  ]
}
```

`reference` is 1–2048 characters and `observation` is 1–20,000 characters. The server
redacts, classifies, hashes, and durably fences each record before candidate generation.
It treats the observation as quoted, untrusted data: UniGrok does not independently
validate that the caller told the truth. Candidate digests and candidate-projection
artifact references are rejected as self-evidence.

## MCP endpoint

```text
http://localhost:4765/mcp
```

Transport: Streamable HTTP. The MCP handshake, `/healthz`, `/readyz`, `/runtimez`,
`grok_mcp_status`, and `grok_mcp_discover_self` all report version `1.1.0`.
`/healthz`, `/runtimez`, and discovery also expose a non-secret
`source_fingerprint`, which identifies the byte content and relative paths of the baked
public runtime tree. For local Docker, compare it with the checkout using
`uv run python scripts/check_runtime_parity.py --container unigrok`.

The owner-operated hosted pilot uses `https://mcp.grokmcp.org/mcp`. It publishes
OAuth protected-resource metadata and requires an active, correctly scoped Control
token for every protected request. MCP initialization needs `unigrok:connect`; general
tools need `unigrok:invoke`; review and status use their dedicated scopes. The
`unigrok:chat` scope is reserved; this core does not expose a `/v1` chat route.
An OAuth-capable client should discover and authorize from the resource URL instead of
storing a provider key or static bearer.

Hosted mode is API-only: the Grok Build CLI is disabled by policy. OAuth principals
receive tenant-scoped sessions, facts, jobs, and telemetry. xAI file tools additionally
require a principal-bound provider credential. The current hosted SQLite directory is
instance-local, so the local-volume restart guarantees below do not extend across Cloud
Run instance replacement or horizontal scaling. See
[Authenticated remote deployment](remote-mcp-deployment.md).

## Public tools

### Main harness and discovery

- `agent` — one-task automatic Grok harness with web research enabled by default,
  optional sessions, scoped knowledge, workspace-context courier, `continue_token`
  reattach, and acceptance-hash CommitDone
- `agent_result` — poll an in-flight quantum without exceeding short IDE deadlines
- `review_pull_request` — review a bounded caller-supplied diff without GitHub or Git access
- `chat` — one stateless, tool-free answer
- `grok_mcp_discover_self` — authoritative live self-description
- `grok_mcp_onboard_client` — consent-first client integration plan; never writes files
- `grok_mcp_status` — non-secret runtime and plane readiness
- `list_models` — independent live CLI and API model catalogs
- `benchmark_status` — route, latency, cost, caller, fallback, and breaker aggregates
- `record_benchmark_result` — attach an explicit pass/fail outcome to a telemetry receipt

### Sessions and knowledge

- `list_sessions`, `session_history`, `forget_session`
- `remember_fact`, `search_knowledge`, `forget_fact`

### xAI API capabilities

- `web_search`, `x_search`, `remote_code_execution`
- `chat_with_vision`, `chat_with_files`
- `generate_image`, `generate_video`, `extend_video`
- `xai_upload_file`, `xai_list_files`, `xai_get_file`
- `xai_get_file_content`, `xai_delete_file`

The complete 29-tool surface remains visible when the API is not configured. API tools
then return a clear setup error instead of disappearing from client discovery.

## PR reviews (`review_pull_request`)

IDE callers use the `review_pull_request` MCP tool: it reviews a bounded
caller-supplied diff without GitHub, Git, or workspace access. Tools are forced
off for the review turn. When the underlying `agent` job is still running, poll
`agent_result` — review metadata (`review_kind`, repository, pull number, title,
`read_only`, and `review` text) is preserved across the poll.

Optional GitHub comment automation (`@grok review`) is provided by the in-tree
`.github/workflows/grok-review.yml`; it is not required to use the MCP tool. The
workflow gates comments to trusted repository roles, checks out default-branch code,
and sends a bounded diff for read-only review. Never execute, install, or import code
from the reviewed PR. Hosted URL, runner, and short-lived auth must be configured by
the repository owner; this local Compose service is not a public review endpoint.

## Routing and billing

The CLI-first policy below is the local Compose policy. Hosted mode reports `api_only`
through discovery and never attempts the disabled CLI plane.

- Callers supply task intent rather than model, plane, effort, or fallback controls.
- Clear tasks route heuristically. Other tasks use three structured CLI-first intent
  votes; their actual planes and costs are receipted. If fewer than two votes parse and
  API is ready, a semantic API fallback runs with a 256-output-token default cap
  (configurable 64–1024). Direct work remains subscription-first; selected specialists
  use the API.
- One bounded cross-plane recovery is permitted after a classified provider failure.
- Known spend and token counts survive non-answer rejection, cross-plane fallback,
  router/hive/polish failure, and post-provider state errors. `incurred_attempts` contains
  bounded metadata only; Mission V2 cumulatively checkpoints it by lease generation so
  restart polls and later quanta cannot erase or double-add a charge.
- `UNIGROK_ENABLE_METERED_API=false` disables metered API execution globally.
- API responses include provider billing receipts when available.
- Destructive tools require `confirm_delete=true`.

Language model IDs are discovered independently from the live CLI and API catalogs.
The caller does not select them. Media tools retain provider-defined media defaults.

## Runtime limits

Environment values outside their range are clamped. `grok_mcp_discover_self` and
`/runtimez` report effective values for the running container.

| Control | Default | Allowed range |
|---|---:|---:|
| `UNIGROK_BUILD_TIMEOUT` | 120 s | 30–600 s |
| `UNIGROK_API_TIMEOUT` | 120 s | 30–600 s |
| `UNIGROK_FILE_LIST_TIMEOUT` | 120 s | 60–600 s |
| `UNIGROK_FILE_IO_TIMEOUT` | 60 s | 30–600 s |
| `UNIGROK_MEDIA_TIMEOUT` | 300 s | 60–600 s |
| `UNIGROK_AGENT_SYNC_WINDOW` | 16 s | 1–60 s |
| `UNIGROK_AGENT_MAX_TURNS` | 6 | 1–24 |
| `UNIGROK_MISSION_LEASE_TTL` | 180 s | 30–900 s |
| `UNIGROK_ROUTER_MAX_OUTPUT_TOKENS` | 256 | 64–1024 |
| `UNIGROK_VOTE_MAX_OUTPUT` | 128 | 48–512 |
| `UNIGROK_MAX_PROMPT_CHARS` | 100,000 | 1,024–500,000 |
| `UNIGROK_MAX_WORKSPACE_CONTEXT_CHARS` | 100,000 | 1,024–500,000 |
| `UNIGROK_FILE_CONTENT_MAX_BYTES` | 2,000,000 | 1,024–10,000,000 |
| `UNIGROK_API_MAX_INFLIGHT` | 4 | 1–16 |
| `UNIGROK_API_MAX_FILE_INFLIGHT` | 2 | 1–4 |
| `UNIGROK_BREAKER_FAILURES` | 3 | 2–20 |
| `UNIGROK_BREAKER_COOLDOWN` | 30 s | 5–600 s |
| `UNIGROK_CATALOG_TTL` | 60 s | 5–600 s |
| `UNIGROK_STATE_RETENTION_HOURS` | 24 h | 1–720 h |

`agent_result(wait_seconds)` is a separate per-poll argument: default 16 seconds,
clamped to 1–20. `xai_get_file_content(max_bytes)` defaults to 500,000 and clamps to
1,024–1,000,000 bytes, then remains bounded by the environment hard cap above.

Circuit breakers are isolated by plane, credential generation, and model. `open=true`
with a positive retry time means calls fail fast during cooldown; `open=true` with
`retry_after_seconds=0` means the breaker is probe-ready, not closed. Exactly one
half-open probe is admitted. Cancellation does not count as provider failure, but a
replacement probe remains fenced through the longest configured provider deadline so a
cancelled worker cannot overlap its replacement. Local policy or unavailable-capability
decisions do not poison provider health. Inspect `/benchmarkz` or `/runtimez` before
restarting merely to clear a breaker, because a restart can interrupt durable work.

## Local Compose team continuity

Named `agent` sessions persist redacted conversation turns in local SQLite. Deliberately
remembered facts use caller-controlled scopes and can be searched or deleted explicitly.
The `unigrok-public-state` Docker volume survives service restarts.

Durable structured payloads are recursively secret-redacted. Mission answer projections
are capped at 100 KB, while the raw candidate is returned only to the winning live call.
Named-session turns and their context packs are CommitDone-gated and keyed by job, so a
rejected draft or repeated terminal reattach cannot add duplicate history. Terminal
mission/job/compatibility rows default to 24-hour retention; sessions and remembered
facts persist until the caller deletes them.

When `UNIGROK_CONTEXT_PACK` is enabled, named sessions also persist a server-pruned
context pack (keeps/don’ts + prefrontal + optional `pfc_absent`) for the next turn.
Pack text is untrusted evidence framing; don’ts remain authoritative over foresight.

An IDE may send selected text through `workspace_context` with an optional
`workspace_label`. This is a bounded, redacted courier—not workspace attachment. It
grants no file, shell, Git, credential, or external MCP authority.

## Project onboarding contract

Global client onboarding is the recommended first choice. Call
`grok_mcp_onboard_client` to receive a consent-first plan for Antigravity, Codex,
Claude Code, Cursor, GitHub Copilot/VS Code, or a generic MCP client. If the client
advertises MCP elicitation, the tool can ask for `global`, `project`, `not_now`, or
`never`; otherwise it returns the same offer for the calling agent to present.

The MCP service never performs the installation. Plans contain only UniGrok-owned,
checksummed files or a client-settings instruction. The calling IDE must preview
conflicts, preserve modified files, and write only after explicit approval. Project
customizations remain higher priority than the global baseline.

Antigravity's global pack uses `~/.gemini/config/plugins/unigrok` plus the global
`ask-grok` workflow. Its generated `~/.gemini/antigravity/mcp` cache is never an install
target.

The `agent` tool accepts canonical `task` and the compatibility alias `prompt`. Web
research, X search, and cloud code execution are available by default. The returned
`agent_tools` receipt tells the calling agent which tools were effective and requires a
concise user notice. Users can disable individual tools with `disable_tools`.

Receipts expose `telemetry_id`, model, route, plane, cost, latency, fallback occurrence,
and a precise `fallback_reason`. Benchmark runners can attach verified outcomes with
`record_benchmark_result`; telemetry never stores prompt text.

The MCP initialization instructions and self-discovery describe these canonical paths:

- `AGENTS.md` — repository-wide agent instructions
- `.agents/rules/<rule-name>.md` — Antigravity workspace rules
- `.agents/workflows/<workflow-name>.md` — Antigravity workflows
- `.agents/skills/<skill-name>/SKILL.md` — Agent Skills packages

`.agent/rules` is legacy and should not be created for new projects. Client-native
adapters such as `.cursor/rules`, `.cursor/commands`, `CLAUDE.md`, or `GEMINI.md` should
only be created when that client is actually present.

UniGrok does not write project files. The calling IDE must inspect existing guidance,
preserve it, and use its own workspace permissions to create only useful missing files.

## Public boundary

The service exposes no caller project, local filesystem, Git repository, host shell,
IDE configuration, plugins, private intelligence, subordinate providers, or their
credentials. Its session database contains only caller-supplied MCP content after
redaction.

Needle remains visibly inactive. Design and evaluation artifacts do not constitute a
live shadow/reflex runtime. WASM guest isolation for dogfood/local code-exec is
likewise design-only until a sandboxed promotion oracle or local RCE path exists
([WASM_DOGFOOD.md](WASM_DOGFOOD.md)).
