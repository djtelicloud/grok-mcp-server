---
okf_version: "0.1"
title: "API Reference"
type: "api_reference"
description: "Auto-generated API reference from the UniGrok codebase."
---

# API Reference

This is the dynamic, auto-generated API reference for the UniGrok MCP server.
It is synchronized natively via the landing process.

## cli.py {#cli}

### Function: `init_project` {#cli-init_project}
**Keywords:** init, project

Create first-run files and print IDE setup snippets.

## credentials.py {#credentials}

### Function: `credential_plane_policy` {#credentials-credential_plane_policy}
**Keywords:** credential, plane, policy

Return the bounded plane preference.

Local UniGrok favors the subscription-backed CLI for compatible, unpinned
work. Cloud Run cannot provide the machine OAuth plane and stays API-first.
Operators can explicitly choose ``api_first`` when API-native behavior is
more important than subscription utilization.

### Function: `build_credential_plane_contract` {#credentials-build_credential_plane_contract}
**Keywords:** build, credential, plane, contract

Build one versioned, prompt-ready credential-plane contract.

## faq.py {#faq}

### Class: `FAQDocumentError` {#faq-faqdocumenterror}
**Keywords:** faq, document, error

Raised when the canonical FAQ cannot be safely loaded or validated.

### Function: `parse_faq_document` {#faq-parse_faq_document}
**Keywords:** parse, faq, document

Parse and validate the strict canonical FAQ Markdown format.

### Function: `get_faq_index` {#faq-get_faq_index}
**Keywords:** get, faq, index

Return the cached index, rebuilding only after the canonical file changes.

### Function: `faq_status` {#faq-faq_status}
**Keywords:** faq, status

Boolean-only readiness view suitable for public-safe health checks.

### Function: `clear_faq_cache` {#faq-clear_faq_cache}
**Keywords:** clear, faq, cache

Test helper: clear the process-local cache after swapping fixture files.

## http_server.py {#http_server}

### Class: `ModeDialContextMiddleware` {#http_server-modedialcontextmiddleware}
**Keywords:** mode, dial, context, middleware

Bind an optional phoneword-port default to this request.

Docker preserves the caller's original ``Host`` port when several host
ports map to the same internal listener. The dial is only a default:
``agent(mode=...)`` remains authoritative.

### Class: `GatewayAuthMiddleware` {#http_server-gatewayauthmiddleware}
**Keywords:** gateway, auth, middleware

Static bearer auth as pure ASGI middleware.

Deliberately NOT Starlette's BaseHTTPMiddleware: its response-buffering
wrapper is known to interfere with SSE client disconnects on the
streamable-HTTP /mcp mount.

### Class: `MCPOriginMiddleware` {#http_server-mcporiginmiddleware}
**Keywords:** mcp, origin, middleware

Origin validation on /mcp and /v1 (MCP-spec DNS-rebinding protection).

/v1/chat/completions reaches the same agent backend as /mcp, so a rebound
browser page must not be able to drive it either.

Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware.
Loopback origins and the UNIGROK_ALLOWED_ORIGINS allowlist pass; any other
browser origin is rejected with 403.

### Class: `CallerContextMiddleware` {#http_server-callercontextmiddleware}
**Keywords:** caller, context, middleware

Binds the request's caller identity to the current async context so
run_agent_turn/orchestrate attribute telemetry, session metadata, and
per-caller budgets without threading a parameter through every route —
including the /mcp mount, whose stateless server task is spawned from the
request context and therefore inherits the contextvar.

Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware;
innermost so origin and auth checks have already passed.

### Class: `RequestIdMiddleware` {#http_server-requestidmiddleware}
**Keywords:** request, id, middleware

Per-request correlation id as pure ASGI middleware (same SSE-disconnect
tombstone as the other gateway middleware).

Outermost of the stack so even origin/auth rejections carry the id: binds
the incoming traceparent's trace-id (or a fresh short id) to the
request-id contextvar — run_agent_turn/orchestrate respect the inherited
value, and the logging filter stamps it on every line — and echoes it
back as an X-Request-Id header on every response, /mcp mount included.

### Class: `RequestBodyLimitMiddleware` {#http_server-requestbodylimitmiddleware}
**Keywords:** request, body, limit, middleware

Reject oversized HTTP bodies before Starlette buffers or parses them.

### Class: `CSPMiddleware` {#http_server-cspmiddleware}
**Keywords:** csp, middleware

