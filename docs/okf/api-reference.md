---
okf_version: "0.1"
title: "API Reference"
type: "api_reference"
description: "Auto-generated API reference from the UniGrok codebase."
---

# API Reference

This deterministic reference is generated from documented public Python symbols.
It is a source-code inventory, not the MCP `tools/list` contract: a Python
symbol appearing here does not mean it is exposed by the stable HTTP service.
Use live MCP discovery for the deployed surface. Topic guides label stable
HTTP, contributor Forge, and trusted stdio capabilities explicitly.
Run `uv run python scripts/generate_okf.py --write` after changing the public API.

## cli.py {#cli}

### Function: `init_project` {#cli-init_project}

```python
def init_project(root: Path | None=None, stream: TextIO | None=None) -> int
```

**Keywords:** init, project

Create first-run files and print IDE setup snippets.

## completion_envelope.py {#completion_envelope}

### Class: `CompletionContractError` {#completion_envelope-completioncontracterror}

```python
class CompletionContractError
```

**Keywords:** completion, contract, error

The completion cannot be accepted under the mechanical contract.

### Class: `CompletionNotFinalError` {#completion_envelope-completionnotfinalerror}

```python
class CompletionNotFinalError
```

**Keywords:** completion, not, final, error

A progress or blocked envelope was presented as a final result.

### Class: `EvidenceResolutionError` {#completion_envelope-evidenceresolutionerror}

```python
class EvidenceResolutionError
```

**Keywords:** evidence, resolution, error

An evidence reference did not resolve to current verified evidence.

### Class: `SchemaCompositionError` {#completion_envelope-schemacompositionerror}

```python
class SchemaCompositionError
```

**Keywords:** schema, composition, error

A caller result schema cannot be safely embedded in the envelope.

### Class: `EvidenceRef` {#completion_envelope-evidenceref}

```python
class EvidenceRef
```

**Keywords:** evidence, ref

Content-bound pointer to one verifier receipt.

### Class: `EvidenceReceipt` {#completion_envelope-evidencereceipt}

```python
class EvidenceReceipt
```

**Keywords:** evidence, receipt

Minimal local receipt shape accepted by evidence resolution.

``verification_status`` describes the verifier receipt itself.  It is not a
task-success label and is never promoted into semantic outcome telemetry.

### Function: `compose_completion_schema` {#completion_envelope-compose_completion_schema}

```python
def compose_completion_schema(result_schema: Mapping[str, Any] | None=None) -> dict[str, Any]
```

**Keywords:** compose, completion, schema

Return a strict JSON Schema for one completion envelope.

A caller schema is copied, checked for remote or scope-changing references,
and embedded under ``complete.result``.  Local JSON-pointer references are
rebased to that location.  The caller's object is never mutated.

### Function: `parse_completion_envelope` {#completion_envelope-parse_completion_envelope}

```python
def parse_completion_envelope(payload: Mapping[str, Any] | str | bytes | bytearray, *, result_schema: Mapping[str, Any] | None=None) -> CompletionEnvelope
```

**Keywords:** parse, completion, envelope

Parse an envelope locally and validate a complete result's caller schema.

### Function: `evidence_receipt_digest` {#completion_envelope-evidence_receipt_digest}

```python
def evidence_receipt_digest(receipt: EvidenceReceipt) -> str
```

**Keywords:** evidence, receipt, digest

Return the canonical content digest used by an :class:`EvidenceRef`.

### Function: `validate_evidence_refs` {#completion_envelope-validate_evidence_refs}

```python
def validate_evidence_refs(envelope: CompletionEnvelope, receipts: Iterable[EvidenceReceipt], *, now: datetime | None=None) -> tuple[EvidenceReceipt, ...]
```

**Keywords:** validate, evidence, refs

Resolve envelope references to current, verified, same-attempt receipts.

This resolver intentionally says nothing about which receipts were required
for the attempt.  Finalization must additionally compare the model-authored
references with a runtime-authoritative set via
:func:`unwrap_complete_result`.

### Function: `unwrap_complete_result` {#completion_envelope-unwrap_complete_result}

```python
def unwrap_complete_result(envelope: CompletionEnvelope, *, expected_attempt_id: str, expected_ttl_expires_at: datetime, provider_finish_reason: str, expected_evidence_refs: Iterable[EvidenceRef], evidence_requirement: EvidenceRequirement, receipts: Iterable[EvidenceReceipt], now: datetime | None=None, result_schema: Mapping[str, Any] | None=None) -> JsonValue
```

**Keywords:** unwrap, complete, result

Mechanically accept and return only a complete result.

This is intentionally not a semantic-success decision.  It checks the
authoritative attempt and TTL, the provider's terminal reason, result
presence, the runtime-selected evidence set, and receipt resolution, then
returns a defensive copy of the result without leaking wrapper fields.

``evidence_requirement`` has no default.  Action-producing paths must use
``"required"`` with a nonempty authoritative reference set.  The ``"none"``
policy is an explicit escape hatch for direct-answer paths and rejects any
expected or model-supplied references.  Neither policy proves that free text
is semantically complete: a typed ``complete`` result can still contain a
promise, so Grok arbitration (and later calibrated Needle evaluation) stays
mandatory wherever semantic completion matters.

## credentials.py {#credentials}

### Function: `credential_plane_policy` {#credentials-credential_plane_policy}

```python
def credential_plane_policy(*, cloudrun: bool=False) -> str
```

**Keywords:** credential, plane, policy

Return the bounded plane preference.

Local UniGrok favors the subscription-backed CLI for compatible, unpinned
work. Cloud Run cannot provide the machine OAuth plane and stays API-first.
Operators can explicitly choose ``api_first`` when API-native behavior is
more important than subscription utilization.

### Function: `build_credential_plane_contract` {#credentials-build_credential_plane_contract}

```python
def build_credential_plane_contract(*, api_configured: bool, cli_status: Optional[Dict[str, Any]], cloudrun: bool=False, containerized: bool=False) -> Dict[str, Any]
```

**Keywords:** build, credential, plane, contract

Build one versioned, prompt-ready credential-plane contract.

## faq.py {#faq}

### Class: `FAQDocumentError` {#faq-faqdocumenterror}

```python
class FAQDocumentError
```

**Keywords:** faq, document, error

Raised when the canonical FAQ cannot be safely loaded or validated.

### Function: `parse_faq_document` {#faq-parse_faq_document}

```python
def parse_faq_document(text: str) -> FAQIndex
```

**Keywords:** parse, faq, document

Parse and validate the strict canonical FAQ Markdown format.

### Function: `get_faq_index` {#faq-get_faq_index}

```python
def get_faq_index() -> FAQIndex
```

**Keywords:** get, faq, index

Return the cached index, rebuilding only after the canonical file changes.

### Function: `faq_status` {#faq-faq_status}

```python
def faq_status() -> Dict[str, Any]
```

**Keywords:** faq, status

Boolean-only readiness view suitable for public-safe health checks.

### Function: `clear_faq_cache` {#faq-clear_faq_cache}

```python
def clear_faq_cache() -> None
```

**Keywords:** clear, faq, cache

Test helper: clear the process-local cache after swapping fixture files.

## http_server.py {#http_server}

### Class: `ModeDialContextMiddleware` {#http_server-modedialcontextmiddleware}

```python
class ModeDialContextMiddleware
```

**Keywords:** mode, dial, context, middleware

Bind an optional phoneword-port default to this request.

Docker preserves the caller's original ``Host`` port when several host
ports map to the same internal listener. The dial is only a default:
``agent(mode=...)`` remains authoritative.

### Class: `GatewayAuthMiddleware` {#http_server-gatewayauthmiddleware}

```python
class GatewayAuthMiddleware
```

**Keywords:** gateway, auth, middleware

Static or remotely introspected OAuth bearer auth as pure ASGI middleware.

Deliberately NOT Starlette's BaseHTTPMiddleware: its response-buffering
wrapper is known to interfere with SSE client disconnects on the
streamable-HTTP /mcp mount.

### Class: `MCPOriginMiddleware` {#http_server-mcporiginmiddleware}

```python
class MCPOriginMiddleware
```

**Keywords:** mcp, origin, middleware

Origin validation on /mcp and /v1 (MCP-spec DNS-rebinding protection).

/v1/chat/completions reaches the same agent backend as /mcp, so a rebound
browser page must not be able to drive it either.

Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware.
Loopback origins and the UNIGROK_ALLOWED_ORIGINS allowlist pass; any other
browser origin is rejected with 403.

### Class: `CallerContextMiddleware` {#http_server-callercontextmiddleware}

```python
class CallerContextMiddleware
```

**Keywords:** caller, context, middleware

Binds the request's caller identity to the current async context so
run_agent_turn/orchestrate attribute telemetry, session metadata, and
per-caller budgets without threading a parameter through every route —
including the /mcp mount, whose stateless server task is spawned from the
request context and therefore inherits the contextvar.

Pure ASGI for the same SSE-disconnect reason as GatewayAuthMiddleware;
innermost so origin and auth checks have already passed.

### Class: `RequestIdMiddleware` {#http_server-requestidmiddleware}

```python
class RequestIdMiddleware
```

**Keywords:** request, id, middleware

Per-request correlation id as pure ASGI middleware (same SSE-disconnect
tombstone as the other gateway middleware).

Outermost of the stack so even origin/auth rejections carry the id: binds
the incoming traceparent's trace-id (or a fresh short id) to the
request-id contextvar — run_agent_turn/orchestrate respect the inherited
value, and the logging filter stamps it on every line — and echoes it
back as an X-Request-Id header on every response, /mcp mount included.

### Class: `RequestBodyLimitMiddleware` {#http_server-requestbodylimitmiddleware}

```python
class RequestBodyLimitMiddleware
```

**Keywords:** request, body, limit, middleware

Reject oversized HTTP bodies before Starlette buffers or parses them.

### Class: `CSPMiddleware` {#http_server-cspmiddleware}

```python
class CSPMiddleware
```

**Keywords:** csp, middleware

ASGI middleware that injects a strict Content-Security-Policy (CSP) header
into all HTTP responses.

### Class: `StaticAssetCacheMiddleware` {#http_server-staticassetcachemiddleware}

```python
class StaticAssetCacheMiddleware
```

**Keywords:** static, asset, cache, middleware

ASGI middleware that stamps ``Cache-Control: no-cache`` on /ui and /docs
responses.

Starlette's ``StaticFiles`` emits ETag/Last-Modified but no Cache-Control,
so browsers apply heuristic freshness and can pair a stale cached
``index.html`` with freshly fetched ``app.js`` — the skew that made the
Control Center discard rendered agent answers. ``no-cache`` keeps caching
(conditional requests answer 304 via the existing ETags) but forces
revalidation, so HTML and JS always come from the same release.

### Function: `unigrok_public_discovery` {#http_server-unigrok_public_discovery}

```python
async def unigrok_public_discovery(_: Request) -> JSONResponse
```

**Keywords:** unigrok, public, discovery

Sanitized, stable project discovery without runtime internals.

This intentionally contains no model availability, credential state,
filesystem/workspace information, client-token counts, or setup commands.

### Function: `oauth_protected_resource_metadata` {#http_server-oauth_protected_resource_metadata}

```python
async def oauth_protected_resource_metadata(_: Request) -> JSONResponse
```

**Keywords:** oauth, protected, resource, metadata

RFC 9728 protected-resource metadata for the external OAuth authority.

### Function: `metrics` {#http_server-metrics}

```python
async def metrics(request: Request) -> Response
```

**Keywords:** metrics

Operational metrics: plain JSON by default, Prometheus text exposition
with ?format=prometheus.

The JSON shape needs no extra dependencies and any JSON-capable collector
can scrape it; the Prometheus variant renders the SAME snapshot as text
exposition 0.0.4 via stdlib string building (see
_render_prometheus_metrics). Auth-protected like every non-probe route
(the auth middleware only exempts /healthz and /readyz). Combines the
telemetry table (per-plane and per-caller aggregates) with in-process
runtime state: circuit breakers, the timed-thread gauge, and the routing
advisor's current view.

### Function: `public_agent` {#http_server-public_agent}

```python
async def public_agent(prompt: str, session: Optional[str]=None, system_prompt: Optional[str]=None, workspace_context: Optional[str]=None, workspace_label: Optional[str]=None, mode: Optional[Literal['auto', 'fast', 'reasoning', 'thinking', 'research']]=None, model: Optional[str]=None, plane: Literal['auto', 'cli', 'api']='auto', fallback_policy: Literal['same_plane', 'cross_plane']='cross_plane') -> AgentResult
```

**Keywords:** public, agent

Single public remote MCP entry point for the UniGrok agent.

Args:
    prompt: The goal, question, or task for the agent.
    session: Optional session name. Persists conversation history and tool
        traces so later calls can continue the work.
    system_prompt: Optional system instruction prepended to the conversation.
    workspace_context: Optional, deliberately selected text from the IDE's
        current project (for example a file excerpt, diff, or error). The
        stable service cannot browse the IDE project automatically.
    workspace_label: Optional human-readable project name for that context.
    mode: Optional explicit mode. When omitted, a phoneword mode-dial port
        supplies the default if enabled; otherwise `"auto"` self-routes.
        `"fast"` forces a single toolless
        completion; `"reasoning"` pins the planning model; `"thinking"`
        runs the agent loop plus a schema-enforced reflection review
        (slowest, most expensive); `"research"` pins the planning route,
        enables multi-agent fan-out, and requests inline citations.
    model: Optional Grok model id. Leave unset (or pass the virtual
        `unigrok-agent`) to let routing choose.
    plane: Starting credential plane. `auto` follows server policy; `cli`
        starts on the SuperGrok subscription; `api` starts on the metered
        developer API.
    fallback_policy: `same_plane` forbids crossing the billing boundary;
        `cross_plane` permits bounded recovery on the other xAI plane.

Returns:
    AgentResult containing execution metadata and responses.

### Class: `PullRequestReviewResult` {#http_server-pullrequestreviewresult}

```python
class PullRequestReviewResult
```

**Keywords:** pull, request, review, result

Read-only Grok review rendered by the ChatGPT/GitHub integration.

### Function: `review_pull_request` {#http_server-review_pull_request}

```python
async def review_pull_request(repository: str, pull_number: int, title: str, diff: str, ci_summary: str='', review_comments: str='', plane: Literal['auto', 'cli', 'api']='auto') -> PullRequestReviewResult
```

**Keywords:** review, pull, request

Review one GitHub pull request without mutating GitHub or local Git.

Use this when ChatGPT or a GitHub workflow has already fetched a PR's
metadata and needs a security-conscious Grok review for Codex to triage.
The diff and comments are untrusted evidence and never grant tool authority.

## identity.py {#identity}

### Function: `scoped_session` {#identity-scoped_session}

```python
def scoped_session(session: Optional[str]) -> Optional[str]
```

**Keywords:** scoped, session

Namespace a session by authenticated principal and client label.

HTTP middleware always binds a principal (OAuth subject, static-key alias,
or the loopback anonymous principal). ``X-Client-ID`` remains an untrusted
subordinate label that separates one principal's IDEs; it never provides
the security boundary by itself. Non-HTTP callers preserve the historical
unscoped behavior unless their transport binds one of these context vars.

### Function: `normalize_caller` {#identity-normalize_caller}

```python
def normalize_caller(value: Any) -> Optional[str]
```

**Keywords:** normalize, caller

Sanitize a caller label and bound it for database/metrics use.

### Function: `normalize_principal` {#identity-normalize_principal}

```python
def normalize_principal(value: Any) -> Optional[str]
```

**Keywords:** normalize, principal

Normalize a principal without collision-prone prefix truncation.

### Function: `set_active_caller` {#identity-set_active_caller}

```python
def set_active_caller(caller: Optional[str])
```

**Keywords:** set, active, caller

Bind the request's reporting identity and return its reset token.

### Function: `set_active_principal` {#identity-set_active_principal}

```python
def set_active_principal(principal: Optional[str])
```

**Keywords:** set, active, principal

Bind the authenticated security principal for the current request.

### Function: `resolve_request_caller` {#identity-resolve_request_caller}

```python
def resolve_request_caller(caller: Optional[str]) -> Optional[str]
```

**Keywords:** resolve, request, caller

Resolve attribution without letting HTTP metadata replace principal.

FastMCP handlers often pass ``clientInfo.name`` explicitly. On HTTP that
value remains only a client label; the middleware's combined
``principal|label`` attribution wins so budget accounting stays anchored
to the principal. Stdio has no HTTP principal and preserves explicit
caller behavior.

### Function: `caller_from_mcp_context` {#identity-caller_from_mcp_context}

```python
def caller_from_mcp_context(ctx: Any) -> Optional[str]
```

**Keywords:** caller, from, mcp, context

Read the MCP ``clientInfo.name`` label from a FastMCP context.

### Function: `telemetry_row_caller` {#identity-telemetry_row_caller}

```python
def telemetry_row_caller(row: Dict[str, Any]) -> Optional[str]
```

**Keywords:** telemetry, row, caller

Return the normalized caller from a telemetry metadata envelope.

## intelligence_capsule.py {#intelligence_capsule}

### Class: `CapsuleValidationError` {#intelligence_capsule-capsulevalidationerror}

```python
class CapsuleValidationError
```

**Keywords:** capsule, validation, error

Raised when a value cannot be a canonical IntelligenceCapsule.

### Function: `canonicalize` {#intelligence_capsule-canonicalize}

```python
def canonicalize(value: Any) -> bytes
```

**Keywords:** canonicalize

Return constrained RFC 8785-compatible UTF-8 bytes for ``value``.

### Function: `parse_canonical` {#intelligence_capsule-parse_canonical}

```python
def parse_canonical(raw: bytes) -> Any
```

**Keywords:** parse, canonical

Decode canonical wire bytes and reject alternate JSON spellings.

Verifiers must call this on the original bytes instead of accepting an
already-parsed framework object.  Re-encoding catches duplicate keys,
whitespace, alternate escapes, key-order drift, BOMs, and numeric aliases.

### Function: `digest_body` {#intelligence_capsule-digest_body}

```python
def digest_body(body: Mapping[str, Any]) -> str
```

**Keywords:** digest, body

Return the lowercase SHA-256 digest of a validated capsule body.

### Function: `capsule_id` {#intelligence_capsule-capsule_id}

```python
def capsule_id(body: Mapping[str, Any]) -> str
```

**Keywords:** capsule, id

Return the stable protocol identity for ``body``.

### Function: `build_envelope` {#intelligence_capsule-build_envelope}

```python
def build_envelope(body: Mapping[str, Any], *, signatures: Sequence[Mapping[str, Any]]=()) -> dict[str, Any]
```

**Keywords:** build, envelope

Build and validate an IntelligenceCapsule envelope.

