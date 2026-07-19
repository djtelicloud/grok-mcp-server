# UniGrok 1.1 technical reference

The README is the public quick start. This page contains the complete tool surface and
runtime contract for agents, integrators, and advanced users.
Known limits of the current release are tracked in [Known limits](known-limits.md).

## How an IDE agent should drive `agent`

- **Default:** call `agent` with just `task`. The router picks route, effort, and
  recovery; hard reasoning auto-engages the deep harness; a typo fix never pays for a
  swarm.
- **Long autonomy:** set `UNIGROK_AUTONOMY=true`. Unfinished agent quanta may return
  `status=continue` with `continue_token` (re-invoke `agent`); `agent_result` still
  works while running. File/media/chat jobs always stay `pending`. Optional
  `acceptance` freezes CommitDone criteria; thin answers become `needs_continuation`,
  not success. Process default is **off**; **Docker compose live default is on**.
- **Mission controller v2:** set `UNIGROK_MISSION_V2=true` (requires autonomy). Adds
  durable `verifying` CommitDone, fenced leases, sealed artifact hashes with redacted
  projections, shadow governor/council receipts, and A0′/A0 task-class literal
  CommitDone (`UNIGROK_TASK_CLASS` / `UNIGROK_VERIFY_LITERAL`). Candidate text never
  counts as acceptance evidence. Process default is **off**; **Docker compose live
  default is on**. Inspect `/runtimez` → `autonomy`.
- **De-overfitting doctrine:** freeze only near-physics envelopes; treat cognition
  weights, timeouts, and pools as versioned posteriors. See [DEOVERFIT.md](DEOVERFIT.md).
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
  `fallback_reason`, `resolved_depth`; hive adds `hive.stages` with per-stage plane+cost.
  Relay cost/plane to the user; do not hide metered spend.
- **Media:** ask `agent` in natural language ("generate an image of …"); it routes to the
  image/video specialist on the API plane and returns an `images` list of `{url}`. Without
  an API key the gateway returns an honest "needs `XAI_API_KEY`" message — it never
  fabricates a media link.
- **Verify:** after checking an answer, call `record_benchmark_result` with the
  `telemetry_id` so the run counts as verified.

## MCP endpoint

```text
http://localhost:4765/mcp
```

Transport: Streamable HTTP. The MCP handshake, `/healthz`, `/readyz`, `/runtimez`,
`grok_mcp_status`, and `grok_mcp_discover_self` all report version `1.1.0`.

## Public tools

### Main harness and discovery

- `agent` — one-task automatic Grok harness with web research enabled by default,
  optional sessions, scoped knowledge, workspace-context courier, `continue_token`
  reattach, and acceptance-hash CommitDone
- `agent_result` — poll an in-flight quantum without exceeding short IDE deadlines
- `review_pull_request` — review a bounded caller-supplied diff without GitHub or Git access
- `chat` — one stateless, tool-free answer
- `grok_mcp_discover_self` — authoritative live self-description
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

Optional GitHub comment automation (`@grok review`) is a separate maintainer
workflow, not required to use the MCP tool. If you maintain a private Actions
workflow for that trigger, keep it on trusted default-branch code only and never
execute, install, or import code from the reviewed PR.

## Routing and billing

- Callers supply task intent rather than model, plane, effort, or fallback controls.
- When API is ready, Grok 4.5 performs one structured routing pass capped at 256 output
  tokens. Direct work remains subscription-first; selected specialists use the API.
- One bounded cross-plane recovery is permitted after a classified provider failure.
- `UNIGROK_ENABLE_METERED_API=false` disables metered API execution globally.
- API responses include provider billing receipts when available.
- Destructive tools require `confirm_delete=true`.

Language model IDs are discovered independently from the live CLI and API catalogs.
The caller does not select them. Media tools retain provider-defined media defaults.

## Team continuity

Named `agent` sessions persist redacted conversation turns in local SQLite. Deliberately
remembered facts use caller-controlled scopes and can be searched or deleted explicitly.
The `unigrok-public-state` Docker volume survives service restarts.

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
live shadow/reflex runtime.