ASGI middleware that injects a strict Content-Security-Policy (CSP) header
into all HTTP responses.

### Class: `PullRequestReviewResult` {#http_server-pullrequestreviewresult}
**Keywords:** pull, request, review, result

Read-only Grok review rendered by the ChatGPT/GitHub integration.

## jobs.py {#jobs}

### Class: `JobManager` {#jobs-jobmanager}
**Keywords:** job, manager

Long-running deferred research jobs over the jobs table.

submit() persists a 'queued' row and launches a background asyncio task
that makes one chat.defer() call — defer blocks with internal polling, so
it runs on a dedicated timed thread via run_blocking, bounded by
UNIGROK_JOB_TIMEOUT_SEC. At most UNIGROK_MAX_CONCURRENT_JOBS defer calls
run at once (a semaphore; excess jobs wait 'queued') so long-running jobs
cannot exhaust the shared timed-thread cap. Server-side tools (web/X
search + code execution) are attached so the deferred completion can
research inside xAI infrastructure without a client-side tool loop.
Results and errors are persisted on the row redacted and bounded (like
the prompt at create time); get()/list() views mark abandoned rows
'stale'.

## metrics.py {#metrics}

### Function: `aggregate_telemetry_planes` {#metrics-aggregate_telemetry_planes}
**Keywords:** aggregate, telemetry, planes

Backward-compatible lifetime aggregates used by /metrics/Prometheus.

## rag.py {#rag}

### Function: `task_rag_mode` {#rag-task_rag_mode}
**Keywords:** task, rag, mode

The rollout mode, defaulting to 'off'. An unknown value warns ONCE
and reads as 'off' — this repo has no fail-fast startup validator, so a
loud log plus /metrics + `rag status` visibility is the consistent
choice over aborting a shared local server.

### Function: `has_management_key` {#rag-has_management_key}
**Keywords:** has, management, key

xAI Collections is a MANAGEMENT API: the inference key alone cannot
create/upload/search collections, and xAI exposes no public embedding
models to inference keys (/v1/embedding-models returns []). Most users
therefore run WITHOUT this key — the semantic routing evidence works
fully locally (task_memory_fts bm25 + recency + per-model success); the
cloud mirror is an optional boost gated on this check so keyless setups
never spawn doomed sync work or remote searches.

### Class: `TaskMemoryMirror` {#rag-taskmemorymirror}
**Keywords:** task, memory, mirror

Best-effort cloud mirror for task_memory rows.

Modeled on the knowledge collections adapter (find-or-create by name,
single XAI_API_KEY client, run_blocking offload, warn-once) but with
instance state instead of module globals, soft-disable with exponential
backoff instead of unbounded retries, and a token bucket bounding
remote searches under bursty borderline traffic. Never raises.

### Class: `SemanticVerdict` {#rag-semanticverdict}
**Keywords:** semantic, verdict

Outcome of the semantic evidence pass. prefers_planning=None means
undecidable — the advisor falls through to telemetry/static.

### Function: `fuse_task_evidence` {#rag-fuse_task_evidence}
**Keywords:** fuse, task, evidence

Fuse local FTS candidates with remote semantic hits into one ranked
list, deduped by memory id (each memory contributes exactly once).

local_rows: get_similar_task_memories output (score = 0..1 band plus
bonuses; batch-normalized here so bonused rows correctly dominate).
remote_rows: LOCAL rows mapped from collection hits, each carrying the
raw remote score under 'remote_score' (batch-normalized here).
fused = local_weight*norm_local + remote_weight*norm_remote*recency.

### Function: `semantic_route_signal` {#rag-semantic_route_signal}
**Keywords:** semantic, route, signal

Fused-score-weighted success comparison of memories that ran on the
planning vs the coding model. Decidable only with >= min_evidence
matched memories AND both sides represented; a decidable verdict flips
to planning iff (planning_signal - coding_signal) >= margin.

### Function: `spawn_sync_task` {#rag-spawn_sync_task}
**Keywords:** spawn, sync, task

Best-effort background outbox drain; returns the task or None when
skipped (mode off, drain already in flight, or no running loop). Never
raises and never blocks the caller.

### Function: `rag_cli` {#rag-rag_cli}
**Keywords:** rag, cli

Hand-rolled `rag` subcommand dispatcher (matching src/cli.py's init
pattern — no argparse). Runs against the shared store singleton unless
a store is injected (tests).