### Function: `validate_envelope_integrity` {#intelligence_capsule-validate_envelope_integrity}

```python
def validate_envelope_integrity(value: Mapping[str, Any]) -> None
```

**Keywords:** validate, envelope, integrity

Validate structure and body digest, not authorship or signature validity.

### Function: `validate_body` {#intelligence_capsule-validate_body}

```python
def validate_body(value: Mapping[str, Any]) -> None
```

**Keywords:** validate, body

Validate the normative IntelligenceCapsule v1 body schema.

## intelligence_payloads.py {#intelligence_payloads}

### Function: `validate_known_payload_profile` {#intelligence_payloads-validate_known_payload_profile}

```python
def validate_known_payload_profile(body: Mapping[str, Any]) -> bool
```

**Keywords:** validate, known, payload, profile

Validate a registered payload profile and return whether it was known.

The caller must first apply the generic IntelligenceCapsule v1 validator.
Unknown versioned payloads remain structurally valid capsules, but this
function returns ``False`` so a materializer can quarantine them instead
of executing or promoting semantics it does not understand.

### Function: `payload_profile_schema_sha256` {#intelligence_payloads-payload_profile_schema_sha256}

```python
def payload_profile_schema_sha256(schema: str) -> str
```

**Keywords:** payload, profile, schema, sha, 256

Return the pinned raw-schema digest for a registered payload profile.

### Function: `validate_optibench_evidence` {#intelligence_payloads-validate_optibench_evidence}

```python
def validate_optibench_evidence(body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes]) -> dict[str, tuple[int, ...]]
```

**Keywords:** validate, optibench, evidence

Verify OptiBench receipts and recompute every published objective.

This consistency verifier does not prove that hardware executed; signed
publication identifies the runner that made that claim.  It does prove
that the closed population, passing gates, raw samples, aggregation, and
canonical body metrics agree byte-for-byte.

### Function: `validate_optibench_population` {#intelligence_payloads-validate_optibench_population}

```python
def validate_optibench_population(benchmark_bodies: Mapping[str, Mapping[str, Any]], evidence_blobs: Mapping[str, Mapping[str, bytes]]) -> dict[str, dict[str, Any]]
```

**Keywords:** validate, optibench, population

Verify one complete cohort and recompute exact NSGA-II fields.

Individual counter receipts can prove a candidate's objective tuple, but
Pareto rank and crowding are properties of a closed population.  This gate
therefore requires exactly one benchmark capsule for every candidate in
the shared population, verifies every evidence set, and recomputes both
rank and exact rational crowding before any result is promotable.

### Function: `validate_gno_dispatch_evidence` {#intelligence_payloads-validate_gno_dispatch_evidence}

```python
def validate_gno_dispatch_evidence(body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes]) -> None
```

**Keywords:** validate, gno, dispatch, evidence

Verify the declared GNO input manifest and every referenced input blob.

### Function: `validate_gno_result_graph` {#intelligence_payloads-validate_gno_result_graph}

```python
def validate_gno_result_graph(result: Mapping[str, Any], dispatch: Mapping[str, Any], *, result_evidence_blobs: Mapping[str, bytes], dispatch_evidence_blobs: Mapping[str, bytes]) -> None
```

**Keywords:** validate, gno, result, graph

Bind a GNO result to the exact verified dispatch it answers.

### Function: `validate_dpo_preference_graph` {#intelligence_payloads-validate_dpo_preference_graph}

```python
def validate_dpo_preference_graph(body: Mapping[str, Any], graph_bodies: Mapping[str, Mapping[str, Any]], evidence_blobs: Mapping[str, bytes], graph_evidence_blobs: Mapping[str, Mapping[str, bytes]]) -> None
```

**Keywords:** validate, dpo, preference, graph

Resolve a closed OptiBench cohort and prove one direct preference.

The supplied graph closure must contain the task, every candidate in the
population, and exactly one verified OptiBench result for every candidate.
Ranks are recomputed from the final metrics; stored online Swarm reward or
provisional rank is never accepted as preference evidence.

### Function: `build_preference_example` {#intelligence_payloads-build_preference_example}

```python
def build_preference_example(body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes], *, graph_bodies: Mapping[str, Mapping[str, Any]], graph_evidence_blobs: Mapping[str, Mapping[str, bytes]]) -> dict[str, str]
```

**Keywords:** build, preference, example

Build one verified preference example for nested inference context.

Blob bytes are resolved by evidence name, checked against the capsule's
byte count and SHA-256 descriptor, and decoded as strict UTF-8.  The
returned record can be nested into an executor's JSON context.  This is
in-context conditioning, not a parameter update.

### Function: `render_preference_jsonl` {#intelligence_payloads-render_preference_jsonl}

```python
def render_preference_jsonl(body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes], *, graph_bodies: Mapping[str, Mapping[str, Any]], graph_evidence_blobs: Mapping[str, Mapping[str, bytes]]) -> bytes
```

**Keywords:** render, preference, jsonl

Render one verified preference example as canonical JSONL.

### Function: `build_needle_tools_context` {#intelligence_payloads-build_needle_tools_context}

```python
def build_needle_tools_context(query: str, examples: Sequence[Mapping[str, str]], *, tokenizer: str, token_counter: Callable[[str], int], max_encoder_tokens: int=1024, max_examples: int=8) -> dict[str, Any]
```

**Keywords:** build, needle, tools, context

Fit whole verified examples into Needle's actual tools-JSON channel.

``token_counter`` must use the pinned Needle tokenizer.  Selection is
deterministic: examples are deduplicated and sorted by source capsule,
then whole records are admitted while they fit beside the query and the
``<tools>`` separator.  Records are never string-sliced.

## jobs.py {#jobs}

### Class: `JobManager` {#jobs-jobmanager}

```python
class JobManager
```

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

### Method: `JobManager.submit` {#jobs-jobmanager-submit}

```python
async def JobManager.submit(self, prompt: str, model: Optional[str]=None, agent_count: Optional[int]=None, caller: Optional[str]=None) -> Dict[str, Any]
```

**Keywords:** job, manager, submit

Create a job row and launch its background defer task.