### Function: `reset_task_rag_state` {#rag-reset_task_rag_state}
**Keywords:** reset, task, rag, state

Fresh mirror/stats/caches/warn-flags — mirrors the knowledge tests'
reset_collections_state fixture so process globals never leak between
tests.

## routing.py {#routing}

### Function: `extract_routing_features` {#routing-extract_routing_features}
**Keywords:** extract, routing, features

Return a compact prompt-free feature vector safe for telemetry.

### Function: `classify_route` {#routing-classify_route}
**Keywords:** classify, route

Choose a bounded capability class and a human-readable reason code.

### Function: `choose_model_candidate` {#routing-choose_model_candidate}
**Keywords:** choose, model, candidate

Pick one of at most three route candidates with stable hysteresis.

The first available candidate is the cold-start default.  A peer may
displace it only when both have mature calibration or telemetry and the
peer's success rate clears QUALITY_MARGIN.  This margin is the hysteresis:
ordinary noise cannot flap the route between releases or restarts.

## storage.py {#storage}

### Class: `SessionStoreProtocol` {#storage-sessionstoreprotocol}
**Keywords:** session, store, protocol

Public async surface of a UniGrok session/telemetry store.

Structural (duck-typed) protocol: GrokSessionStore satisfies it without
inheriting from it, and tests assert conformance via isinstance (the
@runtime_checkable check verifies member presence, not signatures — the
signatures below are the documented contract).

### Function: `get_store` {#storage-get_store}
**Keywords:** get, store

Build a session store for the configured backend.

UNIGROK_STORAGE_BACKEND selects it ('sqlite' default; blank/unset reads
as sqlite). Unknown values fail fast with NotImplementedError naming the
supported set — a typo must not silently fall back to SQLite. db_path is
backend-specific (the SQLite file path; tests use per-test temp paths).

## tools/chats.py {#tools-chats}

### Class: `GrokReflectionResult` {#tools-chats-grokreflectionresult}
**Keywords:** grok, reflection, result

Schema for a focused, tool-free Grok critique.

## tools/resources.py {#tools-resources}

### Function: `register_resource_primitives` {#tools-resources-register_resource_primitives}
**Keywords:** register, resource, primitives

Register the grok:// resources and the reusable prompts.

## utils.py {#utils}

### Function: `grok_cli_available` {#utils-grok_cli_available}
**Keywords:** grok, cli, available

True when a grok CLI binary is resolvable on this host — the gate the
local CLI plane needs (binary presence only; auth validity is the
CLI's own concern at call time).

### Function: `grok_cli_oauth_env` {#utils-grok_cli_oauth_env}
**Keywords:** grok, cli, oauth, env

Return an environment that cannot silently bill the API plane.

The UniGrok process needs ``XAI_API_KEY`` for its SDK route, but the Grok
CLI inherits process variables by default.  Removing API credentials from
every CLI child is what makes the two planes genuinely independent: the
CLI must use its persisted grok.com OAuth session or fail closed.

### Function: `grok_cli_plane_status` {#utils-grok_cli_plane_status}
**Keywords:** grok, cli, plane, status

Return a bounded, cached, non-secret view of the OAuth CLI plane.

``auth.json`` is necessary but not sufficient: it may be stale.  A
successful ``grok models`` response that explicitly identifies grok.com
login verifies the service credential without consuming an inference
turn.  The probe always strips API-key variables so an API-backed CLI can
never masquerade as the independent subscription plane.

### Function: `grok_cli_check_ready` {#utils-grok_cli_check_ready}
**Keywords:** grok, cli, check, ready

Compatibility wrapper for the verified OAuth CLI-plane probe.

### Function: `credential_plane_contract` {#utils-credential_plane_contract}
**Keywords:** credential, plane, contract

Return the shared non-secret plane health and action contract.

### Function: `get_runtime_stats` {#utils-get_runtime_stats}
**Keywords:** get, runtime, stats

Snapshot of timed-thread pressure (consumed by grok_mcp_status).

### Function: `scoped_session` {#utils-scoped_session}
**Keywords:** scoped, session

Prefix an explicit session name with the requesting client id so each
IDE keeps its own history ('vscode:main'). No client id, or no session,
leaves the name untouched.

### Function: `normalize_caller` {#utils-normalize_caller}
**Keywords:** normalize, caller

Sanitize a caller identity: strip control characters, trim, and bound
to 80 chars (it lands in db rows and metrics keys). None/blank -> None.

### Function: `set_active_caller` {#utils-set_active_caller}
**Keywords:** set, active, caller

Bind the caller identity to the current async context (the HTTP
gateway middleware does this per request). Returns the reset token.

### Function: `caller_from_mcp_context` {#utils-caller_from_mcp_context}
**Keywords:** caller, from, mcp, context

Caller identity from an injected FastMCP Context.

Introspected against the installed mcp 1.26: ctx.session (the
ServerSession) exposes client_params — the InitializeRequestParams the
client sent — whose clientInfo (mcp.types.Implementation) carries
name/version. Degrades to None for clients that never completed
initialize, contexts used outside a request (both raise), or SDK layouts
without client_params.

### Function: `telemetry_row_caller` {#utils-telemetry_row_caller}
**Keywords:** telemetry, row, caller

Caller name from a telemetry row's metadata column (raw JSON text from
the db, or an already-parsed dict from mocks). None for pre-v8 rows,
unattributed traffic, and malformed metadata.