caller (the submitting agent's identity) is persisted on the row —
explicit param first, else whatever the transport bound to the
current async context; None stays None.

### Method: `JobManager.submit_distill` {#jobs-jobmanager-submit_distill}

```python
async def JobManager.submit_distill(self, session: str, caller: Optional[str]=None) -> Dict[str, Any]
```

**Keywords:** job, manager, submit, distill

Create a 'distill' job row and launch its background task.

The job summarizes the session's STORED history into 3-8 durable
facts on the cheap coding model via the shared tool-free
structured-parse machinery (FactList), redacts every fact, and saves
them to the knowledge table (scope='global',
source='session:<name>'). Rides the same jobs-table lifecycle and
defer-slot semaphore as research jobs — a distill run pins one timed
thread for the parse call.

caller attribution matches submit(): explicit param first, else
whatever the transport bound to the current async context (the
gateway's X-Caller / MCP clientInfo); None stays None.

### Method: `JobManager.describe` {#jobs-jobmanager-describe}

```python
def JobManager.describe(cls, row: Dict[str, Any]) -> Dict[str, Any]
```

**Keywords:** job, manager, describe

Public view of a job row (what get_research_job returns).

### Method: `JobManager.wait` {#jobs-jobmanager-wait}

```python
async def JobManager.wait(self, job_id: str) -> None
```

**Keywords:** job, manager, wait

Await a job's in-flight task if this process owns one (used by
tests and graceful shutdown; a no-op for finished/foreign jobs).

## metrics.py {#metrics}

### Function: `aggregate_telemetry_planes` {#metrics-aggregate_telemetry_planes}

```python
def aggregate_telemetry_planes(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]
```

**Keywords:** aggregate, telemetry, planes

Backward-compatible lifetime aggregates used by /metrics/Prometheus.

### Function: `fetch_provider_api_usage` {#metrics-fetch_provider_api_usage}

```python
async def fetch_provider_api_usage() -> Dict[str, Any]
```

**Keywords:** fetch, provider, api, usage

Optionally fetch today's team-wide API spend from xAI Management API.

## provider_harvest.py {#provider_harvest}

### Function: `worker_episode_collection_name` {#provider_harvest-worker_episode_collection_name}

```python
def worker_episode_collection_name() -> str
```

**Keywords:** worker, episode, collection, name

Return a safe collection name that cannot alias verified task memory.

### Function: `worker_episode_document_name` {#provider_harvest-worker_episode_document_name}

```python
def worker_episode_document_name(row: dict[str, Any]) -> str
```

**Keywords:** worker, episode, document, name

Content-addressed logical name reused by every retry.

### Function: `freeze_worker_episode_document` {#provider_harvest-freeze_worker_episode_document}

```python
def freeze_worker_episode_document(row: dict[str, Any]) -> FrozenWorkerEpisode
```

**Keywords:** freeze, worker, episode, document

Create the one immutable cloud artifact for a terminal ledger row.

### Function: `worker_episode_document` {#provider_harvest-worker_episode_document}

```python
def worker_episode_document(row: dict[str, Any]) -> bytes
```

**Keywords:** worker, episode, document

Return and verify the exact document frozen at terminal transition.

### Class: `XAIWorkerEpisodeUploader` {#provider_harvest-xaiworkerepisodeuploader}

```python
class XAIWorkerEpisodeUploader
```

**Keywords:** xai, worker, episode, uploader

Synchronous, idempotent xAI Collections boundary.

The xAI service chooses physical file IDs. UniGrok supplies a stable
content-addressed name plus unique ``episode_id`` and ``document_digest``
collection fields, and checks both before every upload. A timeout after a
successful remote write is therefore recovered by lookup rather than a
second logical row.

### Method: `XAIWorkerEpisodeUploader.prepare_client` {#provider_harvest-xaiworkerepisodeuploader-prepare_client}

```python
def XAIWorkerEpisodeUploader.prepare_client(self) -> tuple[Any | None, str | None]
```

**Keywords:** xai, worker, episode, uploader, prepare, client

Resolve local capability before any row is leased or cloud call occurs.

### Method: `XAIWorkerEpisodeUploader.upload` {#provider_harvest-xaiworkerepisodeuploader-upload}

```python
def XAIWorkerEpisodeUploader.upload(self, client: Any, row: dict[str, Any], authority: _ProviderHarvestEffectAuthority) -> str
```

**Keywords:** xai, worker, episode, uploader, upload

Upload or recover one logical episode and return its xAI file ID.

### Class: `ProviderAttemptHarvester` {#provider_harvest-providerattemptharvester}

```python
class ProviderAttemptHarvester
```

**Keywords:** provider, attempt, harvester

One explicitly invoked bounded pass over the provider-attempt outbox.

Constructing this worker performs no effect, and this module does not
schedule it. The future Grok-owned broker is responsible for invocation.

### Method: `ProviderAttemptHarvester.run_once` {#provider_harvest-providerattemptharvester-run_once}

```python
async def ProviderAttemptHarvester.run_once(self, store: Any, *, deadline_monotonic: float | None=None) -> ProviderHarvestRun
```

**Keywords:** provider, attempt, harvester, run, once

Run one bounded batch inside an optional caller-owned deadline.

The caller deadline is absolute so cancellation cannot accidentally
grant a background SDK thread a fresh per-row lease. Every row token
is bounded by both that deadline and the local outbox lease, and is
revoked in ``finally`` even when this coroutine is cancelled.

## providers/base.py {#providers-base}

### Class: `HTTPProviderAdapter` {#providers-base-httpprovideradapter}

```python
class HTTPProviderAdapter
```

**Keywords:** http, provider, adapter

Base for one-shot first-party JSON APIs.

An injected AsyncClient makes every wire interaction deterministic in tests.
Production clients are short-lived, do not inherit proxy environment state,
and never follow redirects carrying credentials.

### Method: `HTTPProviderAdapter.complete` {#providers-base-httpprovideradapter-complete}

```python
async def HTTPProviderAdapter.complete(self, request: ProviderRequest) -> ProviderResponse
```

**Keywords:** http, provider, adapter, complete

Run the complete worker call under one absolute supervisor deadline.

### Method: `HTTPProviderAdapter.attempt` {#providers-base-httpprovideradapter-attempt}

```python
async def HTTPProviderAdapter.attempt(self, request: ProviderRequest) -> ProviderAttemptResult
```

**Keywords:** http, provider, adapter, attempt

Return a complete worker result without granting it semantic authority.

### Function: `opaque_fingerprint` {#providers-base-opaque_fingerprint}

```python
def opaque_fingerprint(value: str) -> str
```

**Keywords:** opaque, fingerprint

Identify a non-secret account/project without exposing its raw value.

## providers/broker.py {#providers-broker}

### Class: `BrokerCancellationPersistenceError` {#providers-broker-brokercancellationpersistenceerror}

```python
class BrokerCancellationPersistenceError
```

**Keywords:** broker, cancellation, persistence, error

A cancelled physical attempt could not be durably terminalized.

### Class: `WorkerFallbackPolicy` {#providers-broker-workerfallbackpolicy}

```python
class WorkerFallbackPolicy
```

**Keywords:** worker, fallback, policy

Bound one delegation to subscription-only or one metered fallback.

### Class: `GrokWorkerLaneAuthorization` {#providers-broker-grokworkerlaneauthorization}

```python
class GrokWorkerLaneAuthorization
```

**Keywords:** grok, worker, lane, authorization

Plan-bound authorization for one immutable provider lane snapshot.

### Method: `GrokWorkerLaneAuthorization.from_descriptor` {#providers-broker-grokworkerlaneauthorization-from_descriptor}

```python
def GrokWorkerLaneAuthorization.from_descriptor(cls, descriptor: ProviderDescriptor) -> 'GrokWorkerLaneAuthorization'
```

**Keywords:** grok, worker, lane, authorization, from, descriptor

Freeze a reviewed descriptor into digest-only plan authority.

### Class: `GrokWorkerDelegation` {#providers-broker-grokworkerdelegation}

```python
class GrokWorkerDelegation
```

**Keywords:** grok, worker, delegation

One semantic worker request chosen by the Grok supervisor.

The Grok-owned plan carries only content digests for reviewed physical
lane snapshots. The broker still applies the fixed same-provider ladder,
while starts carry the full material needed to verify those digests.

### Class: `GrokDelegationPlan` {#providers-broker-grokdelegationplan}

```python
class GrokDelegationPlan
```

**Keywords:** grok, delegation, plan

Content-addressed internal plan bound to one exact Grok turn.

``supervisor='grok'`` and a Grok-shaped model ID are validation constraints,
not authentication.  The future runtime integration must accept plans only
from its trusted Grok session state, never directly from an MCP caller.

### Class: `BrokerAttemptEvidence` {#providers-broker-brokerattemptevidence}

```python
class BrokerAttemptEvidence
```

**Keywords:** broker, attempt, evidence

One physical attempt, with worker output exposed only after durability.

### Method: `GrokWorkerBrokerResult.validate_against_plan` {#providers-broker-grokworkerbrokerresult-validate_against_plan}

```python
def GrokWorkerBrokerResult.validate_against_plan(self, plan: GrokDelegationPlan | Mapping[str, Any]) -> 'GrokWorkerBrokerResult'
```

**Keywords:** grok, worker, broker, result, validate, against, plan

Bind exported evidence to one exact originating Grok plan.

The result intentionally does not duplicate model-visible prompts or
fallback policy. Consumers holding the originating plan must cross the
same explicit boundary used by :meth:`GrokWorkerBroker.execute` before
trusting delegation labels or attempt identities.

### Class: `GrokWorkerBroker` {#providers-broker-grokworkerbroker}

```python
class GrokWorkerBroker
```

**Keywords:** grok, worker, broker

Execute strict subordinate attempts while preserving Grok authority.

### Method: `GrokWorkerBroker.execute` {#providers-broker-grokworkerbroker-execute}

```python
async def GrokWorkerBroker.execute(self, plan: GrokDelegationPlan | Mapping[str, Any]) -> GrokWorkerBrokerResult
```

**Keywords:** grok, worker, broker, execute

Run one plan and return transport evidence for Grok synthesis only.

## providers/config.py {#providers-config}

### Function: `load_model_pins` {#providers-config-load_model_pins}

```python
def load_model_pins(channel: ProviderChannel, environ: Mapping[str, str]) -> ProviderModelPins
```

**Keywords:** load, model, pins

Resolve route pin > provider pin > stable first-party default.

Invalid values fail closed while naming only the environment variable, never
its content.

## providers/contracts.py {#providers-contracts}

### Function: `transport_resource_identity` {#providers-contracts-transport_resource_identity}

```python
def transport_resource_identity(namespace: str, value: str) -> str
```

**Keywords:** transport, resource, identity

Return a secret-safe stable identity for one configured transport resource.

### Class: `GrokSupervisorBinding` {#providers-contracts-groksupervisorbinding}

```python
class GrokSupervisorBinding
```

**Keywords:** grok, supervisor, binding

Opaque Grok-owned state copied into every worker receipt.

Adapters may bind outputs to this state, but cannot create, extend, route,
verify, harvest, or finalize it.

### Class: `WorkerAuthority` {#providers-contracts-workerauthority}

```python
class WorkerAuthority
```

**Keywords:** worker, authority

Mechanical denial of supervisor authority to external model workers.

### Function: `model_visible_messages` {#providers-contracts-model_visible_messages}

```python
def model_visible_messages(request: ProviderRequest) -> tuple[ProviderMessage, ...]
```

**Keywords:** model, visible, messages

Return the exact normalized messages shown to a subordinate worker.

The supervisor deadline is model-visible and shared by every transport.
Keeping this construction in the contract module lets the adapter, the
attempt ledger, and the Grok broker layer hash the same logical request.

### Class: `ProviderExecutionBinding` {#providers-contracts-providerexecutionbinding}

```python
class ProviderExecutionBinding
```

**Keywords:** provider, execution, binding

Stable physical-lane material frozen into a provider attempt start.

Availability metadata and credential names are intentionally excluded.
Model pins, supported routes, and physical caps are included so a trusted
plan can authorize the exact lane snapshot without depending on a live
registry during durable replay.

### Class: `ProviderAttemptStart` {#providers-contracts-providerattemptstart}

```python
class ProviderAttemptStart
```

**Keywords:** provider, attempt, start

Grok-authorized identity for one physical subordinate channel call.

Versions 1 and 2 remain parseable for ledger inspection and migration
tooling. Broker evidence and replay intentionally fail closed unless the
start is version 3 with complete plan-bound lane material.

### Class: `ProviderFailureReceipt` {#providers-contracts-providerfailurereceipt}

```python
class ProviderFailureReceipt
```

**Keywords:** provider, failure, receipt

Bounded, secret-safe failure evidence returned to the Grok supervisor.

### Class: `ProviderAttemptResult` {#providers-contracts-providerattemptresult}

```python
class ProviderAttemptResult
```

**Keywords:** provider, attempt, result

One subordinate worker return or failure for Grok synthesis.

### Function: `provider_result_matches_start` {#providers-contracts-provider_result_matches_start}

```python
def provider_result_matches_start(start: ProviderAttemptStart, result: ProviderAttemptResult) -> bool
```

**Keywords:** provider, result, matches, start

Return whether one normalized result is bound to its exact v2 start.

### Function: `is_safe_model_id` {#providers-contracts-is_safe_model_id}

```python
def is_safe_model_id(value: str) -> bool
```

**Keywords:** is, safe, model, id

Return whether a provider-supplied model ID is safe to put in a receipt.

### Function: `is_safe_response_id` {#providers-contracts-is_safe_response_id}

```python
def is_safe_response_id(value: str) -> bool
```

**Keywords:** is, safe, response, id

Return whether an upstream response ID is safe to put in a receipt.

## providers/errors.py {#providers-errors}

### Class: `ProviderAuthorizationInvariantError` {#providers-errors-providerauthorizationinvarianterror}

```python
class ProviderAuthorizationInvariantError
```

**Keywords:** provider, authorization, invariant, error

A server-owned authorization invariant failed after routing.

This is deliberately neither a configuration, transport, nor protocol
failure.  In particular, a consumed one-shot delegation must never make a
broker eligible to repeat the effect through an API fallback.

## providers/mcp_sampling.py {#providers-mcp_sampling}

### Class: `MCPSessionAuthorization` {#providers-mcp_sampling-mcpsessionauthorization}

```python
class MCPSessionAuthorization
```

**Keywords:** mcp, session, authorization

Gateway-issued authorization for one principal-bound MCP session.

``verified_local`` covers a directly verified loopback request or a
separately attested loopback-only proxy boundary.  Anonymous HTTP is only
legal inside that boundary.  Remote sessions must have an authenticated
principal.  This object is not accepted from request JSON or headers; a
future stateful session middleware must inject it into the ASGI scope.

### Class: `TrustedMCPProviderGrant` {#providers-mcp_sampling-trustedmcpprovidergrant}

```python
class TrustedMCPProviderGrant
```

**Keywords:** trusted, mcp, provider, grant

Server-owned provider/model grant bound to one authorized MCP session.

Client labels and advertised sampling support never imply a provider
brand.  Only later Grok routing policy may issue this one-delegation grant;
generic session middleware must never mint it.

### Method: `TrustedMCPProviderGrant.effect_digest` {#providers-mcp_sampling-trustedmcpprovidergrant-effect_digest}

```python
def TrustedMCPProviderGrant.effect_digest(self) -> str
```

**Keywords:** trusted, mcp, provider, grant, effect, digest

Stable one-shot identity for the exact broker-authorized effect.

Grant IDs and validity windows are intentionally excluded.  Reissuing
a grant cannot repeat an indeterminate physical effect; an authorized
retry requires a new deterministic ``ProviderRequest.request_id``.

### Class: `MCPSamplingSessionRuntime` {#providers-mcp_sampling-mcpsamplingsessionruntime}

```python
class MCPSamplingSessionRuntime
```

**Keywords:** mcp, sampling, session, runtime

Mutable session-wide revocation and concurrency gate.

A future authoritative stateful-session registry creates exactly one of
these objects for one ``MCPSessionAuthorization`` and injects that same
object into every request scope for the MCP session.  It is intentionally
not a module global.  Separate request leases therefore share one physical
sampling slot and one disconnect/revocation state.

### Method: `MCPSamplingSessionRuntime.effect_claimed` {#providers-mcp_sampling-mcpsamplingsessionruntime-effect_claimed}

```python
def MCPSamplingSessionRuntime.effect_claimed(self, effect_digest: str) -> bool
```

**Keywords:** mcp, sampling, session, runtime, effect, claimed

Return exact session-owned state for one stable effect identity.

### Method: `MCPSamplingSessionRuntime.claim_effect` {#providers-mcp_sampling-mcpsamplingsessionruntime-claim_effect}

```python
def MCPSamplingSessionRuntime.claim_effect(self, effect_digest: str) -> bool
```

**Keywords:** mcp, sampling, session, runtime, claim, effect

Atomically consume one grant before its physical sampling effect.

This runtime belongs to one asyncio session loop.  The synchronous
check-and-add has no suspension point and is therefore atomic with
respect to every lease sharing that runtime.  Claims are terminal:
revocation, timeout, disconnect, and indeterminate provider outcomes
never remove them.

### Method: `MCPSamplingSessionRuntime.revoke` {#providers-mcp_sampling-mcpsamplingsessionruntime-revoke}

```python
async def MCPSamplingSessionRuntime.revoke(self, *, drain_timeout_seconds: float=1.0) -> None
```

**Keywords:** mcp, sampling, session, runtime, revoke

Revoke the whole MCP session and boundedly cancel every sample.

### Class: `StatefulMCPSamplingLease` {#providers-mcp_sampling-statefulmcpsamplinglease}

```python
class StatefulMCPSamplingLease
```

**Keywords:** stateful, mcp, sampling, lease

Short-lived callback lease around one exact FastMCP tool request.

### Method: `StatefulMCPSamplingLease.revoke` {#providers-mcp_sampling-statefulmcpsamplinglease-revoke}

```python
async def StatefulMCPSamplingLease.revoke(self) -> None
```

**Keywords:** stateful, mcp, sampling, lease, revoke

Revoke the callback, cancel in-flight samples, and bound the drain.

### Function: `create_stateful_mcp_sampling_lease` {#providers-mcp_sampling-create_stateful_mcp_sampling_lease}

```python
def create_stateful_mcp_sampling_lease(ctx: Any, *, provider: ProviderId, channel: ProviderChannel, provider_request: ProviderRequest, drain_timeout_seconds: Annotated[float, Field(gt=0.0, le=10.0)]=1.0, clock: Clock | None=None) -> StatefulMCPSamplingLease
```

**Keywords:** create, stateful, mcp, sampling, lease

Validate the current FastMCP request and create an inert sampling lease.

The returned object must be used as an async context manager.  Merely
constructing it has no provider, process, network, storage, or routing
effect.

## providers/registry.py {#providers-registry}

### Function: `build_provider_registry` {#providers-registry-build_provider_registry}

```python
def build_provider_registry(*, environ: Mapping[str, str] | None=None, clients: Mapping[ProviderChannel, httpx.AsyncClient] | None=None, vertex_token_provider: ADCTokenProvider | None=None, clock: Clock | None=None) -> dict[ProviderChannel, ProviderAdapter]
```

**Keywords:** build, provider, registry

Build adapters without performing discovery or provider calls.

## providers/subscription.py {#providers-subscription}

### Class: `ClaudeCLIAdapter` {#providers-subscription-claudecliadapter}

```python
class ClaudeCLIAdapter
```

**Keywords:** claude, cli, adapter

One-shot Claude Code OAuth worker with tools and persistence disabled.

### Function: `provider_request_digest` {#providers-subscription-provider_request_digest}

```python
def provider_request_digest(request: ProviderRequest) -> str
```

**Keywords:** provider, request, digest

Canonical secret-safe digest of one complete provider request.

### Class: `MCPClientSamplingAdapter` {#providers-subscription-mcpclientsamplingadapter}

```python
class MCPClientSamplingAdapter
```

**Keywords:** mcp, client, sampling, adapter

One lease-owned MCP sampling lane bound to one trusted provider grant.

The private authority state is sealed against ordinary assignment and
rechecked before and after every await.  Arbitrary code execution inside
the trusted server process remains outside this object's security boundary.

### Method: `MCPClientSamplingAdapter.effect_claimed` {#providers-subscription-mcpclientsamplingadapter-effect_claimed}

```python
def MCPClientSamplingAdapter.effect_claimed(self) -> bool
```

**Keywords:** mcp, client, sampling, adapter, effect, claimed

Return fail-closed one-shot state from the lease-owned runtime.

### Method: `MCPClientSamplingAdapter.complete` {#providers-subscription-mcpclientsamplingadapter-complete}

```python
async def MCPClientSamplingAdapter.complete(self, request: ProviderRequest) -> ProviderResponse
```

**Keywords:** mcp, client, sampling, adapter, complete

Preserve post-claim indeterminacy across the base TTL boundary.

The future broker integration must perform the same probe around its
own outer timeout before this adapter can be wired into runtime.

### Function: `build_subscription_registry` {#providers-subscription-build_subscription_registry}

```python
def build_subscription_registry(*, claude_executable: str='claude', environ: Mapping[str, str] | None=None, claude_runner: CLIProcessRunner | None=None, clock: Clock | None=None) -> dict[ProviderChannel, ProviderAdapter]
```

**Keywords:** build, subscription, registry

Construct request-scoped subscription adapters without any effect.

## providers/vertex.py {#providers-vertex}

### Function: `load_google_adc_identity` {#providers-vertex-load_google_adc_identity}

```python
async def load_google_adc_identity(timeout_seconds: float=60.0) -> ADCIdentity
```

**Keywords:** load, google, adc, identity

Resolve and refresh ADC off the event loop.

All third-party exception details are suppressed at the adapter boundary so
credential paths, token fragments, and account identifiers cannot escape.

## rag.py {#rag}

### Function: `task_rag_mode` {#rag-task_rag_mode}

```python
def task_rag_mode() -> str
```

**Keywords:** task, rag, mode

The rollout mode, defaulting to 'off'. An unknown value warns ONCE
and reads as 'off' — this repo has no fail-fast startup validator, so a
loud log plus /metrics + `rag status` visibility is the consistent
choice over aborting a shared local server.

### Function: `has_management_key` {#rag-has_management_key}

```python
def has_management_key() -> bool
```

**Keywords:** has, management, key

xAI Collections is a MANAGEMENT API: the inference key alone cannot
create/upload/search collections, and xAI exposes no public embedding
models to inference keys (/v1/embedding-models returns []). Most users
therefore run WITHOUT either supported management-key alias — the
semantic routing evidence works fully locally (task_memory_fts bm25 +
recency + per-model success); the cloud mirror is an optional boost gated
on this check so keyless setups never spawn doomed sync work or remote
searches.

### Class: `TaskMemoryMirror` {#rag-taskmemorymirror}

```python
class TaskMemoryMirror
```

**Keywords:** task, memory, mirror

Best-effort cloud mirror for task_memory rows.

Modeled on the knowledge collections adapter (find-or-create by name,
role-separated xAI management client, run_blocking offload, warn-once) but with
instance state instead of module globals, soft-disable with exponential
backoff instead of unbounded retries, and a token bucket bounding
remote searches under bursty borderline traffic. Never raises.

### Method: `TaskMemoryMirror.document_body` {#rag-taskmemorymirror-document_body}

```python
def TaskMemoryMirror.document_body(self, row: Dict[str, Any]) -> str
```

**Keywords:** task, memory, mirror, document, body

One-line JSON header (ultra-reliable fallback identity parse)
followed by a search-friendly prose summary. Every field is already
redacted/bounded at rest by save_task_memory.

### Method: `TaskMemoryMirror.ready` {#rag-taskmemorymirror-ready}

```python
async def TaskMemoryMirror.ready(self) -> bool
```

**Keywords:** task, memory, mirror, ready

Real readiness probe: mode enabled + capable SDK + resolvable
collection. Updates last_known_ready (which /metrics reads without
any network call).

### Method: `TaskMemoryMirror.upload_memory` {#rag-taskmemorymirror-upload_memory}

```python
async def TaskMemoryMirror.upload_memory(self, row: Dict[str, Any]) -> Optional[str]
```

**Keywords:** task, memory, mirror, upload, memory

Upload ONE task-memory row; returns the remote file id (the
deterministic document name when the SDK returns no id).

### Method: `TaskMemoryMirror.search` {#rag-taskmemorymirror-search}

```python
async def TaskMemoryMirror.search(self, query: str, limit: int) -> List[Dict[str, Any]]
```

**Keywords:** task, memory, mirror, search

AT MOST one bounded semantic search; [] on any failure, cooldown,
or empty token bucket (fail open).

### Method: `TaskMemoryMirror.sync_pending` {#rag-taskmemorymirror-sync_pending}

```python
async def TaskMemoryMirror.sync_pending(self, store: Any, limit: int=8, max_attempts: Optional[int]=5) -> Dict[str, int]
```

**Keywords:** task, memory, mirror, sync, pending

Drain the outbox oldest-first: upload each unsynced row and mark
it synced/failed in place. Sequential (one row at a time) so the
mirror occupies at most one run_blocking timed thread; stops early
when the mirror soft-disables.

### Class: `SemanticVerdict` {#rag-semanticverdict}

```python
class SemanticVerdict
```

**Keywords:** semantic, verdict

Outcome of the semantic evidence pass. prefers_planning=None means
undecidable — the advisor falls through to telemetry/static.

### Function: `fuse_task_evidence` {#rag-fuse_task_evidence}

```python
def fuse_task_evidence(local_rows: List[Dict[str, Any]], remote_rows: List[Dict[str, Any]], *, top_k: int, half_life_hours: float, local_weight: float, remote_weight: float, now: Optional[datetime]=None) -> List[Dict[str, Any]]
```

**Keywords:** fuse, task, evidence

Fuse local FTS candidates with remote semantic hits into one ranked
list, deduped by memory id (each memory contributes exactly once).

local_rows: get_similar_task_memories output (score = 0..1 band plus
bonuses; batch-normalized here so bonused rows correctly dominate).
remote_rows: LOCAL rows mapped from collection hits, each carrying the
raw remote score under 'remote_score' (batch-normalized here).
fused = local_weight*norm_local + remote_weight*norm_remote*recency.

### Function: `semantic_route_signal` {#rag-semantic_route_signal}

```python
def semantic_route_signal(fused: List[Dict[str, Any]], planning_model: str, coding_model: str, *, margin: float, min_evidence: int) -> SemanticVerdict
```

**Keywords:** semantic, route, signal

Fused-score-weighted success comparison of memories that ran on the
planning vs the coding model. Decidable only with >= min_evidence
matched memories AND both sides represented; a decidable verdict flips
to planning iff (planning_signal - coding_signal) >= margin.

### Function: `gather_semantic_evidence` {#rag-gather_semantic_evidence}

```python
async def gather_semantic_evidence(store: Any, prompt: str, context_id: Optional[str], planning_model: str, coding_model: str) -> Optional[SemanticVerdict]
```

**Keywords:** gather, semantic, evidence

Local candidates + at most ONE remote search, fused into a
SemanticVerdict. 30s TTL cache keyed by task hash + context (verdicts
AND misses are cached). Every failure path returns None — fail open.

### Function: `spawn_sync_task` {#rag-spawn_sync_task}

```python
def spawn_sync_task(store: Any) -> Optional['asyncio.Task']
```

**Keywords:** spawn, sync, task

Best-effort background outbox drain; returns the task or None when
skipped (mode off, drain already in flight, or no running loop). Never
raises and never blocks the caller.

### Function: `rag_cli` {#rag-rag_cli}

```python
def rag_cli(args: List[str], stream: Optional[TextIO]=None, store: Any=None) -> int
```

**Keywords:** rag, cli

Hand-rolled `rag` subcommand dispatcher (matching src/cli.py's init
pattern — no argparse). Runs against the shared store singleton unless
a store is injected (tests).

### Function: `reset_task_rag_state` {#rag-reset_task_rag_state}

```python
def reset_task_rag_state() -> None
```

**Keywords:** reset, task, rag, state

Fresh mirror/stats/caches/warn-flags — mirrors the knowledge tests'
reset_collections_state fixture so process globals never leak between
tests.

## routing.py {#routing}

### Function: `extract_routing_features` {#routing-extract_routing_features}

```python
def extract_routing_features(prompt: str, *, reason_score: int, input_messages: Optional[Sequence[Dict[str, Any]]]=None, enable_agentic: bool=True) -> Dict[str, Any]
```

**Keywords:** extract, routing, features

Return a compact prompt-free feature vector safe for telemetry.

### Function: `classify_route` {#routing-classify_route}

```python
def classify_route(*, mode: str, thinking_mode: bool, features: Dict[str, Any], borderline_prefers_planning: Optional[bool]=None) -> Tuple[str, str]
```

**Keywords:** classify, route

Choose a bounded capability class and a human-readable reason code.

### Function: `choose_model_candidate` {#routing-choose_model_candidate}

```python
def choose_model_candidate(route_class: str, *, available_models: Optional[Sequence[str]], telemetry: Sequence[Dict[str, Any]]=(), calibration: Sequence[Dict[str, Any]]=()) -> Dict[str, Any]
```

**Keywords:** choose, model, candidate

Pick one of at most three route candidates with stable hysteresis.

The first available candidate is the cold-start default.  A peer may
displace it only when both have mature calibration or telemetry and the
peer's success rate clears QUALITY_MARGIN.  This margin is the hysteresis:
ordinary noise cannot flap the route between releases or restarts.

## semantic_evals.py {#semantic_evals}

### Function: `semantic_evals_mode` {#semantic_evals-semantic_evals_mode}

```python
def semantic_evals_mode() -> str
```

**Keywords:** semantic, evals, mode

The rollout mode, defaulting to 'off'. An unknown value warns ONCE and
reads as 'off' (same rationale as rag.task_rag_mode: loud log + metrics
visibility beats aborting a shared local server).

### Function: `set_testing_override` {#semantic_evals-set_testing_override}

```python
def set_testing_override(enabled: bool) -> None
```

**Keywords:** set, testing, override

Tests only: let the sampler run despite UNI_GROK_TESTING=1.

### Function: `reset_semantic_evals_state` {#semantic_evals-reset_semantic_evals_state}

```python
def reset_semantic_evals_state() -> None
```

**Keywords:** reset, semantic, evals, state

Reset every module-level accumulator (test isolation).

### Class: `SemanticEvalVerdict` {#semantic_evals-semanticevalverdict}

```python
class SemanticEvalVerdict
```

**Keywords:** semantic, eval, verdict

Schema-enforced judge verdict (1-5 integer scales; parsed via the same
tool-free structured-parse machinery as ReflectionVerdict).

### Class: `TrajectorySample` {#semantic_evals-trajectorysample}

```python
class TrajectorySample
```

**Keywords:** trajectory, sample

In-memory judge input for one completed turn. Never persisted.

### Function: `should_sample` {#semantic_evals-should_sample}

```python
def should_sample(request_id: str, rate: float) -> bool
```

**Keywords:** should, sample

Deterministic per-request sampling: a stable hash of the request id
against the rate. No RNG state — the same request id always yields the
same verdict, so replays and tests are reproducible.

### Function: `maybe_submit_semantic_eval` {#semantic_evals-maybe_submit_semantic_eval}

```python
def maybe_submit_semantic_eval(sample: TrajectorySample, store: Any) -> Optional['asyncio.Task']
```

**Keywords:** maybe, submit, semantic, eval

Fire-and-forget judge task for a completed turn, or None when skipped.

Gate order: mode, testing flag (explicit override required under
UNI_GROK_TESTING so pytest and cassette evals stay byte-stable), gradeable
outcome, deterministic hash sample, daily judge budget.

### Function: `wait_for_pending` {#semantic_evals-wait_for_pending}

```python
async def wait_for_pending(timeout: float=10.0) -> None
```

**Keywords:** wait, for, pending

Await outstanding judge tasks (tests and shutdown).

## storage.py {#storage}

### Class: `SessionStoreProtocol` {#storage-sessionstoreprotocol}

```python
class SessionStoreProtocol
```

**Keywords:** session, store, protocol

Public async surface of a UniGrok session/telemetry store.

Structural (duck-typed) protocol: GrokSessionStore satisfies it without
inheriting from it, and tests assert conformance via isinstance (the
@runtime_checkable check verifies member presence, not signatures — the
signatures below are the documented contract).

### Function: `get_store` {#storage-get_store}

```python
def get_store(db_path: Any=None) -> SessionStoreProtocol
```

**Keywords:** get, store

Build a session store for the configured backend.

UNIGROK_STORAGE_BACKEND selects it ('sqlite' default; blank/unset reads
as sqlite). Unknown values fail fast with NotImplementedError naming the
supported set — a typo must not silently fall back to SQLite. db_path is
backend-specific (the SQLite file path; tests use per-test temp paths).

## swarm/analytics.py {#swarm-analytics}

### Function: `analyze_python_source` {#swarm-analytics-analyze_python_source}

```python
def analyze_python_source(source: str) -> Dict[str, Any]
```

**Keywords:** analyze, python, source

Return measured-only AST analytics or a structured parse error.

### Function: `add_ruff_summary` {#swarm-analytics-add_ruff_summary}

```python
async def add_ruff_summary(source: str, analytics: Dict[str, Any]) -> Dict[str, Any]
```

**Keywords:** add, ruff, summary

Attach isolated Ruff aggregate counts without returning source excerpts.

## swarm/ast_utils.py {#swarm-ast_utils}

### Function: `parse_ok` {#swarm-ast_utils-parse_ok}

```python
def parse_ok(source: bytes) -> bool
```

**Keywords:** parse, ok

Syntax filter: True when tree-sitter parses without any error node.
A syntax filter ONLY — import-time and collection failures are the
sandbox stages' job.

### Function: `extract_node_span` {#swarm-ast_utils-extract_node_span}

```python
def extract_node_span(source: bytes, focus_node: str) -> Tuple[int, int]
```

**Keywords:** extract, node, span

Resolve `focus_node` (``function:outer.inner`` or
``method:Class.method.inner``) to its exact byte span, decorators included.

Raises ValueError on: unparseable source, malformed focus spec, missing
node, or an AMBIGUOUS node (multiple same-named matches — e.g.
conditional redefinitions) — a wrong span that still passes tests would
corrupt adjacent code at apply, so ambiguity is fatal by design.

### Function: `signature_fingerprint` {#swarm-ast_utils-signature_fingerprint}

```python
def signature_fingerprint(source: bytes, focus_node: str) -> str
```

**Keywords:** signature, fingerprint

Return a stable fingerprint for the focused callable's signature.

The optimizer may change the body and decorators, but not sync/async kind
or arguments. Tests rarely exercise every valid calling convention, so a
passing suite alone is not enough to enforce this drop-in contract.

### Function: `span_line_range` {#swarm-ast_utils-span_line_range}

```python
def span_line_range(source: bytes, start: int, end: int) -> Tuple[int, int]
```

**Keywords:** span, line, range

1-based inclusive line range covered by a byte span (for coverage
intersection in the preflight oracle check).

### Function: `apply_byte_replacement` {#swarm-ast_utils-apply_byte_replacement}

```python
def apply_byte_replacement(source: bytes, start: int, end: int, replacement: bytes) -> bytes
```

**Keywords:** apply, byte, replacement

Exact byte splice: everything outside [start:end) is byte-identical.

### Function: `is_ast_identical` {#swarm-ast_utils-is_ast_identical}

```python
def is_ast_identical(original_span: bytes, replacement: bytes) -> bool
```

**Keywords:** is, ast, identical

True when the replacement parses to the SAME AST as the original span
(a formatting/comment-only no-op mutant) — evaluating it would just
re-measure the baseline, so the funnel discards it for free like a
duplicate hash. Indented method spans are dedented before parsing; any
parse failure returns False so the funnel proceeds and judges the
candidate properly (this check may only ever discard true no-ops, never
hide a real mutant).

## swarm/config.py {#swarm-config}

### Function: `validate_search_strategy` {#swarm-config-validate_search_strategy}

```python
def validate_search_strategy(value: str | None) -> str
```

**Keywords:** validate, search, strategy

Return a canonical strategy or reject an unknown caller value.

Unlike rollout mode, this is request data rather than process
configuration. Silently coercing a typo would make a run's lineage
receipt dishonest, so unknown values are errors.

### Function: `validate_primary_goal` {#swarm-config-validate_primary_goal}

```python
def validate_primary_goal(value: str | None) -> str
```

**Keywords:** validate, primary, goal

Return a canonical champion-selection goal or reject it.

### Function: `swarm_mode` {#swarm-config-swarm_mode}

```python
def swarm_mode() -> str
```

**Keywords:** swarm, mode

The rollout mode, defaulting to 'off'. Unknown values warn once and
read as 'off' (same rationale as rag.task_rag_mode / semantic_evals_mode:
a loud log plus status visibility beats aborting a shared local server).

### Function: `swarm_eval_timeout` {#swarm-config-swarm_eval_timeout}

```python
def swarm_eval_timeout() -> float
```

**Keywords:** swarm, eval, timeout

Per-candidate evaluation (tests + bench) wall-clock ceiling.

### Function: `swarm_stage_budget_fraction` {#swarm-config-swarm_stage_budget_fraction}

```python
def swarm_stage_budget_fraction() -> float
```

**Keywords:** swarm, stage, budget, fraction

Fraction of the eval timeout the preflight baseline run must fit in —
a test_target slower than this fails the task at start instead of
producing a multi-hour zombie.

### Function: `swarm_bench_repeats` {#swarm-config-swarm_bench_repeats}

```python
def swarm_bench_repeats() -> int
```

**Keywords:** swarm, bench, repeats

Measured bench repeats (an additional first warmup run is discarded).

### Function: `swarm_max_copy_mb` {#swarm-config-swarm_max_copy_mb}

```python
def swarm_max_copy_mb() -> int
```

**Keywords:** swarm, max, copy, mb

Workspace-copy size guard for the per-task sandbox.

### Function: `swarm_child_mem_mb` {#swarm-config-swarm_child_mem_mb}

```python
def swarm_child_mem_mb() -> int
```

**Keywords:** swarm, child, mem, mb

RLIMIT_AS ceiling for mutant test/bench child processes.

### Function: `swarm_stale_after_sec` {#swarm-config-swarm_stale_after_sec}

```python
def swarm_stale_after_sec() -> float
```

**Keywords:** swarm, stale, after, sec

Heartbeat staleness horizon: a running task whose row has not been
touched for this long is reported failed_stale (the runner touches
updated_at after every candidate). Deliberately derived from the eval
timeout — a healthy swarm can far exceed JobManager's global default.

### Function: `swarm_ruff_filter` {#swarm-config-swarm_ruff_filter}

```python
def swarm_ruff_filter() -> bool
```

**Keywords:** swarm, ruff, filter

UNIGROK_SWARM_RUFF_FILTER=0/false/no/off disables the $0 ruff static
fast-gate between compile() and the sandbox stages. The gate only saves
sandbox seconds — the tests stage still catches everything it would
have — so disabling it is always safe.

### Function: `reset_swarm_state` {#swarm-config-reset_swarm_state}

```python
def reset_swarm_state() -> None
```

**Keywords:** reset, swarm, state

Test isolation for module-level flags.

## swarm/fold.py {#swarm-fold}

### Function: `build_folded_state` {#swarm-fold-build_folded_state}

```python
def build_folded_state(*, goal: str, target_path: str, test_target: str, bench_command: str, candidates: List[Dict[str, Any]], front_size: int, best_delta_pct: Optional[float], generation: int) -> str
```

**Keywords:** build, folded, state

Render the swarm's working state for the next mutator prompt.

## swarm/generate.py {#swarm-generate}

### Class: `BudgetExceeded` {#swarm-generate-budgetexceeded}

```python
class BudgetExceeded
```

**Keywords:** budget, exceeded

Raised defensively if a swarm generation is non-CLI or charged.

### Function: `generate_mutation` {#swarm-generate-generate_mutation}

```python
async def generate_mutation(prompt: str, system_prompt: str, *, remaining_budget_usd: float, session: Optional[str]=None) -> GenerationResult
```

**Keywords:** generate, mutation

One toolless completion, strictly on the CLI subscription plane.

## swarm/mutators.py {#swarm-mutators}

### Function: `parse_mutation_output` {#swarm-mutators-parse_mutation_output}

```python
def parse_mutation_output(raw: str) -> Optional[str]
```

**Keywords:** parse, mutation, output

Extract the raw replacement source from a model response, or None when
the contract is violated (empty, or clearly prose rather than code).

Strips a single accidental markdown fence; does NOT attempt to salvage
multi-block or explanatory output — that routes to the one heal retry.

## swarm/pareto.py {#swarm-pareto}

### Function: `dominates` {#swarm-pareto-dominates}

```python
def dominates(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool
```

**Keywords:** dominates

a dominates b (minimization): no worse on all objectives, strictly
better on at least one.

### Function: `fast_non_dominated_sort` {#swarm-pareto-fast_non_dominated_sort}

```python
def fast_non_dominated_sort(points: Sequence[Tuple[float, ...]]) -> List[List[int]]
```

**Keywords:** fast, non, dominated, sort

Return fronts as lists of indices; front 0 is the Pareto-optimal set.
O(MN^2) — fine for the tens-of-candidates scale here.

### Function: `crowding_distance` {#swarm-pareto-crowding_distance}

```python
def crowding_distance(points: Sequence[Tuple[float, ...]], indices: Sequence[int]) -> Dict[int, float]
```

**Keywords:** crowding, distance

NSGA-II crowding distance within one front. Boundary points get
infinity; interior points get the summed normalized neighbor gap.

### Function: `rank_candidates` {#swarm-pareto-rank_candidates}

```python
def rank_candidates(candidates: List[Dict[str, Any]], objectives: Sequence[str]=OBJECTIVES) -> List[Dict[str, Any]]
```

**Keywords:** rank, candidates

Annotate FEASIBLE candidates in place with pareto_rank (0 = optimal
front) and crowding, and return them ordered (rank asc, crowding desc).
Infeasible candidates are dropped — they are not selectable.

### Function: `select_champion` {#swarm-pareto-select_champion}

```python
def select_champion(candidates: List[Dict[str, Any]], primary_goal: str='balanced') -> Dict[str, Any] | None
```

**Keywords:** select, champion

Choose one deterministic CTA candidate from the current Pareto front.

This does not alter Pareto membership. It only turns a multi-objective
front into the single "Best verified candidate" action requested by the
UI, with candidate id as the final stable tie-break.

## swarm/preflight.py {#swarm-preflight}

### Class: `PreflightError` {#swarm-preflight-preflighterror}

```python
class PreflightError
```

**Keywords:** preflight, error

A refusal with a user-actionable reason; partial oracle facts ride
the .oracle attribute so status can show how far preflight got.

### Function: `module_name_for` {#swarm-preflight-module_name_for}

```python
def module_name_for(target_rel: str) -> str
```

**Keywords:** module, name, for

Dotted module name for a workspace-relative path, tolerating the
src/ layout. Non-standard layouts fail the provenance probe loudly
rather than guessing.

### Function: `noise_floor_pct` {#swarm-preflight-noise_floor_pct}

```python
def noise_floor_pct(latency_samples: List[float]) -> float
```

**Keywords:** noise, floor, pct

max(5%, 3σ relative to the median) — improvements below this are
treated as zero everywhere (bench numbers, rewards, deltas).

## swarm/router.py {#swarm-router}

### Class: `DiscountedUCBRouter` {#swarm-router-discounteducbrouter}

```python
class DiscountedUCBRouter
```

**Keywords:** discounted, ucb, router

Per-task bandit over the four mutator arms.

### Method: `DiscountedUCBRouter.select` {#swarm-router-discounteducbrouter-select}

```python
def DiscountedUCBRouter.select(self, generation: int) -> Dict[str, object]
```

**Keywords:** discounted, ucb, router, select

Return {'arm', 'receipt'} for the next mutant slot.

### Method: `DiscountedUCBRouter.update` {#swarm-router-discounteducbrouter-update}

```python
def DiscountedUCBRouter.update(self, arm: str, reward: float) -> None
```

**Keywords:** discounted, ucb, router, update

Discount every arm, then credit the pulled arm's reward — standard
discounted UCB bookkeeping (non-stationary: the folded prompt context
shifts each generation, so recent evidence should dominate).

## swarm/runner.py {#swarm-runner}

### Function: `is_stale` {#swarm-runner-is_stale}

```python
def is_stale(row: Dict[str, Any], stale_after_sec: float) -> bool
```

**Keywords:** is, stale

A running/queued task whose heartbeat is older than the horizon — the
process that owned its asyncio task almost certainly died.

### Function: `effective_status` {#swarm-runner-effective_status}

```python
def effective_status(row: Dict[str, Any]) -> str
```

**Keywords:** effective, status

Status a reader should see: failed_stale overrides a stuck row.

### Method: `SwarmRunner.cancel` {#swarm-runner-swarmrunner-cancel}

```python
def SwarmRunner.cancel(self, task_id: str) -> None
```

**Keywords:** swarm, runner, cancel

Cooperative cancel — the engine checks between candidates.

### Method: `SwarmRunner.wait` {#swarm-runner-swarmrunner-wait}

```python
async def SwarmRunner.wait(self, task_id: str, timeout: float=30.0) -> bool
```

**Keywords:** swarm, runner, wait

Wait for an in-process task and report whether it completed.

Returning a boolean keeps timeout distinct from completion. A missing
task is already outside this runner's active set; callers must still
read the durable row for its terminal status.

### Method: `SwarmRunner.shutdown` {#swarm-runner-swarmrunner-shutdown}

```python
async def SwarmRunner.shutdown(self) -> None
```

**Keywords:** swarm, runner, shutdown

Hard-cancel and drain every in-process task during CLI shutdown.

## swarm/sandbox.py {#swarm-sandbox}

### Class: `SandboxError` {#swarm-sandbox-sandboxerror}

```python
class SandboxError
```

**Keywords:** sandbox, error

Sandbox setup or evaluation infrastructure failure (not a candidate
verdict): copy guard trips, missing target, malformed bench output.

### Function: `parse_bench_line` {#swarm-sandbox-parse_bench_line}

```python
def parse_bench_line(stdout: str) -> Optional[Dict[str, float]]
```

**Keywords:** parse, bench, line

Extract the single `SWARM_BENCH {...}` contract line; None when the
contract is not met (missing, duplicated, or malformed).

### Method: `SwarmSandbox.create` {#swarm-sandbox-swarmsandbox-create}

```python
def SwarmSandbox.create(self) -> None
```

**Keywords:** swarm, sandbox, create

Copy the workspace (bounded, byte-exact, symlink-safe) and link
the original venv in.

### Method: `SwarmSandbox.hygiene` {#swarm-sandbox-swarmsandbox-hygiene}

```python
def SwarmSandbox.hygiene(self) -> None
```

**Keywords:** swarm, sandbox, hygiene

Per-candidate cleanup so one mutant's cache pollution can't skew
the next one's feasibility or bench numbers.

### Method: `SwarmSandbox.run_child` {#swarm-sandbox-swarmsandbox-run_child}

```python
async def SwarmSandbox.run_child(self, argv: List[str], timeout: float) -> Tuple[int, str, str]
```

**Keywords:** swarm, sandbox, run, child

Run one untrusted child: own session, RLIMITs, allowlisted env,
process-group SIGKILL on timeout (rc -9).

### Method: `SwarmSandbox.run_bench` {#swarm-sandbox-swarmsandbox-run_bench}

```python
async def SwarmSandbox.run_bench(self, bench_argv: List[str], repeats: int, timeout: float) -> Dict[str, Any]
```

**Keywords:** swarm, sandbox, run, bench

1 discarded warmup + `repeats` measured runs of the SWARM_BENCH
contract command; medians + raw samples (for noise-floor math).
Raises SandboxError when the command fails or breaks the contract —
for the BASELINE that fails the task; for a mutant the engine treats
it as an infeasible candidate at the bench stage.

## swarm/static_gate.py {#swarm-static_gate}

### Function: `ruff_bin` {#swarm-static_gate-ruff_bin}

```python
def ruff_bin() -> Optional[str]
```

**Keywords:** ruff, bin

The venv's ruff first (a pinned project dependency), PATH second.

### Function: `violation_counts` {#swarm-static_gate-violation_counts}

```python
async def violation_counts(source: bytes, timeout: float=10.0) -> Optional[ViolationCounts]
```

**Keywords:** violation, counts

F821/F823 diagnostics keyed by rule and message, or None when the gate cannot
run (ruff missing, timeout, or internal error) — callers must treat None
as gate-disabled, never as clean.

### Function: `count_violations` {#swarm-static_gate-count_violations}

```python
async def count_violations(source: bytes, timeout: float=10.0) -> Optional[int]
```

**Keywords:** count, violations

Compatibility helper returning the total F821/F823 diagnostic count.

## swarm/transforms.py {#swarm-transforms}

### Function: `deterministic_transforms` {#swarm-transforms-deterministic_transforms}

```python
def deterministic_transforms(source: str) -> List[Tuple[str, str]]
```

**Keywords:** deterministic, transforms

Return unique named rewrites of one definition, in registry order.

## tools/chats.py {#tools-chats}

### Class: `GrokReflectionResult` {#tools-chats-grokreflectionresult}

```python
class GrokReflectionResult
```

**Keywords:** grok, reflection, result

Schema for a focused, tool-free Grok critique.

### Function: `agent` {#tools-chats-agent}

```python
async def agent(task: str, session: Optional[str]=None, mode: Literal['auto', 'fast', 'reasoning', 'thinking', 'research']='auto', model: Optional[str]=None, require_reasoning_level: Optional[Literal['low', 'medium', 'high']]=None, plane: Literal['auto', 'cli', 'api']='auto', fallback_policy: Literal['same_plane', 'cross_plane']='cross_plane', ctx: Optional[Context]=None) -> AgentResult
```

**Keywords:** agent

Run the unified UniGrok agent on any task. This is the headline entry
point — use it by default for anything nontrivial instead of picking a
specialized tool.

It auto-routes across Grok models (planning model for reasoning-heavy
tasks, coding model otherwise), gives the model its full action space on
every request — xAI server-side web search, X search, and sandboxed code
execution plus local file, git, and test tools — and lets the model decide
for itself whether to act. Pass a session name and it remembers prior
turns, including tool observations, so multi-step work continues across
calls. When the client requests progress (MCP progressToken), depth and
tool progress is reported live via the injected FastMCP context.

Args:
    task: The goal, question, or task for the agent.
    session: Optional session name. Persists conversation history and tool
        traces so later calls can continue the work.
    mode: `"auto"` (default) self-routes; `"fast"` forces a single toolless
        completion for trivial prompts; `"reasoning"` pins the planning
        model; `"thinking"` runs the agent loop plus a schema-enforced
        reflection review for the hardest tasks (slowest, most expensive);
        `"research"` uses the catalog's multi-agent-capable research model
        (agent_count from UNIGROK_RESEARCH_AGENT_COUNT, 4 or 16) with
        inline citations requested — sources come back under `citations`.
    model: Optional Grok model id. Leave unset to let routing choose.
    require_reasoning_level: Minimum required Grok reasoning level (low, medium, high).
    plane: Starting credential plane. `auto` follows server policy; `cli`
        starts on the SuperGrok subscription; `api` starts on the metered
        developer API.
    fallback_policy: `same_plane` forbids crossing the billing boundary;
        `cross_plane` permits bounded recovery on the other xAI plane.

Returns:
    AgentResult containing execution metadata and responses.

### Function: `chat` {#tools-chats-chat}

```python
async def chat(prompt: str, session: Optional[str]=None, model: str='grok-build-0.1', system_prompt: Optional[str]=None, agent_count: Optional[int]=None, enable_agentic: bool=True, require_reasoning_level: Optional[Literal['low', 'medium', 'high']]=None) -> ChatResult
```

**Keywords:** chat

Send a text prompt to a Grok model and return its reply.

Absorbs the old `agentic_chat` tool: the ReAct AgentLoop is now the
default route, so the model has its tool surface and self-directs.
Set `enable_agentic=False` to force a single toolless completion.

Args:
    prompt: User message to send to the model.
    session: Optional session name. Persists conversation history.
    model: Grok model id (defaults to `grok-build-0.1`).
    system_prompt: Optional system instruction prepended to the conversation.
    agent_count: 4 or 16. Only valid with `grok-4.20-multi-agent`.
    enable_agentic: If True (default), runs through the ReAct AgentLoop.
    require_reasoning_level: Minimum required Grok reasoning level (low, medium, high).

### Function: `grok_agent` {#tools-chats-grok_agent}

```python
async def grok_agent(prompt: str, session: Optional[str]=None, model: str=DEFAULT_PLANNING_MODEL, system_prompt: Optional[str]=None, max_iterations: int=5, cost_limit: float=0.5) -> AgentResult
```

**Keywords:** grok, agent

Unified @grok Entry Point: run the thinking route — the ReAct AgentLoop
wrapped in a schema-enforced reflection loop — with explicit retry and
budget caps.

Args:
    prompt: Task or question for the agent.
    session: Optional session name for persistent history in chats.
    model: Grok model id (default `grok-4.5`).
    system_prompt: Optional system instruction prepended to the conversation.
    max_iterations: Strict cap on reviewer-driven correction retries (default 5).
    cost_limit: Total budget in USD before hard abort (default 0.50).

### Function: `grok_reflect` {#tools-chats-grok_reflect}

```python
async def grok_reflect(subject: str, criteria: Optional[str]=None, context: Optional[str]=None, model: str=DEFAULT_PLANNING_MODEL) -> ReflectionResult
```

**Keywords:** grok, reflect

Run a structured, tool-free Grok review of an artifact or plan.

Use this when a client needs a deterministic critique shape rather than a
full agent run. It calls xAI structured outputs through the shared
`_parse_structured` helper, so the reflection pass cannot invoke local
tools and degrades explicitly if structured parsing is unavailable.

### Function: `stateful_chat` {#tools-chats-stateful_chat}

```python
async def stateful_chat(prompt: str, model: str=DEFAULT_PLANNING_MODEL, response_id: Optional[str]=None, system_prompt: Optional[str]=None) -> ChatResult
```

**Keywords:** stateful, chat

Continue a server-side stored conversation using xAI's stateful chat.

Args:
    prompt: User message to append.
    model: Grok model id (default `grok-4.5`).
    response_id: ID of the previous response to continue from.
    system_prompt: Optional system instruction.

Returns:
    ChatResult containing execution metadata and responses.

### Function: `retrieve_stateful_response` {#tools-chats-retrieve_stateful_response}

```python
async def retrieve_stateful_response(response_id: str) -> str
```

**Keywords:** retrieve, stateful, response

Fetch a stored chat completion from xAI by its response ID.

Args:
    response_id: ID returned by a prior `stateful_chat` call.

### Function: `delete_stateful_response` {#tools-chats-delete_stateful_response}

```python
async def delete_stateful_response(response_id: str) -> str
```

**Keywords:** delete, stateful, response

Delete a stored chat completion from xAI's servers.

Args:
    response_id: ID of the stored response to remove.

### Function: `chat_with_vision` {#tools-chats-chat_with_vision}

```python
async def chat_with_vision(prompt: str, session: Optional[str]=None, model: str=DEFAULT_PLANNING_MODEL, image_paths: Optional[List[str]]=None, image_urls: Optional[List[str]]=None, detail: str='auto') -> ChatResult
```

**Keywords:** chat, with, vision

Analyze one or more images with a Grok vision model.

Args:
    prompt: Question or instruction about the image(s).
    session: Optional session name for persistent history in chats.
    model: Vision-capable Grok model (default `grok-4.5`).
    image_paths: Local image file paths to analyze.
    image_urls: Public image URLs to analyze.
    detail: Image detail level. One of `"auto"`, `"low"`, or `"high"`.

### Function: `chat_with_files` {#tools-chats-chat_with_files}

```python
async def chat_with_files(prompt: str, file_ids: List[str], session: Optional[str]=None, model: str=DEFAULT_PLANNING_MODEL, system_prompt: Optional[str]=None) -> ChatResult
```

**Keywords:** chat, with, files

Chat with Grok using one or more previously uploaded files as context.

Args:
    prompt: Question or instruction about the attached files.
    file_ids: IDs returned by `xai_upload_file`.
    session: Optional session name for persistent local history.
    model: Grok model id (default `grok-4.5`).
    system_prompt: Optional system instruction prepended to the conversation.

### Function: `raw_get_session_history` {#tools-chats-raw_get_session_history}

```python
async def raw_get_session_history(session: str) -> str
```

**Keywords:** raw, get, session, history

Get local chat history for a session — lets agent recall prior context.

## tools/faq.py {#tools-faq}

### Function: `lookup_unigrok_faq` {#tools-faq-lookup_unigrok_faq}

```python
async def lookup_unigrok_faq(query: str, limit: int=3) -> str
```

**Keywords:** lookup, unigrok, faq

Retrieve verified FAQ context only for an explicit UniGrok help request.

This local, zero-cost lookup never calls xAI or reads user/session memory.
Use it when the user asks about UniGrok setup, IDE configuration, routing,
security, health checks, or troubleshooting. Do not call it for a general
question that merely happens to mention a word such as "port" or "Cursor".
If no entry applies, continue with normal reasoning instead of forcing an
FAQ answer.

Args:
    query: The user's clearly UniGrok-specific support question.
    limit: Maximum matches to return (1-10, default 3).

## tools/git.py {#tools-git}

### Function: `git_status` {#tools-git-git_status}

```python
async def git_status(repo_path: Optional[str]=None) -> str
```

**Keywords:** git, status

Return `git status --porcelain` for the current repository.

### Function: `git_diff` {#tools-git-git_diff}

```python
async def git_diff(cached: bool=False, path: Optional[str]=None, repo_path: Optional[str]=None) -> str
```

**Keywords:** git, diff

Return the current git diff, optionally for staged changes or one path.

### Function: `git_log` {#tools-git-git_log}

```python
async def git_log(limit: int=10, repo_path: Optional[str]=None) -> str
```

**Keywords:** git, log

Return a short one-line git history.

### Function: `git_show` {#tools-git-git_show}

```python
async def git_show(commit: str='HEAD', repo_path: Optional[str]=None) -> str
```

**Keywords:** git, show

Return `git show` for a validated commit-ish ref.

### Function: `git_current_branch` {#tools-git-git_current_branch}

```python
async def git_current_branch(repo_path: Optional[str]=None) -> str
```

**Keywords:** git, current, branch

Return the active branch name.

### Function: `git_create_branch` {#tools-git-git_create_branch}

```python
async def git_create_branch(branch_name: str, repo_path: Optional[str]=None) -> str
```

**Keywords:** git, create, branch

Create and switch to a new branch. Requires local git write mode.

### Function: `git_apply_patch` {#tools-git-git_apply_patch}

```python
async def git_apply_patch(patch: str, repo_path: Optional[str]=None) -> str
```

**Keywords:** git, apply, patch

Apply a unified diff patch. Requires local git write mode.

### Function: `git_commit` {#tools-git-git_commit}

```python
async def git_commit(message: str, paths: List[str], repo_path: Optional[str]=None) -> str
```

**Keywords:** git, commit

Commit explicit paths only. Requires local git write mode.

## tools/knowledge.py {#tools-knowledge}

### Function: `remember_fact` {#tools-knowledge-remember_fact}

```python
async def remember_fact(fact: str, scope: str='global') -> Dict[str, Any]
```

**Keywords:** remember, fact

Save one durable fact to the local workspace knowledge memory.

Facts are distilled knowledge — decisions, constraints, preferences,
verified findings — injected as hints into future prompts that match
them. Saving an identical fact again touches the existing row instead of
duplicating it.

Args:
    fact: One self-contained sentence with concrete specifics.
    scope: 'global' (default, injected everywhere) or a session name for
        session-scoped knowledge.

### Function: `search_knowledge` {#tools-knowledge-search_knowledge}

```python
async def search_knowledge(query: str, limit: int=5) -> Dict[str, Any]
```

**Keywords:** search, knowledge

Search the workspace knowledge memory for facts matching a query.

Local results are ranked by FTS5 bm25 when available (term-overlap
otherwise). With UNIGROK_COLLECTIONS=1 and a capable SDK, matches from
the xAI knowledge collection are merged in (origin='collection').

Args:
    query: Search terms.
    limit: Maximum number of local facts to return (1-25, default 5).

### Function: `forget_fact` {#tools-knowledge-forget_fact}

```python
async def forget_fact(fact_id: int) -> Dict[str, Any]
```

**Keywords:** forget, fact

Permanently delete one fact from the workspace knowledge memory.

Args:
    fact_id: The id returned by `remember_fact` or `search_knowledge`.

### Function: `distill_session` {#tools-knowledge-distill_session}

```python
async def distill_session(session: str, ctx: Optional[Context]=None) -> Dict[str, Any]
```

**Keywords:** distill, session

Distill a chat session's stored history into durable knowledge facts.

Submits a background job (same lifecycle as research jobs — poll
`get_research_job(job_id)`) that summarizes the session into 3-8
standalone facts on the cheap coding model and saves them to the
knowledge memory with source='session:<name>'.

Args:
    session: Name of a stored chat session.

## tools/media.py {#tools-media}

### Function: `generate_image` {#tools-media-generate_image}

```python
async def generate_image(prompt: str, model: str='grok-imagine-image', image_paths: Optional[List[str]]=None, image_urls: Optional[List[str]]=None, n: int=1, image_format: str='url', aspect_ratio: Optional[str]=None, resolution: Optional[str]=None) -> MediaResult
```

**Keywords:** generate, image

Generate new images or edit existing ones with Grok Imagine.

Args:
    prompt: Image description or edit instruction.
    model: Image model (`grok-imagine-image` or `grok-imagine-image-pro`).
    image_paths: Local image files used as edit sources or references.
    image_urls: Public image URLs used as edit sources or references.
    n: Number of images to generate (1–10).
    image_format: `"url"` (default) or `"base64"`.
    aspect_ratio: Aspect ratio like `"16:9"`, `"1:1"`, or `"9:16"`.
    resolution: `"1k"` or `"2k"`.

Returns:
    MediaResult containing image metadata and URLs.

### Function: `generate_video` {#tools-media-generate_video}

```python
async def generate_video(prompt: str, model: str='grok-imagine-video', image_path: Optional[str]=None, image_url: Optional[str]=None, video_path: Optional[str]=None, video_url: Optional[str]=None, reference_image_paths: Optional[List[str]]=None, reference_image_urls: Optional[List[str]]=None, duration: Optional[int]=None, aspect_ratio: Optional[str]=None, resolution: Optional[str]=None) -> MediaResult
```

**Keywords:** generate, video

Generate or edit videos with Grok Imagine.

Args:
    prompt: Video description, or the edit instruction for video editing.
    model: Video model (default `grok-imagine-video`).
    image_path: Local image to use as the starting frame.
    image_url: Public image URL to use as the starting frame.
    video_path: Local video to edit (max 20 MB, .mp4, ≤ 8.7s).
    video_url: Public video URL to edit (.mp4, ≤ 8.7s).
    reference_image_paths: Local images used as style/subject references.
    reference_image_urls: Public image URLs used as style/subject references.
    duration: Video length in seconds (1–15, ignored when editing).
    aspect_ratio: Aspect ratio like `"16:9"` or `"9:16"`.
    resolution: `"480p"` or `"720p"`.

### Function: `extend_video` {#tools-media-extend_video}

```python
async def extend_video(prompt: str, video_url: str, model: str='grok-imagine-video', duration: Optional[int]=None) -> MediaResult
```

**Keywords:** extend, video

Extend an existing video with a follow-up prompt.

Args:
    prompt: What should happen in the extended segment.
    video_url: Public URL of the source video (.mp4, 2–15 s).
    model: Video model (default `grok-imagine-video`).
    duration: Length of the extension in seconds (2–10, default 6).

## tools/research.py {#tools-research}

### Function: `submit_research_job` {#tools-research-submit_research_job}

```python
async def submit_research_job(prompt: str, model: Optional[str]=None, agent_count: Optional[int]=None, ctx: Optional[Context]=None) -> Dict[str, Any]
```

**Keywords:** submit, research, job

Submit a long-running research task as a deferred xAI job and return
immediately. The job runs in the background with xAI's server-side web
search, X search, and code-execution tools attached; poll
`get_research_job(job_id)` for the result.

Args:
    prompt: The research question or task.
    model: Optional Grok model id. Leave unset to use the planning model.
    agent_count: Optional multi-agent fan-out — only 4 or 16 are accepted.

Returns:
    A dict with `job_id` (pass it to `get_research_job`), `status`
    (`"queued"`), and the resolved `model`.

### Function: `get_research_job` {#tools-research-get_research_job}

```python
async def get_research_job(job_id: str) -> Dict[str, Any]
```

**Keywords:** get, research, job

Fetch the status and result of a deferred research job.

Statuses: `queued`/`running` (in flight), `done` (`result` and `cost_usd`
present), `error` (`error` present), `not_found`, or `stale` — a
queued/running job whose `updated_at` is older than
UNIGROK_JOB_TIMEOUT_SEC, meaning the task that owned it did not survive a
server restart and the job will never finish on its own.

Args:
    job_id: ID returned by `submit_research_job`.

### Function: `list_research_jobs` {#tools-research-list_research_jobs}

```python
async def list_research_jobs(limit: int=20) -> Dict[str, Any]
```

**Keywords:** list, research, jobs

List the most recent deferred research jobs, newest first.

Args:
    limit: Maximum number of jobs to return (clamped to 1-100, default 20).

## tools/resources.py {#tools-resources}

### Function: `register_resource_primitives` {#tools-resources-register_resource_primitives}

```python
def register_resource_primitives(mcp: FastMCP)
```

**Keywords:** register, resource, primitives

Register the grok:// resources and the reusable prompts.

## tools/swarm.py {#tools-swarm}

### Function: `analyze_code_for_swarm` {#tools-swarm-analyze_code_for_swarm}

```python
async def analyze_code_for_swarm(code: str, language: str='python') -> str
```

**Keywords:** analyze, code, for, swarm

Analyze pasted Python without a model call, import, or user-code execution.

The source is capped at 256 KiB, read only from this request, and never
persisted. Cloud Run refuses this server-side tool because the public
page performs its preview entirely in the browser.

### Function: `start_code_swarm` {#tools-swarm-start_code_swarm}

```python
async def start_code_swarm(target_path: str, focus_node: str, test_target: str, bench_command: str, budget_usd: Optional[float]=None, allow_unstable_bench: bool=False, search_strategy: str='baseline_batch', primary_goal: str='balanced') -> str
```

**Keywords:** start, code, swarm

Launch a swarm that searches rewrites of ONE focus function for
latency/memory wins verified by your tests. Returns a task id to poll with
get_swarm_status. focus_node is 'function:<name>' or 'method:<Class>.<name>';
test_target and bench_command define the correctness oracle and the
benchmark (the command must print a single SWARM_BENCH JSON line —
scripts/swarm_bench.py is the easy path).

### Function: `start_paste_swarm` {#tools-swarm-start_paste_swarm}

```python
async def start_paste_swarm(code: str, test_code: str, bench_code: str, focus_node: str, budget_usd: Optional[float]=None, allow_unstable_bench: bool=False, search_strategy: str='elite_offspring', primary_goal: str='balanced') -> str
```

**Keywords:** start, paste, swarm

Run a verified local swarm over pasted Python, tests, and benchmark.

Source material is written only to a task-scoped local scratch directory.
Tests and a benchmark are mandatory; examples never count as proof. Paste
tasks return copyable champions but cannot use workspace Apply.

### Function: `get_swarm_status` {#tools-swarm-get_swarm_status}

```python
async def get_swarm_status(task_id: str, view: Literal['text', 'json']='text') -> str
```

**Keywords:** get, swarm, status

Report a swarm's status, the oracle-honesty facts (focus-span coverage,
bench stability), the current Pareto front with relative deltas, and
spend. ``view="json"`` returns the stable machine-readable payload
(format ``unigrok-swarm-status-v2``) that the local workbench and any
static export consume — one call renders the whole run.

The JSON schema is deliberately honest: it carries ONLY measured values.
No ``instructions_retired``/``allocated_blocks`` (hardware counters are
the OptiBench harness's domain, not measurable on this stack), no
``semantic_*`` scores (no judge exists in the v1 funnel by contract), and
no invented cost comparisons — ``aggregates`` are computed from the same
SQLite rows the text view reads.

### Function: `apply_swarm_winner` {#tools-swarm-apply_swarm_winner}

```python
async def apply_swarm_winner(candidate_id: str) -> str
```

**Keywords:** apply, swarm, winner

Splice a winning candidate into the live workspace file (contributor +
active mode only). Guarded by a base_file_hash staleness check and
post-apply re-verification: if the file changed since the swarm ran, or the
candidate breaks the tests, the original bytes are restored and nothing
lands. Never commits — that stays with you.

### Function: `cancel_swarm` {#tools-swarm-cancel_swarm}

```python
async def cancel_swarm(task_id: str) -> str
```

**Keywords:** cancel, swarm

Cooperatively cancel a running swarm; the partial Pareto front is kept.

### Function: `list_swarm_tasks` {#tools-swarm-list_swarm_tasks}

```python
async def list_swarm_tasks(limit: int=10) -> str
```

**Keywords:** list, swarm, tasks

List recent swarm tasks newest-first as a JSON array (id, effective
status incl. staleness override, target, focus node, generations run,
spend). The Playground's task picker consumes this — read-only, no gate:
on a service that never ran a swarm it simply returns [].

## tools/system.py {#tools-system}

### Function: `grok_mcp_status` {#tools-system-grok_mcp_status}

```python
async def grok_mcp_status(view: Literal['text', 'json']='text') -> str
```

**Keywords:** grok, mcp, status

Inspect health and usage; ``view=json`` returns stable structured metrics.

### Function: `list_chat_sessions` {#tools-system-list_chat_sessions}

```python
async def list_chat_sessions() -> str
```

**Keywords:** list, chat, sessions

List all chat sessions stored under the SQLite session store.

### Function: `get_chat_history` {#tools-system-get_chat_history}

```python
async def get_chat_history(session: str='default', limit: int=20) -> str
```

**Keywords:** get, chat, history

Return the most recent messages for a local chat session from SQLite.

### Function: `clear_chat_history` {#tools-system-clear_chat_history}

```python
async def clear_chat_history(session: str='default') -> str
```

**Keywords:** clear, chat, history

Delete the history mapping and cascades messages for a chat session.

### Function: `list_models` {#tools-system-list_models}

```python
async def list_models() -> List[str]
```

**Keywords:** list, models

List live xAI API model IDs. Lightweight, direct, and fast.

### Function: `list_models_detailed` {#tools-system-list_models_detailed}

```python
async def list_models_detailed() -> str
```

**Keywords:** list, models, detailed

List xAI API models, local Grok CLI models, and `.grok` model profiles separately.

### Function: `xai_upload_file` {#tools-system-xai_upload_file}

```python
async def xai_upload_file(file_path: str) -> Dict[str, Any]
```

**Keywords:** xai, upload, file

Upload a local project file to xAI's servers so it can be reference-attached in chats.

Returns:
    A dict with `file_id` (pass it to `chat_with_files`/`xai_get_file_content`),
    `filename`, `size_bytes`, and a human-readable `summary`.

### Function: `xai_list_files` {#tools-system-xai_list_files}

```python
async def xai_list_files() -> str
```

**Keywords:** xai, list, files

List all files uploaded to xAI from this account.

### Function: `xai_get_file` {#tools-system-xai_get_file}

```python
async def xai_get_file(file_id: str) -> str
```

**Keywords:** xai, get, file

Retrieve metadata of a file uploaded to xAI.

### Function: `xai_get_file_content` {#tools-system-xai_get_file_content}

```python
async def xai_get_file_content(file_id: str, max_bytes: int=500000) -> str
```

**Keywords:** xai, get, file, content

Download the raw content of an uploaded file from xAI.

### Function: `xai_delete_file` {#tools-system-xai_delete_file}

```python
async def xai_delete_file(file_id: str) -> str
```

**Keywords:** xai, delete, file

Delete an uploaded file from xAI.

### Function: `read_local_file` {#tools-system-read_local_file}

```python
async def read_local_file(file_path: str, max_chars: int=500000) -> str
```

**Keywords:** read, local, file

Read a local project workspace file for code context or diagnostics.

### Function: `list_project_files` {#tools-system-list_project_files}

```python
async def list_project_files(extensions: Optional[str]=None, max_results: int=200) -> str
```

**Keywords:** list, project, files

List source code and config files present in the current workspace.

### Function: `remote_code_execution` {#tools-system-remote_code_execution}

```python
async def remote_code_execution(prompt: str, max_turns: Optional[int]=None) -> SystemResult
```

**Keywords:** remote, code, execution

Solve a task by letting Grok write and run Python in xAI's server-side sandbox.

Renamed from `code_executor` — it invokes xAI's remote `code_execution`
tool; no code runs on this machine.

### Function: `run_local_tests` {#tools-system-run_local_tests}

```python
async def run_local_tests(target: str='tests', max_seconds: int=60, max_output_chars: int=12000) -> str
```

**Keywords:** run, local, tests

Run local pytest verification without exposing arbitrary shell execution.

### Function: `web_search` {#tools-system-web_search}

```python
async def web_search(prompt: str, allowed_domains: Optional[List[str]]=None, excluded_domains: Optional[List[str]]=None) -> SystemResult
```

**Keywords:** web, search

Query the web using xAI's real-time web search tool.

Args:
    prompt: The research question or search instruction.
    allowed_domains: Restrict search to these domains (e.g. `["arxiv.org"]`).
    excluded_domains: Domains to exclude from search results.

### Function: `x_search` {#tools-system-x_search}

```python
async def x_search(prompt: str, allowed_x_handles: Optional[List[str]]=None, from_date: Optional[str]=None, to_date: Optional[str]=None) -> SystemResult
```

**Keywords:** x, search

Query X posts and profiles using xAI's real-time X search tool.

Args:
    prompt: The search question or instruction.
    allowed_x_handles: Restrict search to posts from these X handles.
    from_date: Earliest post date, ISO format (e.g. `"2026-06-01"`).
    to_date: Latest post date, ISO format (e.g. `"2026-07-01"`).

### Function: `db_vacuum` {#tools-system-db_vacuum}

```python
async def db_vacuum() -> str
```

**Keywords:** db, vacuum

Perform database compacting and optimization (VACUUM).

### Function: `load_okf_manifest` {#tools-system-load_okf_manifest}

```python
def load_okf_manifest() -> dict[str, Any]
```

**Keywords:** load, okf, manifest

Load and validate the packaged OKF manifest as the discovery source of truth.

### Function: `grok_mcp_discover_self` {#tools-system-grok_mcp_discover_self}

```python
async def grok_mcp_discover_self(include_models: bool=False) -> SystemResult
```

**Keywords:** grok, mcp, discover, self

Exposes OKF bundle information, WebMCP manifests, and tool schemas for zero-configuration agent onboarding.

### Function: `grok_mcp_restart_container` {#tools-system-grok_mcp_restart_container}

```python
async def grok_mcp_restart_container() -> SystemResult
```

**Keywords:** grok, mcp, restart, container

Safely restart the UniGrok Docker container by executing docker compose up --build -d.
Only works if running in a context where docker compose is available and enabled.

## tools/workspace_memory.py {#tools-workspace_memory}

### Function: `recall_workspace_memory` {#tools-workspace_memory-recall_workspace_memory}

```python
async def recall_workspace_memory(query: str, head_sha: str, changed_paths: Optional[List[str]]=None, limit: int=3) -> Dict[str, Any]
```

**Keywords:** recall, workspace, memory

Recall engineering evidence relevant to one specific local checkout.

The caller must supply its own full Git HEAD because the shared Docker MCP
sees the main checkout and cannot infer an IDE's hidden task worktree.

Args:
    query: Current engineering task or question.
    head_sha: Full 40-character commit id checked out by the calling agent.
    changed_paths: Repository-relative paths currently in scope.
    limit: Maximum evidence cards to return (1-10, default 3).

### Function: `record_landed_outcome` {#tools-workspace_memory-record_landed_outcome}

```python
async def record_landed_outcome(landed_sha: str, summary: str, kind: str='decision', paths: Optional[List[str]]=None, symbols: Optional[List[str]]=None, confidence: float=0.8, supersedes: Optional[List[str]]=None, task_memory_ids: Optional[List[int]]=None, ctx: Optional[Context]=None) -> Dict[str, Any]
```

**Keywords:** record, landed, outcome

Record one engineering outcome after ``scripts/land`` succeeds.

The server verifies that ``landed_sha`` is reachable from local main and
has a matching passing landing receipt. SQLite commits first; compact Git
Notes mirroring is best-effort through a durable outbox.

Args:
    landed_sha: Full commit id printed by ``scripts/land``.
    summary: Concise decision, constraint, failure lesson, or workaround.
    kind: decision, invariant, workaround, failure, observation, or routing.
    paths: Repository-relative files affected; defaults to receipt paths.
    symbols: Optional functions/classes/config keys in scope.
    confidence: Evidence confidence from 0.0 to 1.0.
    supersedes: Older evidence ids explicitly invalidated by this outcome.
    task_memory_ids: Optional routing task-memory rows linked as provenance.

### Function: `explain_workspace_evidence` {#tools-workspace_memory-explain_workspace_evidence}

```python
async def explain_workspace_evidence(evidence_id: str, head_sha: str) -> Dict[str, Any]
```

**Keywords:** explain, workspace, evidence

Explain provenance and current-head applicability for one evidence card.

### Function: `workspace_memory_status` {#tools-workspace_memory-workspace_memory_status}

```python
async def workspace_memory_status() -> Dict[str, Any]
```

**Keywords:** workspace, memory, status

Show mode, local evidence counts, Git Notes outbox, and note-ref state.

### Function: `sync_workspace_memory_notes` {#tools-workspace_memory-sync_workspace_memory_notes}

```python
async def sync_workspace_memory_notes(limit: int=20) -> Dict[str, Any]
```

**Keywords:** sync, workspace, memory, notes

Retry pending compact Git Notes mirrors when local Git writes are enabled.

### Function: `import_workspace_memory_notes` {#tools-workspace_memory-import_workspace_memory_notes}

```python
async def import_workspace_memory_notes(limit: int=200) -> Dict[str, Any]
```

**Keywords:** import, workspace, memory, notes

Recover SQLite evidence from verified compact envelopes in Git Notes.

## utils.py {#utils}

### Method: `PathResolver.get_service_root` {#utils-pathresolver-get_service_root}

```python
def PathResolver.get_service_root() -> Path
```

**Keywords:** path, resolver, get, service, root

Immutable UniGrok application/assets root.

This is deliberately independent from any project an IDE happens to
have open. Container images use ``/app``; source contributors may
override it to their checkout with ``UNIGROK_SERVICE_ROOT``.

### Method: `PathResolver.get_workspace_root` {#utils-pathresolver-get_workspace_root}

```python
def PathResolver.get_workspace_root(cls) -> Optional[Path]
```

**Keywords:** path, resolver, get, workspace, root

Return an explicitly attached target workspace, if one exists.

Stable HTTP service mode is workspace-neutral. Contributor mode binds
the source checkout by default so local development keeps its existing
file/git/test capabilities. The test runtime follows that contributor
fallback unless a test explicitly selects stable service mode.

### Method: `PathResolver.get_project_root` {#utils-pathresolver-get_project_root}

```python
def PathResolver.get_project_root(cls) -> Path
```

**Keywords:** path, resolver, get, project, root

Backward-compatible project root alias.

New code must choose ``get_service_root`` for bundled assets or
``get_workspace_root`` for user-project access. This fallback exists
for integrations that have not yet made that distinction explicit.

### Method: `PathResolver.validate_path` {#utils-pathresolver-validate_path}

```python
def PathResolver.validate_path(cls, path_str: str) -> Path
```

**Keywords:** path, resolver, validate, path

Resolve path and ensure it lies within the attached workspace.

### Function: `grok_cli_available` {#utils-grok_cli_available}

```python
def grok_cli_available() -> bool
```

**Keywords:** grok, cli, available

True when a grok CLI binary is resolvable on this host — the gate the
local CLI plane needs (binary presence only; auth validity is the
CLI's own concern at call time).

### Function: `grok_cli_oauth_env` {#utils-grok_cli_oauth_env}

```python
def grok_cli_oauth_env(base: Optional[Dict[str, str]]=None) -> Dict[str, str]
```

**Keywords:** grok, cli, oauth, env

Return an OAuth-only environment for a Grok CLI child.

The UniGrok process owns API, management, gateway, and subordinate-provider
credentials. Removing that exact set from every CLI child keeps the xAI
credential planes independent and prevents Grok-launched tools from seeing
unrelated provider secrets. The persisted grok.com OAuth path remains
available, so the CLI must use that subscription identity or fail closed.

### Function: `grok_cli_plane_status` {#utils-grok_cli_plane_status}

```python
def grok_cli_plane_status(timeout_sec: float=5.0, *, force: bool=False) -> Dict[str, Any]
```

**Keywords:** grok, cli, plane, status

Return a bounded, cached, non-secret view of the OAuth CLI plane.

``auth.json`` is necessary but not sufficient: it may be stale.  A
successful ``grok models`` response that explicitly identifies grok.com
login verifies the service credential without consuming an inference
turn.  The probe always strips API-key variables so an API-backed CLI can
never masquerade as the independent subscription plane.

### Function: `grok_cli_check_ready` {#utils-grok_cli_check_ready}

```python
def grok_cli_check_ready(timeout_sec: float=2.0) -> bool
```

**Keywords:** grok, cli, check, ready

Compatibility wrapper for the verified OAuth CLI-plane probe.

### Function: `credential_plane_contract` {#utils-credential_plane_contract}

```python
def credential_plane_contract(cli_status: Optional[Dict[str, Any]]=None) -> Dict[str, Any]
```

**Keywords:** credential, plane, contract

Return the shared non-secret plane health and action contract.

### Function: `get_runtime_stats` {#utils-get_runtime_stats}

```python
def get_runtime_stats() -> Dict[str, int]
```

**Keywords:** get, runtime, stats

Snapshot of timed-thread pressure (consumed by grok_mcp_status).

### Function: `run_blocking` {#utils-run_blocking}

```python
async def run_blocking(fn: Callable, *args, timeout: Optional[float]=None, **kwargs)
```

**Keywords:** run, blocking

Run blocking SDK/local work on a bounded executor.

Timed calls get a dedicated daemon thread instead of a shared-pool worker:
a call that outlives its timeout then only strands its own thread, whereas
with the shared pool eight stuck calls would permanently occupy every
worker and deadlock all SDK bridging in the server. The dedicated threads
are capped (UNIGROK_MAX_TIMED_THREADS, default 64): at capacity the call
fails fast with RuntimeError instead of spawning yet another thread.

### Function: `communicate_with_timeout` {#utils-communicate_with_timeout}

```python
async def communicate_with_timeout(proc: Any, timeout_sec: Optional[float], input_data: Optional[bytes]=None)
```

**Keywords:** communicate, with, timeout

Communicate with a subprocess and always reap it on timeout.

``None`` deliberately means no gateway deadline.  The caller can still
cancel the coroutine, and operators can configure a real deadline when
their deployment requires one.

### Class: `CallerBudgetExceeded` {#utils-callerbudgetexceeded}

```python
class CallerBudgetExceeded
```

**Keywords:** caller, budget, exceeded

A caller's UNIGROK_CALLER_BUDGETS daily spend is at/over its limit.

Raised by orchestrate() BEFORE any model work; FastMCP surfaces it to the
client as a tool error (isError), never a server crash.

### Function: `enforce_caller_budget` {#utils-enforce_caller_budget}

```python
async def enforce_caller_budget(store_param: Any, caller: Optional[str]) -> None
```

**Keywords:** enforce, caller, budget

Pre-execution per-caller daily budget gate.

No-op unless UNIGROK_CALLER_BUDGETS is set AND the caller matches an
entry (unset env returns before any parsing — zero hot-path cost by
default). Spend is today's telemetry cost across every caller matching
the entry's substring (the entry IS the shared pot), read via one
created_at-indexed query and cached ~60s per entry. At/over budget raises
CallerBudgetExceeded; a failing store read degrades OPEN — a broken
telemetry table must not block traffic.

### Function: `new_request_id` {#utils-new_request_id}

```python
def new_request_id() -> str
```

**Keywords:** new, request, id

Fresh short correlation id: the first 12 hex chars of a uuid4 — unique
enough to grep logs and cheap enough to stamp on every row.

### Function: `normalize_request_id` {#utils-normalize_request_id}

```python
def normalize_request_id(value: Any) -> str
```

**Keywords:** normalize, request, id

Sanitize a request id for logs/db rows/headers: keep only URL- and
header-safe chars, bound to 64 (a W3C trace-id is 32 hex). Blank -> "".

### Function: `set_request_id` {#utils-set_request_id}

```python
def set_request_id(value: Any)
```

**Keywords:** set, request, id

Bind a request id to the current async context (the HTTP gateway
middleware does this per request). Returns the reset token.

### Function: `request_id_scope` {#utils-request_id_scope}

```python
def request_id_scope()
```

**Keywords:** request, id, scope

Guarantee a bound request id for the duration of one agent call.

Respects an inherited id (gateway traceparent, an outer agent call);
otherwise generates a fresh one and RESETS it on exit so two sequential
calls in the same task never share a correlation id.

### Function: `prefer_cli_for_route` {#utils-prefer_cli_for_route}

```python
def prefer_cli_for_route(*, route_class: str, thinking_mode: bool) -> bool
```

**Keywords:** prefer, cli, for, route

Prefer subscription CLI for compatible unpinned local work.

Grok 4.5 thinking, vision, and multi-agent research are API-native. When
the API key is absent, CLI remains the graceful service-saving route even
for a request that asked for thinking; the receipt records that downgrade.

### Function: `load_grok_profile` {#utils-load_grok_profile}

```python
def load_grok_profile(profile_or_model: str) -> Dict[str, Any]
```

**Keywords:** load, grok, profile

Load a bounded Grok model profile from `.grok/hyperparams`.

### Function: `load_grok_prompt` {#utils-load_grok_prompt}

```python
def load_grok_prompt(prompt_ref: str) -> str
```

**Keywords:** load, grok, prompt

Read a Grok adapter prompt from `.grok/prompts` with traversal protection.

### Function: `bounded_env_int` {#utils-bounded_env_int}

```python
def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int
```

**Keywords:** bounded, env, int

Read an integer limit from the environment without allowing extremes.

### Function: `input_limit` {#utils-input_limit}

```python
def input_limit(name: str, default: int, minimum: int, maximum: int) -> int
```

**Keywords:** input, limit

Named resource-limit helper shared by local and media tools.

### Function: `validate_local_input` {#utils-validate_local_input}

```python
def validate_local_input(path: Path, *, max_bytes: int, allowed_suffixes: Optional[tuple[str, ...]]=None, label: str='file') -> Path
```

**Keywords:** validate, local, input

Validate a resolved local input before any unbounded read occurs.

### Function: `discover_xai_api_models` {#utils-discover_xai_api_models}

```python
async def discover_xai_api_models() -> Dict[str, Any]
```

**Keywords:** discover, xai, api, models

Discover xAI API language models, falling back to known API model ids.

### Function: `discover_grok_cli_models` {#utils-discover_grok_cli_models}

```python
async def discover_grok_cli_models(timeout_sec: float=5.0) -> Dict[str, Any]
```

**Keywords:** discover, grok, cli, models

Discover local Grok CLI models with Cloud Run and failure safeguards.

### Function: `discover_local_grok_profiles` {#utils-discover_local_grok_profiles}

```python
def discover_local_grok_profiles() -> Dict[str, Any]
```

**Keywords:** discover, local, grok, profiles

List local `.grok` profiles without treating them as provider models.

### Function: `build_model_catalog` {#utils-build_model_catalog}

```python
async def build_model_catalog(include_cli: bool=True) -> Dict[str, Any]
```

**Keywords:** build, model, catalog

Build a structured catalog for API models, local CLI models, and profiles.

### Function: `xai_management_key_configured` {#utils-xai_management_key_configured}

```python
def xai_management_key_configured() -> bool
```

**Keywords:** xai, management, key, configured

Return whether one unambiguous xAI management credential is configured.

### Function: `get_xai_inference_client` {#utils-get_xai_inference_client}

```python
def get_xai_inference_client()
```

**Keywords:** get, xai, inference, client

Return the cached inference-only xAI SDK client.

The installed SDK reads ``XAI_MANAGEMENT_KEY`` whenever its management
argument is falsey.  Pass a fixed, non-provider isolation canary instead of
mutating process environment, then deny Collections on the returned
surface.  Real management credentials can therefore never enter this
client's channel or inference call paths.

### Function: `get_xai_client` {#utils-get_xai_client}

```python
def get_xai_client()
```

**Keywords:** get, xai, client

Compatibility alias for the inference-only xAI client factory.

### Function: `get_xai_management_client` {#utils-get_xai_management_client}

```python
def get_xai_management_client()
```

**Keywords:** get, xai, management, client

Return the cached xAI Collections/admin client.

The installed SDK requires the inference key alongside the management key
when constructing its Collections service.  This factory is therefore the
only place where both credentials may enter one SDK client, and callers are
restricted to the RAG, knowledge, task-memory, and provider-harvest admin
surfaces.

### Function: `close_xai_inference_client` {#utils-close_xai_inference_client}

```python
def close_xai_inference_client()
```

**Keywords:** close, xai, inference, client

Close only the inference client cache.

### Function: `close_xai_management_client` {#utils-close_xai_management_client}

```python
def close_xai_management_client()
```

**Keywords:** close, xai, management, client

Close only the Collections/admin client cache.

### Function: `close_xai_client` {#utils-close_xai_client}

```python
def close_xai_client()
```

**Keywords:** close, xai, client

Compatibility shutdown hook that closes both role-separated caches.

### Class: `CircuitBreakerOpenError` {#utils-circuitbreakeropenerror}

```python
class CircuitBreakerOpenError
```

**Keywords:** circuit, breaker, open, error

Raised to fail fast when a model's circuit breaker is open.

### Function: `classify_xai_error` {#utils-classify_xai_error}

```python
def classify_xai_error(exc: Exception) -> str
```

**Keywords:** classify, xai, error

Classify an xAI call failure as "retryable" (429/5xx/connection/timeout/
transient) or "fatal" (400/401/403/404, validation). Fatal errors must not
burn retries — retrying an auth failure only delays the real error.

### Function: `check_circuit_breaker` {#utils-check_circuit_breaker}

```python
def check_circuit_breaker(model: str)
```

**Keywords:** check, circuit, breaker

Fail fast with CircuitBreakerOpenError while a model's breaker is open.

After the cool-down elapses the breaker half-opens: the next call is
allowed through as a probe; its success closes the breaker, its failure
re-opens it via record_xai_failure.

### Function: `record_xai_failure` {#utils-record_xai_failure}

```python
def record_xai_failure(model: str)
```

**Keywords:** record, xai, failure

Count a failed xAI call; open the breaker at the consecutive threshold.

### Function: `record_xai_success` {#utils-record_xai_success}

```python
def record_xai_success(model: str)
```

**Keywords:** record, xai, success

Reset a model's breaker after any successful xAI call.

### Function: `get_circuit_breaker_state` {#utils-get_circuit_breaker_state}

```python
def get_circuit_breaker_state() -> Dict[str, Any]
```

**Keywords:** get, circuit, breaker, state

Snapshot of per-model breaker state (consumed by grok_mcp_status).

### Class: `RequestContextLogFilter` {#utils-requestcontextlogfilter}

```python
class RequestContextLogFilter
```

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

```python
class JsonLogFormatter
```

**Keywords:** json, log, formatter

One JSON object per line, stdlib only: ts, level, logger, msg,
request_id (always present, "" when unset), and caller when known.
The rendered message goes through redact_secrets so structured logs get
the same secret hygiene as every persisted surface.

### Method: `GrokSessionStore.attach_semantic_scores` {#utils-groksessionstore-attach_semantic_scores}

```python
async def GrokSessionStore.attach_semantic_scores(self, request_id: str, semantic: Dict[str, Any], *, scan_limit: int=200) -> bool
```

**Keywords:** grok, session, store, attach, semantic, scores

Attach a shadow semantic-eval block to the turn's telemetry row.

The judge runs asynchronously after the row was written, so this is a
read-modify-write of the v8 metadata envelope keyed by the request id
already inside it. The scan is bounded to the newest rows and matches
in Python (never depends on the optional json1 extension — the
get_caller_cost_today precedent). Rows written by auxiliary work
sharing the same request context (history compaction) are skipped so
the block always lands on the turn's own row. Returns False on a miss
so the caller can count it; a second telemetry row is deliberately not
written (it would inflate request/success aggregates).

### Method: `GrokSessionStore.get_semantic_judge_cost_today` {#utils-groksessionstore-get_semantic_judge_cost_today}

```python
async def GrokSessionStore.get_semantic_judge_cost_today(self) -> float
```

**Keywords:** grok, session, store, get, semantic, judge, cost, today

Durable sum of today's semantic-eval judge spend (the
semantic.judge_cost_usd values in telemetry metadata) — rehydrates
the in-process daily budget accumulator across restarts. Same bounded
created_at scan + Python-side JSON match as get_caller_cost_today
(never depends on the optional json1 extension).

### Method: `GrokSessionStore.get_caller_cost_today` {#utils-groksessionstore-get_caller_cost_today}

```python
async def GrokSessionStore.get_caller_cost_today(self, caller_principal: str) -> float
```

**Keywords:** grok, session, store, get, caller, cost, today

Today's total telemetry cost attributed to one exact principal.

One indexed read: idx_telemetry_created_at bounds the scan to today's
rows; telemetry may append an encoded client label after ``|``, but
labels cannot match or poison another principal's pot. The match runs
in Python so the query never depends on optional json1.

### Method: `GrokSessionStore.get_caller_stats_today` {#utils-groksessionstore-get_caller_stats_today}

```python
async def GrokSessionStore.get_caller_stats_today(self, limit: int=10) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, get, caller, stats, today

Per-caller aggregate over TODAY's telemetry rows, busiest first:
{caller, requests, verified_outcomes, success_rate, total_cost_usd}. Unattributed rows
(pre-v8 or anonymous) are excluded. Same bounded created_at-indexed
read as get_caller_cost_today — consumed by grok_mcp_status.

### Method: `GrokSessionStore.get_recent_model_stats` {#utils-groksessionstore-get_recent_model_stats}

```python
async def GrokSessionStore.get_recent_model_stats(self, limit: int=200) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, get, recent, model, stats

Per-plane/model aggregate over the most recent task-memory rows.

The RoutingAdvisor's data source: rows are
{plane, model, samples, success_rate, avg_cost} computed over the last
`limit` task_memory entries (task memory carries the model column;
the telemetry table only records the plane).

### Method: `GrokSessionStore.upsert_routing_calibration` {#utils-groksessionstore-upsert_routing_calibration}

```python
async def GrokSessionStore.upsert_routing_calibration(self, category: str, route: str, model: str, success_rate: float, avg_cost_usd: float, n: int)
```

**Keywords:** grok, session, store, upsert, routing, calibration

Upsert one eval-derived calibration row (evals/runner.py writes
these after every run). updated_at always bumps so the freshness
window in get_routing_calibration measures the last eval run.

### Method: `GrokSessionStore.get_routing_calibration` {#utils-groksessionstore-get_routing_calibration}

```python
async def GrokSessionStore.get_routing_calibration(self, max_age_hours: Optional[float]=None) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, get, routing, calibration

Calibration rows, optionally filtered to those refreshed within the
last max_age_hours (ISO timestamps compare lexicographically, matching
the jobs-staleness convention).

### Method: `GrokSessionStore.get_similar_task_memories` {#utils-groksessionstore-get_similar_task_memories}

```python
async def GrokSessionStore.get_similar_task_memories(self, prompt: str, context_id: Optional[str]=None, limit: int=3, verified_only: bool=False) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, get, similar, task, memories

Rank stored task memories against a prompt; every row carries a
`score` (higher = better on both paths).

FTS5 path: MATCH over an OR-joined _task_terms expression (safe
tokens only — raw prompt text never reaches MATCH), bm25-ranked and
batch-normalized into the same 0..1 band as the fallback's
term-overlap fraction BEFORE the +2.0 context_id / +1.0 task_hash
bonuses, so downstream consumers see one score contract. A plain
context_id query is merged in because a same-context row with ZERO
term overlap must still surface (score 0 + 2.0 bonus — the
long-standing fallback semantics). Fallback (no FTS5): Python
term-overlap over the most recent 200 rows.

### Method: `GrokSessionStore.reset_task_memory_sync` {#utils-groksessionstore-reset_task_memory_sync}

```python
async def GrokSessionStore.reset_task_memory_sync(self) -> int
```

**Keywords:** grok, session, store, reset, task, memory, sync

Re-queue every VERIFIED task memory for mirroring (rag backfill
--force-reupload): deterministic document names keep the re-upload
idempotent on the collection side. Returns the row count.

### Method: `GrokSessionStore.begin_provider_attempt` {#utils-groksessionstore-begin_provider_attempt}

```python
async def GrokSessionStore.begin_provider_attempt(self, start: Any) -> bool
```

**Keywords:** grok, session, store, begin, provider, attempt

Durably record Grok authorization before a worker channel effect.

Returns True for a new row and False for an exact idempotent replay.
Reusing either identity for different work fails closed.

### Method: `GrokSessionStore.complete_provider_attempt` {#utils-groksessionstore-complete_provider_attempt}

```python
async def GrokSessionStore.complete_provider_attempt(self, attempt_id: str, result: Any) -> bool
```

**Keywords:** grok, session, store, complete, provider, attempt

Bind one normalized transport result to its exact Grok start row.

### Method: `GrokSessionStore.mark_stale_provider_attempts_indeterminate` {#utils-groksessionstore-mark_stale_provider_attempts_indeterminate}

```python
async def GrokSessionStore.mark_stale_provider_attempts_indeterminate(self, stale_before: datetime) -> int
```

**Keywords:** grok, session, store, mark, stale, provider, attempts, indeterminate

Close crash-left starts without pretending the worker failed.

### Method: `GrokSessionStore.lease_provider_attempts_for_harvest` {#utils-groksessionstore-lease_provider_attempts_for_harvest}

```python
async def GrokSessionStore.lease_provider_attempts_for_harvest(self, lease_id: str, lease_seconds: float=60.0, limit: int=25) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, lease, provider, attempts, for, harvest

Atomically lease due terminal episodes, including expired leases.

A crashed uploader therefore never strands a row.  The retry count is
intentionally unbounded: this outbox has no discard/dead-letter state.

### Method: `GrokSessionStore.provider_attempt_harvest_lease_is_fresh` {#utils-groksessionstore-provider_attempt_harvest_lease_is_fresh}

```python
async def GrokSessionStore.provider_attempt_harvest_lease_is_fresh(self, attempt_id: str, lease_id: str, minimum_remaining_seconds: float=0.0) -> bool
```

**Keywords:** grok, session, store, provider, attempt, harvest, lease, is, fresh

Check trusted-time ownership immediately before a cloud effect.

### Method: `GrokSessionStore.mark_provider_attempt_harvest_synced` {#utils-groksessionstore-mark_provider_attempt_harvest_synced}

```python
async def GrokSessionStore.mark_provider_attempt_harvest_synced(self, attempt_id: str, lease_id: str, remote_file_id: str) -> bool
```

**Keywords:** grok, session, store, mark, provider, attempt, harvest, synced

Commit one upload only when the caller still owns its lease.

### Method: `GrokSessionStore.mark_provider_attempt_harvest_retry` {#utils-groksessionstore-mark_provider_attempt_harvest_retry}

```python
async def GrokSessionStore.mark_provider_attempt_harvest_retry(self, attempt_id: str, lease_id: str, error: str, backoff_seconds: float) -> bool
```

**Keywords:** grok, session, store, mark, provider, attempt, harvest, retry

Release a failed lease into bounded backoff without discarding it.

### Method: `GrokSessionStore.save_fact` {#utils-groksessionstore-save_fact}

```python
async def GrokSessionStore.save_fact(self, fact: str, scope: str='global', source: str='') -> Optional[int]
```

**Keywords:** grok, session, store, save, fact

Persist one distilled fact, redacted and bounded at rest.

Deduped on exact (scope, fact) text: re-saving an existing fact
touches it (uses+1, last_used_at bump) instead of inserting a
duplicate, so re-distilling a session never multiplies rows. Returns
the row id (existing or new); None for empty facts.

### Method: `GrokSessionStore.search_facts` {#utils-groksessionstore-search_facts}

```python
async def GrokSessionStore.search_facts(self, query: str, scope: Optional[str]=None, limit: int=5) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, search, facts

Rank stored facts against a query; every row carries a `score`
(higher = better on both paths).

FTS5 path: MATCH over an OR-joined _task_terms expression (safe
tokens only — raw query text never reaches MATCH), ranked by bm25.
Fallback path (no FTS5): LIKE prefilter on the terms column plus
term-overlap scoring, like get_similar_task_memories. scope=None
searches all scopes; a named scope also surfaces 'global' facts so
session-scoped retrieval still sees workspace-wide knowledge.

### Method: `GrokSessionStore.touch_facts` {#utils-groksessionstore-touch_facts}

```python
async def GrokSessionStore.touch_facts(self, fact_ids: List[int])
```

**Keywords:** grok, session, store, touch, facts

Bump uses/last_used_at on the given facts (called for every fact
actually injected into a prompt).

### Method: `GrokSessionStore.delete_fact` {#utils-groksessionstore-delete_fact}

```python
async def GrokSessionStore.delete_fact(self, fact_id: int) -> bool
```

**Keywords:** grok, session, store, delete, fact

Remove one fact (and its index row); True when a row was deleted.

### Method: `GrokSessionStore.list_facts` {#utils-groksessionstore-list_facts}

```python
async def GrokSessionStore.list_facts(self, limit: int=20, scope: Optional[str]=None) -> List[Dict[str, Any]]
```

**Keywords:** grok, session, store, list, facts

Most recent facts first (the grok://knowledge resource view).

### Method: `GrokSessionStore.replace_messages` {#utils-groksessionstore-replace_messages}

```python
async def GrokSessionStore.replace_messages(self, session_name: str, messages: List[Dict[str, Any]])
```

**Keywords:** grok, session, store, replace, messages

Atomically replace a session's message history in one transaction.

Replaces the old delete-then-reinsert save_history flow: a crash mid-way
can no longer leave a session with partially rewritten history. The
session row itself (cli_session_id/api_thread_id/model) is preserved.

### Method: `GrokSessionStore.create_job` {#utils-groksessionstore-create_job}

```python
async def GrokSessionStore.create_job(self, job_id: str, prompt: str, model: str, caller: Optional[str]=None, request_id: Optional[str]=None)
```

**Keywords:** grok, session, store, create, job

Insert a 'queued' job row. caller=None and request_id=None fall
back to the identities bound to the current async context (the
src/storage.py contract) so gateway-submitted jobs stay attributed
and traceable back to their originating request.

### Method: `GrokSessionStore.update_job` {#utils-groksessionstore-update_job}

```python
async def GrokSessionStore.update_job(self, job_id: str, status: Optional[str]=None, result: Optional[str]=None, cost: Optional[float]=None)
```

**Keywords:** grok, session, store, update, job

Update a job row; updated_at always bumps so staleness detection
(JobManager) measures the last time the owning task touched the row.

### Method: `GrokSessionStore.create_swarm_task` {#utils-groksessionstore-create_swarm_task}

```python
async def GrokSessionStore.create_swarm_task(self, task_id: str, target_path: str, focus_node: str, base_file_hash: str, test_target: str, bench_command: str, budget_usd: float, seed: int, caller: Optional[str]=None, request_id: Optional[str]=None, search_strategy: str='baseline_batch', primary_goal: str='balanced', input_kind: str='workspace', analytics_json: Optional[str]=None) -> None
```

**Keywords:** grok, session, store, create, swarm, task

Insert a 'queued' swarm task row. caller/request_id fall back to
the identities bound to the current async context (the src/storage.py
contract), matching create_job.

### Method: `GrokSessionStore.update_swarm_task` {#utils-groksessionstore-update_swarm_task}

```python
async def GrokSessionStore.update_swarm_task(self, task_id: str, status: Optional[str]=None, spent_usd: Optional[float]=None, generation: Optional[int]=None, baseline_json: Optional[str]=None, oracle_json: Optional[str]=None, folded_state: Optional[str]=None, analytics_json: Optional[str]=None, champion_id: Optional[str]=None) -> None
```

**Keywords:** grok, session, store, update, swarm, task

Update a swarm task row. updated_at ALWAYS bumps — the runner calls
this after every candidate as its heartbeat, and staleness detection
measures the time since the owning task last touched the row.

### Method: `GrokSessionStore.insert_swarm_candidate` {#utils-groksessionstore-insert_swarm_candidate}

```python
async def GrokSessionStore.insert_swarm_candidate(self, candidate: Dict[str, Any]) -> bool
```

**Keywords:** grok, session, store, insert, swarm, candidate

Insert one evaluated candidate row; False on a duplicate
(task_id, code_hash) — the engine treats duplicates as free discards.

The stored code is the exact replacement slice apply_swarm_winner
would splice, so it is NEVER silently truncated or rewritten: an
oversized slice or one whose bytes redact_secrets would alter is
rejected here with ValueError (a mutant carrying secret-shaped
literals is suspect, and splicing a redacted variant would corrupt
the file).

### Function: `extract_cost_from_output` {#utils-extract_cost_from_output}

```python
def extract_cost_from_output(content: str) -> float
```

**Keywords:** extract, cost, from, output

Read the standard usage footer cost from nested local tool output.

### Class: `FoldedSessionState` {#utils-foldedsessionstate}

```python
class FoldedSessionState
```

**Keywords:** folded, session, state

Schema-enforced compaction fold: the per-session ephemeral working
state a later turn needs verbatim (durable facts stay with the
distiller/FactList — the two are deliberately separate). List bounds live
in the schema; char caps live in _render_folded_state so an over-long
field degrades by truncation instead of failing validation.

### Function: `maybe_compact_history` {#utils-maybe_compact_history}

```python
async def maybe_compact_history(session: str, history: List[dict], store_param: Optional[Any]=None, force: bool=False, model_hint: Optional[str]=None) -> List[dict]
```

**Keywords:** maybe, compact, history

Compact a session's local history once it exceeds the token budget.

LOCAL compaction by design: the installed SDK's compaction surface
(Chat.compact() and client.chat.compact_context()) returns an OPAQUE
``encrypted_content`` blob meant to be re-sent as an assistant message —
it cannot serve as the readable durable record this store keeps. Instead
the oldest half of the history is FOLDED into a schema-enforced
FoldedSessionState (goal / constraints / dead ends / active files /
narrative) via the shared tool-free structured-parse seam, so hard
constraints survive compaction verbatim instead of dissolving into
prose; any fold failure falls back to the legacy prose summary IN THE
SAME CALL (worst case two bounded paid calls under the same
UNIGROK_COMPACT_TIMEOUT each — UNIGROK_COMPACT_FOLD=0 opts out). The
newest half stays verbatim, and the replay paths (AgentLoop._init_chat,
_call_plane) append system-role history entries so the fold reaches the
model. The trigger is min(UNIGROK_COMPACT_THRESHOLD_TOKENS,
UNIGROK_COMPACT_CONTEXT_RATIO × model context) when model_hint is
provided — bit-identical to the flat threshold at the defaults for
current large-context models.

Never compacts under UNI_GROK_TESTING unless force=True (hermetic tests
exercise it with a mocked client). Returns the possibly-compacted history;
every failure path returns the input unchanged.

### Class: `GitContextCache` {#utils-gitcontextcache}

```python
class GitContextCache
```

**Keywords:** git, context, cache

Tiny bounded TTL cache. get_dynamic_context caches one entry per
distinct prompt hash (each holding a multi-KB context string), so entries
MUST be evicted: expired keys are dropped on read and pruned on every
write, and max_entries caps live keys (oldest-first eviction) so a
long-running server never accumulates one entry per unique prompt.

### Method: `GitContextCache.clear_prefix` {#utils-gitcontextcache-clear_prefix}

```python
def GitContextCache.clear_prefix(self, prefix: str)
```

**Keywords:** git, context, cache, clear, prefix

Drop every entry whose key starts with prefix — the prompt-keyed
'dynamic_context:<hash>' family invalidates as one unit.

### Function: `format_tool_trace_block` {#utils-format_tool_trace_block}

```python
def format_tool_trace_block(trace: List[Any], max_entries: int=20, max_chars_per_entry: int=600) -> str
```

**Keywords:** format, tool, trace, block

Render a persisted tool trace as a compact context block for replay.

The SDK's assistant() helper cannot carry tool_calls, so replaying raw
tool_result messages would orphan their ids — this text block is the
replay format instead.

### Class: `ToolObservation` {#utils-toolobservation}

```python
class ToolObservation
```

**Keywords:** tool, observation

Structured result from an internal tool dispatch call.

### Function: `model_max_tokens_fallback` {#utils-model_max_tokens_fallback}

```python
def model_max_tokens_fallback(model_name: str) -> int
```

**Keywords:** model, max, tokens, fallback

Static known-limit lookup — never touches the network.

### Function: `get_model_max_tokens` {#utils-get_model_max_tokens}

```python
def get_model_max_tokens(model_name: str) -> int
```

**Keywords:** get, model, max, tokens

Resolve maximum prompt token lengths using the xAI SDK's models API,
with a robust known fallback directory for CLI models and network isolation.

Successful API lookups are cached per model for _MODEL_MAX_TOKENS_TTL_SEC so
repeated agent runs do not pay a synchronous SDK network call each time.

### Function: `format_knowledge_notes` {#utils-format_knowledge_notes}

```python
def format_knowledge_notes(facts: List[Dict[str, Any]]) -> str
```

**Keywords:** format, knowledge, notes

Render injected knowledge facts (mirrors format_task_memory_notes):
clearly marked as recalled memory — a hint to verify, never proof.

### Function: `get_dynamic_context` {#utils-get_dynamic_context}

```python
async def get_dynamic_context(mcp_instance: Any=None, prompt: Optional[str]=None) -> tuple[str, bool, str]
```

**Keywords:** get, dynamic, context

Build the workspace system-prompt context: git state, the most
relevant modified/recent file (ranked against `prompt` when given), and
top-K knowledge facts matching the prompt. Cached per prompt-hash under
the same short git-cache TTL as before; the promptless call keeps the
exact legacy behavior (first modified file, no knowledge block).

### Class: `AgentLoopPolicy` {#utils-agentlooppolicy}

```python
class AgentLoopPolicy
```

**Keywords:** agent, loop, policy

Configurable guardrails for the AgentLoop.

### Function: `register_internal_tool` {#utils-register_internal_tool}

```python
def register_internal_tool(name: str, fn: Callable)
```

**Keywords:** register, internal, tool

Register a raw async callable for internal agent dispatch.

### Function: `ensure_internal_tools_registered` {#utils-ensure_internal_tools_registered}

```python
def ensure_internal_tools_registered()
```

**Keywords:** ensure, internal, tools, registered

Import modular tools for side-effect registration when utils is used directly.

### Function: `dispatch_internal_tool` {#utils-dispatch_internal_tool}

```python
async def dispatch_internal_tool(name: str, arguments: Dict[str, Any], timeout_sec: float=30.0) -> ToolObservation
```

**Keywords:** dispatch, internal, tool

Execute a registered raw tool with timeout and full error isolation.

### Class: `AgentLoop` {#utils-agentloop}

```python
class AgentLoop
```

**Keywords:** agent, loop

True ReAct agentic loop with parallel tool dispatch, cost/timeout guardrails,
and observation truncation. Replaces the closed text-echo recursive loop.

Architecture:
  Tier 1 (xAI server-side built-ins): code_execution, web_search, x_search
    → Passed in AGENTIC_TOOLS_SCHEMA, run inside xAI infra, zero re-entrancy risk.
  Tier 2 (local raw callables): generate_image, file ops, filesystem reads
    → Dispatched via _INTERNAL_TOOL_REGISTRY, executed locally.

### Method: `AgentLoop.run` {#utils-agentloop-run}

```python
async def AgentLoop.run(self, prompt: str, session: Optional[str]=None, history: Optional[List[dict]]=None, input_messages: Optional[List[Dict[str, Any]]]=None) -> MetaLayer
```

**Keywords:** agent, loop, run

Execute the full ReAct loop and return a populated MetaLayer.

### Class: `ModelResolver` {#utils-modelresolver}

```python
class ModelResolver
```

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

### Method: `ModelResolver.catalog_snapshot` {#utils-modelresolver-catalog_snapshot}

```python
async def ModelResolver.catalog_snapshot(self) -> tuple[List[str], str, bool]
```

**Keywords:** model, resolver, catalog, snapshot

Return a TTL-cached catalog without making routing depend on it.

Discovery failure returns the known fallback IDs and is cached just
like a success, preventing every request from paying a dead-network
timeout.  The source/availability values ride the routing receipt.

### Function: `resolve_model` {#utils-resolve_model}

```python
async def resolve_model(alias_or_model: str) -> str
```

**Keywords:** resolve, model

Resolve a routing alias (planning/coding/vision/research) to a live model
slug via the shared ModelResolver; explicit slugs pass through unchanged.

### Function: `routing_reason_score` {#utils-routing_reason_score}

```python
def routing_reason_score(prompt: str) -> int
```

**Keywords:** routing, reason, score

Score whether a prompt benefits from the higher-intelligence route.

This keeps routing local and cheap, but avoids escalating every prompt that
happens to contain a broad word such as "product" or "timeline".

### Class: `RoutingDecision` {#utils-routingdecision}

```python
class RoutingDecision
```

**Keywords:** routing, decision

Diagnostic record of the advisor's last borderline decision.

source precedence: calibration > semantic > telemetry > static.
shadow=True marks a decision where a semantic verdict WAS computed but
the baseline was returned (UNIGROK_TASK_RAG=shadow — zero production
impact by construction).

### Class: `RoutingAdvisor` {#utils-routingadvisor}

```python
class RoutingAdvisor
```

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

### Method: `RoutingAdvisor.inject_stats` {#utils-routingadvisor-inject_stats}

```python
def RoutingAdvisor.inject_stats(self, stats: List[Dict[str, Any]])
```

**Keywords:** routing, advisor, inject, stats

Test hook: pin the aggregate; refresh is skipped while injected.

### Method: `RoutingAdvisor.inject_calibration` {#utils-routingadvisor-inject_calibration}

```python
def RoutingAdvisor.inject_calibration(self, rows: List[Dict[str, Any]])
```

**Keywords:** routing, advisor, inject, calibration

Test hook: pin eval calibration rows; overrides the UNI_GROK_TESTING
bypass so tests exercise the precedence path explicitly.

### Method: `RoutingAdvisor.inject_semantic` {#utils-routingadvisor-inject_semantic}

```python
def RoutingAdvisor.inject_semantic(self, verdict: Optional[Any])
```

**Keywords:** routing, advisor, inject, semantic

Test hook: pin the semantic verdict (a rag.SemanticVerdict);
overrides the UNI_GROK_TESTING bypass so tests exercise the
precedence path explicitly.

### Method: `RoutingAdvisor.prefers_planning` {#utils-routingadvisor-prefers_planning}

```python
async def RoutingAdvisor.prefers_planning(self, store: Any, planning_model: str, coding_model: str, prompt: Optional[str]=None, context_id: Optional[str]=None) -> bool
```

**Keywords:** routing, advisor, prefers, planning

True only when fresh eval calibration (first), a decidable
semantic task-memory verdict (UNIGROK_TASK_RAG=active only), or
recent telemetry (fallback) justifies flipping a borderline prompt
to the planning model; anything else keeps the static prior. The
3-arg legacy call (no prompt) behaves exactly as before semantic
evidence existed.

### Method: `RoutingAdvisor.status_view` {#utils-routingadvisor-status_view}

```python
async def RoutingAdvisor.status_view(self, store: Any) -> Dict[str, Any]
```

**Keywords:** routing, advisor, status, view

The advisor's current view, for grok_mcp_status.

### Method: `RoutingAdvisor.selection_evidence` {#utils-routingadvisor-selection_evidence}

```python
async def RoutingAdvisor.selection_evidence(self, store: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
```

**Keywords:** routing, advisor, selection, evidence

Cached local evidence for bounded peer selection.

### Class: `ReflectionVerdict` {#utils-reflectionverdict}

```python
class ReflectionVerdict
```

**Keywords:** reflection, verdict

Schema-enforced reviewer verdict for the thinking route.

### Class: `FactList` {#utils-factlist}

```python
class FactList
```

**Keywords:** fact, list

Schema-enforced distillation output: 3-8 durable, standalone facts
(parsed via the same tool-free structured-parse machinery as
ReflectionVerdict — see _parse_structured).

### Function: `sync_fact_to_collection` {#utils-sync_fact_to_collection}

```python
async def sync_fact_to_collection(fact_id: Any, fact: str, scope: str='global', source: str='') -> bool
```

**Keywords:** sync, fact, to, collection

Best-effort mirror of ONE saved fact into the xAI knowledge collection.

No-op (False) unless UNIGROK_COLLECTIONS=1 and the installed SDK is
capable; never raises and never blocks the caller beyond the bounded
UNIGROK_COLLECTIONS_TIMEOUT. This is the adapter seam: local-first
callers (distill job, remember_fact) fire it after the local save.

### Function: `search_knowledge_collection` {#utils-search_knowledge_collection}

```python
async def search_knowledge_collection(query: str, limit: int=5) -> List[Dict[str, Any]]
```

**Keywords:** search, knowledge, collection

Best-effort search passthrough over the knowledge collection; results
(chunk content + score, origin='collection') merge into search_knowledge.
Returns [] unless enabled and capable; never raises.

### Function: `run_thinking_loop` {#utils-run_thinking_loop}

```python
async def run_thinking_loop(prompt: str, session: Optional[str]=None, store: Any=None, dynamic_sys_prompt: str='', model: str=DEFAULT_PLANNING_MODEL, context_id: Optional[str]=None, max_reflections: Optional[int]=None, global_budget_usd: Optional[float]=None, profile: Optional[Dict[str, Any]]=None, input_messages: Optional[List[Dict[str, Any]]]=None, on_event: Optional[Callable]=None, caller: Optional[str]=None, routing_receipt: Optional[Dict[str, Any]]=None, attempt_recorder: Optional[Callable[..., Any]]=None, defer_telemetry: bool=False) -> MetaLayer
```

**Keywords:** run, thinking, loop

Thinking route: AgentLoop execution wrapped in a schema-enforced
reflection loop. Replaces the retired 6-stage ThinkingKernel.

caller attributes this route's telemetry row (per-caller budgets/metrics
count thinking-mode spend like every other route); orchestrate threads
the identity it resolved, and save_telemetry additionally falls back to
the ambient contextvar when the param stays None.

Each attempt runs the full ReAct AgentLoop; a dedicated tool-free reviewer
chat then parses a ReflectionVerdict via chat.parse() (structured
outputs). status='fail' feeds the issues back into a fresh AgentLoop
attempt — up to max_reflections retries (default
UNIGROK_REFLECT_MAX_ITERATIONS=2) — while cost accumulates across attempts
against ONE shared budget (AgentLoopPolicy.global_budget_usd semantics).
An unavailable reviewer accepts the answer as-is.

### Function: `orchestrate` {#utils-orchestrate}

```python
async def orchestrate(prompt: str, session: Optional[str]=None, mode: Literal['auto', 'reasoning', 'research', 'composer']='auto', thinking_mode: bool=False, store: Any=None, dynamic_sys_prompt: str='', requested_model: Optional[str]=None, mcp_instance: Any=None, enable_agentic: bool=True, context_id: Optional[str]=None, agent_count: Optional[int]=None, input_messages: Optional[List[Dict[str, Any]]]=None, on_event: Optional[Callable]=None, include: Optional[List[str]]=None, caller: Optional[str]=None, require_reasoning_level: Optional[Literal['low', 'medium', 'high']]=None, requested_plane: Literal['auto', 'cli', 'api']='auto', fallback_policy: Literal['same_plane', 'cross_plane']='cross_plane', cli_no_plan: bool=False, cli_verbatim: bool=False, cli_allowed_tools: Optional[str]=None, cli_isolated: bool=False) -> MetaLayer
```

**Keywords:** orchestrate

Route a prompt through the layered execution planes:
  - Thinking route (run_thinking_loop): only when thinking_mode=True —
    AgentLoop execution wrapped in a schema-enforced reflection loop
    (chat.parse(ReflectionVerdict) reviews the answer; failing verdicts
    trigger bounded retries under one shared budget).
  - AgentLoop (ReAct): the DEFAULT path. The full tool surface is attached
    on every request and the model self-directs whether to act. The local
    keyword heuristic no longer gates the agent — it only selects the model
    (reasoning-scored prompts → planning model, others → coding model) when
    the caller has not pinned one.
  - Fast path (_call_plane): toolless single call. Used only when
    enable_agentic=False, when UNIGROK_FORCE_FAST is truthy (kill-switch),
    or as the fallback when the intelligence routes above raise.

### Function: `run_agent_turn` {#utils-run_agent_turn}

```python
async def run_agent_turn(prompt: Optional[str]=None, session: Optional[str]=None, system_prompt: Optional[str]=None, messages: Optional[List[Dict[str, Any]]]=None, model: Optional[str]=None, mode: str='auto', thinking_mode: bool=False, enable_agentic: bool=True, on_event: Optional[Callable]=None, agent_count: Optional[int]=None, include: Optional[List[str]]=None, caller: Optional[str]=None, require_reasoning_level: Optional[Literal['low', 'medium', 'high']]=None, plane: Literal['auto', 'cli', 'api']='auto', fallback_policy: Literal['same_plane', 'cross_plane']='cross_plane', cli_no_plan: bool=False, cli_verbatim: bool=False, cli_allowed_tools: Optional[str]=None, cli_isolated: bool=False) -> MetaLayer
```

**Keywords:** run, agent, turn

Shared single-agent gateway boundary used by HTTP and remote MCP.

model=None lets orchestrate() auto-select between the planning and coding
defaults; mode, thinking_mode, and enable_agentic pass straight through to
orchestrate() (enable_agentic=False selects the toolless fast path).
agent_count (4|16 multi-agent fan-out) and include (extra response
surfaces such as ["inline_citations"]) forward to orchestrate() for the
agent tool's research mode — both are capability-gated downstream.
on_event (sync or async) receives progress events — depth advances, tool
start/end, and real content deltas on the fast plane (see
_emit_agent_event for the event shapes).
caller is the calling agent's identity (MCP clientInfo name or the HTTP
gateway's X-Caller/auth-key alias); None falls back to whatever the
transport bound to the current async context, and it flows into telemetry
attribution, per-caller budgets, and session message metadata.
cli_no_plan/cli_verbatim are narrow headless controls for deterministic
internal generation workflows; cli_allowed_tools can additionally set the
CLI's exact built-in tool allowlist (an empty string disables all tools).
cli_isolated additionally removes inherited project/task context and runs
with an OAuth-only temporary home, empty workspace, disabled memory,
subagents, web search, and interactive prompts. Public calls keep defaults.

## workspace_memory.py {#workspace_memory}

### Function: `import_git_notes` {#workspace_memory-import_git_notes}

```python
async def import_git_notes(store: Any, *, limit: int=200) -> Dict[str, Any]
```

**Keywords:** import, git, notes

Recover verified evidence envelopes from the local notes ref.

Import never trusts a note alone: the annotated commit must still have the
exact landing receipt hash recorded in the envelope.