### Class: `CallerBudgetExceeded` {#utils-callerbudgetexceeded}
**Keywords:** caller, budget, exceeded

A caller's UNIGROK_CALLER_BUDGETS daily spend is at/over its limit.

Raised by orchestrate() BEFORE any model work; FastMCP surfaces it to the
client as a tool error (isError), never a server crash.

### Function: `new_request_id` {#utils-new_request_id}
**Keywords:** new, request, id

Fresh short correlation id: the first 12 hex chars of a uuid4 — unique
enough to grep logs and cheap enough to stamp on every row.

### Function: `normalize_request_id` {#utils-normalize_request_id}
**Keywords:** normalize, request, id

Sanitize a request id for logs/db rows/headers: keep only URL- and
header-safe chars, bound to 64 (a W3C trace-id is 32 hex). Blank -> "".

### Function: `set_request_id` {#utils-set_request_id}
**Keywords:** set, request, id

Bind a request id to the current async context (the HTTP gateway
middleware does this per request). Returns the reset token.

### Function: `request_id_scope` {#utils-request_id_scope}
**Keywords:** request, id, scope

Guarantee a bound request id for the duration of one agent call.

Respects an inherited id (gateway traceparent, an outer agent call);
otherwise generates a fresh one and RESETS it on exit so two sequential
calls in the same task never share a correlation id.

### Function: `prefer_cli_for_route` {#utils-prefer_cli_for_route}
**Keywords:** prefer, cli, for, route

Prefer subscription CLI for compatible unpinned local work.

Grok 4.5 thinking, vision, and multi-agent research are API-native. When
the API key is absent, CLI remains the graceful service-saving route even
for a request that asked for thinking; the receipt records that downgrade.

### Function: `load_grok_profile` {#utils-load_grok_profile}
**Keywords:** load, grok, profile

Load a bounded Grok model profile from `.grok/hyperparams`.

### Function: `load_grok_prompt` {#utils-load_grok_prompt}
**Keywords:** load, grok, prompt

Read a Grok adapter prompt from `.grok/prompts` with traversal protection.

### Function: `bounded_env_int` {#utils-bounded_env_int}
**Keywords:** bounded, env, int

Read an integer limit from the environment without allowing extremes.

### Function: `input_limit` {#utils-input_limit}
**Keywords:** input, limit

Named resource-limit helper shared by local and media tools.

### Function: `validate_local_input` {#utils-validate_local_input}
**Keywords:** validate, local, input

Validate a resolved local input before any unbounded read occurs.

### Function: `discover_local_grok_profiles` {#utils-discover_local_grok_profiles}
**Keywords:** discover, local, grok, profiles

List local `.grok` profiles without treating them as provider models.

### Class: `_EvalRecordingChat` {#utils-_evalrecordingchat}
**Keywords:** eval, recording, chat

Chat proxy: delegates everything, records completed responses.

Only append/sample are intercepted directly; parse is wrapped through
__getattr__ so hasattr(chat, "parse") keeps EXACT parity with the
underlying SDK chat (the reflection reviewer capability-gates on it).

### Class: `_EvalRecordingClient` {#utils-_evalrecordingclient}
**Keywords:** eval, recording, client

Client proxy: intercepts chat.create only; every other service
(models, batch, ...) passes straight through.

### Class: `CircuitBreakerOpenError` {#utils-circuitbreakeropenerror}
**Keywords:** circuit, breaker, open, error

Raised to fail fast when a model's circuit breaker is open.

### Function: `classify_xai_error` {#utils-classify_xai_error}
**Keywords:** classify, xai, error

Classify an xAI call failure as "retryable" (429/5xx/connection/timeout/
transient) or "fatal" (400/401/403/404, validation). Fatal errors must not
burn retries — retrying an auth failure only delays the real error.

### Function: `check_circuit_breaker` {#utils-check_circuit_breaker}
**Keywords:** check, circuit, breaker

Fail fast with CircuitBreakerOpenError while a model's breaker is open.

After the cool-down elapses the breaker half-opens: the next call is
allowed through as a probe; its success closes the breaker, its failure
re-opens it via record_xai_failure.

### Function: `record_xai_failure` {#utils-record_xai_failure}
**Keywords:** record, xai, failure

Count a failed xAI call; open the breaker at the consecutive threshold.

### Function: `record_xai_success` {#utils-record_xai_success}
**Keywords:** record, xai, success

Reset a model's breaker after any successful xAI call.

### Function: `get_circuit_breaker_state` {#utils-get_circuit_breaker_state}
**Keywords:** get, circuit, breaker, state

Snapshot of per-model breaker state (consumed by grok_mcp_status).

### Class: `RequestContextLogFilter` {#utils-requestcontextlogfilter}
**Keywords:** request, context, log, filter

Injects the bound request id and caller into every record that passes
through a handler carrying this filter.

record.request_id / record.caller hold the raw values ("" when unset) for
the JSON formatter; record.rid_suffix is a pre-formatted " [rid=<id>]"
fragment so the plain format stays byte-identical to the historical
format when no request id is bound. Never raises — logging must survive
interpreter shutdown and foreign threads (where the contextvars simply
read as unset).

### Class: `JsonLogFormatter` {#utils-jsonlogformatter}
**Keywords:** json, log, formatter

One JSON object per line, stdlib only: ts, level, logger, msg,
request_id (always present, "" when unset), and caller when known.
The rendered message goes through redact_secrets so structured logs get
the same secret hygiene as every persisted surface.

### Function: `extract_cost_from_output` {#utils-extract_cost_from_output}
**Keywords:** extract, cost, from, output

Read the standard usage footer cost from nested local tool output.

### Class: `GitContextCache` {#utils-gitcontextcache}
**Keywords:** git, context, cache

Tiny bounded TTL cache. get_dynamic_context caches one entry per
distinct prompt hash (each holding a multi-KB context string), so entries
MUST be evicted: expired keys are dropped on read and pruned on every
write, and max_entries caps live keys (oldest-first eviction) so a
long-running server never accumulates one entry per unique prompt.

### Function: `format_tool_trace_block` {#utils-format_tool_trace_block}
**Keywords:** format, tool, trace, block

Render a persisted tool trace as a compact context block for replay.

The SDK's assistant() helper cannot carry tool_calls, so replaying raw
tool_result messages would orphan their ids — this text block is the
replay format instead.

### Class: `ToolObservation` {#utils-toolobservation}
**Keywords:** tool, observation

Structured result from an internal tool dispatch call.

### Function: `model_max_tokens_fallback` {#utils-model_max_tokens_fallback}
**Keywords:** model, max, tokens, fallback

Static known-limit lookup — never touches the network.

### Function: `get_model_max_tokens` {#utils-get_model_max_tokens}
**Keywords:** get, model, max, tokens

Resolve maximum prompt token lengths using the xAI SDK's models API,
with a robust known fallback directory for CLI models and network isolation.

Successful API lookups are cached per model for _MODEL_MAX_TOKENS_TTL_SEC so
repeated agent runs do not pay a synchronous SDK network call each time.

### Function: `format_knowledge_notes` {#utils-format_knowledge_notes}
**Keywords:** format, knowledge, notes

Render injected knowledge facts (mirrors format_task_memory_notes):
clearly marked as recalled memory — a hint to verify, never proof.

### Class: `AgentLoopPolicy` {#utils-agentlooppolicy}
**Keywords:** agent, loop, policy

Configurable guardrails for the AgentLoop.

### Function: `register_internal_tool` {#utils-register_internal_tool}
**Keywords:** register, internal, tool

Register a raw async callable for internal agent dispatch.

### Function: `ensure_internal_tools_registered` {#utils-ensure_internal_tools_registered}
**Keywords:** ensure, internal, tools, registered

Import modular tools for side-effect registration when utils is used directly.

### Class: `AgentLoop` {#utils-agentloop}
**Keywords:** agent, loop

True ReAct agentic loop with parallel tool dispatch, cost/timeout guardrails,
and observation truncation. Replaces the closed text-echo recursive loop.

Architecture:
  Tier 1 (xAI server-side built-ins): code_execution, web_search, x_search
    → Passed in AGENTIC_TOOLS_SCHEMA, run inside xAI infra, zero re-entrancy risk.
  Tier 2 (local raw callables): generate_image, file ops, filesystem reads
    → Dispatched via _INTERNAL_TOOL_REGISTRY, executed locally.

### Class: `ModelResolver` {#utils-modelresolver}
**Keywords:** model, resolver

Resolve the routing aliases planning/coding/vision/research to slugs.

Resolution order per alias:
  1. Matching UNIGROK_*_MODEL override — wins over everything, including
     testing mode.
  2. The static default when discovery is disabled
     (UNIGROK_MODEL_DISCOVERY=0) or under UNI_GROK_TESTING (hermetic
     tests never discover).
  3. The TTL-cached live catalog (discover_xai_api_models): the configured
     default when it is still listed, else the closest available slug with
     a WARNING naming old → new.

Resolution is lazy (first use), never runs at import, and never blocks the
event loop — catalog discovery is bridged through run_blocking with its
own timeout inside discover_xai_api_models. Non-alias inputs pass through
unchanged so explicit model slugs keep working everywhere.

### Function: `routing_reason_score` {#utils-routing_reason_score}
**Keywords:** routing, reason, score

Score whether a prompt benefits from the higher-intelligence route.

This keeps routing local and cheap, but avoids escalating every prompt that
happens to contain a broad word such as "product" or "timeline".

### Class: `RoutingDecision` {#utils-routingdecision}
**Keywords:** routing, decision

Diagnostic record of the advisor's last borderline decision.

source precedence: calibration > semantic > telemetry > static.
shadow=True marks a decision where a semantic verdict WAS computed but
the baseline was returned (UNIGROK_TASK_RAG=shadow — zero production
impact by construction).

### Class: `RoutingAdvisor` {#utils-routingadvisor}
**Keywords:** routing, advisor

Telemetry-informed prior for BORDERLINE routing scores (score == 1).

Borderline prompts statically fall to the coding model. This advisor can
flip one to the planning model, consulting three data sources in strict
precedence order:

  1. EVAL CALIBRATION (routing_calibration table, written by
     `python -m evals run`): rows fresh within
     UNIGROK_CALIBRATION_TTL_HOURS (default 168) whose n >= _CALIB_MIN_N
     are aggregated per model; when BOTH models have eligible rows the
     calibration verdict is final — curated golden-task outcomes beat raw
     telemetry.
  2. SEMANTIC TASK-MEMORY EVIDENCE (src/rag.py, only when
     UNIGROK_TASK_RAG is shadow|active and calibration was undecidable):
     fused local-FTS + collection matches for THIS prompt, weighted by
     per-model success. In shadow mode the verdict is recorded but the
     baseline is returned; in active mode a decidable verdict is final
     (a decidable False blocks a telemetry flip, mirroring calibration).
  3. RAW TELEMETRY fallback: the most recent task-memory rows
     (store.get_recent_model_stats, last 200) aggregated into per-model
     success rates; flips only when planning's recent success rate
     exceeds the coding model's by UNIGROK_ADVISOR_MARGIN (default 0.15)
     AND both models have at least _MIN_SAMPLES recent rows.

Both aggregates are cached in-process for _TTL_SEC, so the routing hot
path performs zero extra DB reads between refreshes. Under
UNI_GROK_TESTING the advisor is bypassed entirely (returns the static
prior) unless a test injects data via inject_stats()/inject_calibration()/
inject_semantic() — offline evals and cassettes stay byte-identical.

### Class: `ReflectionVerdict` {#utils-reflectionverdict}
**Keywords:** reflection, verdict

Schema-enforced reviewer verdict for the thinking route.

### Class: `FactList` {#utils-factlist}
**Keywords:** fact, list

Schema-enforced distillation output: 3-8 durable, standalone facts
(parsed via the same tool-free structured-parse machinery as
ReflectionVerdict — see _parse_structured).
