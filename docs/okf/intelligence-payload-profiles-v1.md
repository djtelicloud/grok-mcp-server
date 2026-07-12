# UniGrok Insider Intelligence payload profiles v1

These profiles evolve the Insider Intelligence DAG without changing the pinned
`IntelligenceCapsule` v1 envelope. Each profile occupies the existing
`body.payload = {"schema": ..., "data": ...}` seam and has its own immutable
schema digest:

| payload schema | JSON Schema | raw SHA-256 |
| --- | --- | --- |
| `org.grokmcp.gno_envelope.v1` | [`gno-envelope-v1.schema.json`](gno-envelope-v1.schema.json) | `4c7fb150b3f82738ae43d52669c8c663283807d42add1f4532f01527a4d70665` |
| `org.grokmcp.optibench_result.v1` | [`optibench-result-v1.schema.json`](optibench-result-v1.schema.json) | `dfc216d1855eb36e54829c3aca00434f0dc9845a6efc205c2c49016531accf81` |
| `org.grokmcp.agentic_dpo_pair.v1` | [`agentic-dpo-pair-v1.schema.json`](agentic-dpo-pair-v1.schema.json) | `7db601ccc11aaa94409383f88c7305a46b63a705176897aaa313f835b24bed84` |

The raw file digests are pinned in both language registries. Normative
cross-capsule rules are separately frozen in
[`intelligence-payload-semantics-v1.json`](intelligence-payload-semantics-v1.json),
SHA-256 `7464c2343c3edaadc21a14a880e689ef8e4b4ac0fa3fc07b2b6f37b08733545a`.
The public, digest-bound
[`intelligence-payload-conformance-v1.json`](intelligence-payload-conformance-v1.json)
at SHA-256 `6a0df82c82cd3bfbadc6ff1febf1e43b2d2a6446acd1b977ae7d3262de8d98f4`
contains shared body identities, receipt examples, exact NSGA-II outcomes, a
Needle projection identity, and adversarial reject vectors. Any semantic
change requires a new profile/spec version;
editing a `.v1` contract in place is a protocol fork.

## Scope and trust boundary

These are protocol contracts, not claims that every producer exists today.
The current local Swarm still stores its working state in contributor SQLite,
uses wall-clock latency and `tracemalloc`, ranks
`latency_ms`/`peak_mem_bytes`/`diff_bytes`, and routes with discounted UCB.
It does not currently emit GNO capsules, hardware-counter OptiBench capsules,
or preference pairs.

A capsule with a known profile becomes eligible for promotion only after all
of these independent gates succeed:

1. canonical envelope parsing and body-digest verification;
2. the registered profile schema and semantic validator;
3. evidence byte-count and SHA-256 verification;
4. complete parent-graph closure, bounds, and acyclicity checks;
5. signed-Git-commit or approved cloud-attestation publication authentication;
6. repository promotion policy.

An unknown payload schema may be retained in quarantine, but it must not be
executed, materialized, promoted, or exported. The envelope `signatures` array
does not authorize publication in v1.

The Python module is the current executable implementation of every v1
evidence and complete-graph gate. The browser TypeScript module performs the
same envelope/profile structural checks, but it is not a promotion authority
and does not independently resolve evidence blobs or recompute cohort graphs.

## GNO input-manifest envelope

`org.grokmcp.gno_envelope.v1` alternates task dispatch capsules with candidate
or failure result capsules.

- A dispatch has `body.kind = task`, explicit execution identity, and required
  `task_spec` plus `input_manifest` evidence. Its parents are the complete
  graph dependencies. The canonical manifest binds routing, execution, and
  every other declared non-secret artifact, including any Git input diff,
  context, system prompt, tool manifest, and policy.
- A result has `body.kind = candidate` or `failure`, matching its `outcome`.
  Its sole parent is its dispatch capsule. A candidate carries an
  `output_diff` evidence reference; a failure carries a `failure_receipt`.
  It must omit `body.execution`: executor identity is inherited from the
  manifest-bound dispatch and cannot be contradicted downstream.
- Every artifact reference binds both evidence name and SHA-256. Name-only
  lookup is forbidden, and evidence names are unique inside a known profile.
- Routing labels are literal. The existing Swarm is
  `discounted_ucb_v1`; it cannot be relabeled as UCB1. Decimal routing
  parameters are strings, never floats or nulls.
- The resolver verifies every declared byte count and SHA-256 and rejects
  secret-like shared text. Credentials and private consumer SQLite state are
  never evidence.

"Manifest-closed and stateless" does not mean mathematically deterministic. A
remote model, provider implementation, or sampling path may change. The
envelope makes every declared input replayable and every result attributable;
it does not promise byte-identical model output. Promotion also requires an
executor receipt proving the agent was sandboxed from undeclared ambient reads:
a manifest alone cannot prove that a process did not cheat.

The DPO proof resolver enforces bounded transitive parent closure and
acyclicity. General GNO TaskGraph admission and total evidence-byte limits
remain additional promotion-time rules. A parallelism index and a `1 -> 2`
branching policy are intentionally not serialized: neither has a frozen
definition or current implementation.

## Closed-population OptiBench result

`org.grokmcp.optibench_result.v1` is deliberately impossible to mint from the
current wall-clock Swarm result. It requires a closed population, canonical
counter samples, an environment manifest, a harness manifest, and successful
gate receipts. The evidence verifier recomputes population/cohort binding,
requires a passing tests gate, and derives every published median from the raw
samples before the capsule can enter a trusted graph.

Two non-interchangeable cohorts are supported:

| cohort | exact objective metrics in `body.metrics` |
| --- | --- |
| `perf_stat_instructions_v1` | `cpu_instructions_retired/instruction`, `diff_tokens/token`, `peak_memory_bytes/byte` |
| `callgrind_ir_v1` | `callgrind_ir/instruction_reference`, `diff_tokens/token`, `peak_memory_bytes/byte` |

All objectives minimize nonnegative integer values. `perf` instructions and
Callgrind instruction references are different physical measurements and
must never share a cohort or be compared as though they were identical. The
harness evidence fixes tokenizer identity for `diff_tokens` and measurement
method for peak memory.

Environment receipts have exactly `architecture`, `cpu_model`, `kernel`,
`operating_system`, and `runner`. Harness receipts have exactly `diff_metric`,
`peak_memory_method`, `source_commit`, `tokenizer`, and `version`; the source
commit is a full lowercase SHA-1 or SHA-256 object id. Identifier syntax alone
is not treated as safe: all shared textual receipt values also pass the pinned
secret detector.

NSGA-II ranking occurs only after the population closes. Finite crowding is a
reduced nonnegative rational `{numerator, denominator}`; a boundary point uses
`{"kind":"boundary"}`. IEEE infinity and floating-point crowding never enter
canonical bytes. Rational components are bounded to 128 decimal digits.
Repetition counts are odd, so an integer-valued median has one exact middle
sample and no hidden rounding rule.

Rank and crowding are not properties of one result, so the single-receipt
verifier cannot authorize promotion by itself. The closed-population verifier
requires exactly one evidence-verified benchmark per population candidate,
then recomputes every final rank and crowding value. It groups tied objective
values symmetrically: all tied nonconstant extrema are boundaries; tied
interior values receive the same nearest-distinct-neighbor fraction; constant
objectives add zero. Candidate IDs order output but never break mathematical
ties.

`baseline_relation` is mandatory because nondominated among mutants does not
mean better than the original program. The canonical counter receipt carries
the baseline objective tuple; the verifier recomputes whether the candidate
dominates, equals, is incomparable with, or is dominated by that baseline.

## Direct-Pareto preference pair

`org.grokmcp.agentic_dpo_pair.v1` records one conservative training/evaluation
label:

- chosen and rejected candidates are distinct, feasible members of the same
  closed population and measurement cohort;
- the chosen benchmark is final Pareto rank zero;
- the rejected benchmark has a worse final rank;
- the chosen objective tuple directly dominates that specific rejected tuple;
- the task plus chosen/rejected candidate and benchmark IDs are the five direct
  parents; the cohort proof hash-binds every additional population benchmark;
- task and candidates are evidence-verified GNO dispatch/result capsules;
- prompt binds the dispatch's sole `task_spec`, while chosen and rejected text
  bind the candidates' sole `output_diff` artifacts by SHA-256;
- prompt, outputs, population manifest, complete candidate-to-benchmark cohort
  proof, and dominance receipt are hash-bound evidence.

An arbitrary elite cannot be paired against an arbitrary dominated candidate:
front membership alone does not prove pairwise dominance. Infeasible mutants
are constraint failures, not objective-preference negatives. Online rewards or
provisional ranks are not admissible; the producer must recompute the final
closed front. V1 therefore supports only `pareto_dominance` under
`nsga2_direct_dominance_v1`. Human, judge, champion-policy, and constraint
preferences require later profiles with their own proof rules.

`build_preference_example` accepts a complete graph closure, verifies every
OptiBench receipt, recomputes final fronts and direct dominance, checks that the
chosen candidate beats baseline, verifies every transitive dependency uses a
registered GNO or OptiBench profile, and rejects secret-like shared text. The
cohort proof manifest binds the exact benchmark capsule selected for every
candidate, including candidates that are not one of the exported pair. Only
then does it return one provider-neutral record.
`render_preference_jsonl` emits that record with canonical key order and a
single trailing LF for storage or batch transport:

```json
{"chosen":"...","prompt":"...","rejected":"...","source_capsule":"ucap1:sha256:..."}
```

The record and JSONL are disposable projections, not second sources of truth.
Time decay or relevance weighting belongs in the Insider
materialized-view query policy; it never mutates immutable pair provenance.

## Canonical hashing and publication authentication in Python

```python
import hashlib

from src.intelligence_capsule import (
    build_envelope,
    canonicalize,
    capsule_id,
    validate_envelope_integrity,
)
from src.intelligence_payloads import validate_known_payload_profile

# `body` includes a registered payload and only SHA-256 evidence descriptors.
body_bytes = canonicalize(body)                 # restricted RFC 8785 profile
body_sha256 = hashlib.sha256(body_bytes).hexdigest()
identity = capsule_id(body)                     # ucap1:sha256:<body_sha256>

envelope = build_envelope(body)                 # signatures remains [] in v1
validate_envelope_integrity(envelope)
assert validate_known_payload_profile(body)       # separate semantic gate
wire_bytes = canonicalize(envelope)

assert envelope["digest"] == {
    "algorithm": "sha-256",
    "value": body_sha256,
}
assert identity == f"ucap1:sha256:{body_sha256}"
```

The signed publication object is the Git commit containing `wire_bytes`, not an
ad-hoc signature over a framework JSON object. Local publication uses the
contributor's configured SSH/GPG Git signing identity; cloud publication uses
an approved workflow attestation. The v1 `signatures` field is reserved until a
future protocol freezes a signature domain, key discovery, and trust roots.

## Needle conditioning boundary

Needle's playground accepts `tools` as a JSON string or array whose parsed top
level is an array. The server compacts it, and `generate` normalizes only
top-level tool names while preserving nested fields. The encoder receives the
query, a tools separator, and the serialized tools, truncated to the space
remaining in its 1,024-token encoder budget. Consequently, nested examples in
a valid tool definition are mechanically supported inference-time context.
They have no dedicated chosen/rejected semantics, so their ranking benefit must
be benchmarked. [UI request](https://github.com/cactus-compute/needle/blob/ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2/needle/ui/static/app.js#L69-L92),
[server normalization](https://github.com/cactus-compute/needle/blob/ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2/needle/ui/server.py#L156-L187),
[encoder path](https://github.com/cactus-compute/needle/blob/ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2/needle/model/run.py#L92-L137).

The provider-neutral DPO capsule does not claim Needle compatibility.
`org.grokmcp.needle_tools_context.v1` is a separate executor-only projection,
pinned by [`needle-tools-context-v1.schema.json`](needle-tools-context-v1.schema.json)
at SHA-256 `ac92a88b87e35254a7eef4a151d8743418ef102402022b228609743cbcbf7496`.
`build_needle_tools_context` wraps verified records inside one valid tool,
counts the exact compacted tools string with a caller-supplied pinned Needle
tokenizer, admits only whole examples in source-capsule order, and never exceeds
the shared query/tools budget. The wrapper is non-parametric inference-time
conditioning, not a weight or optimizer update. Query normalization trims only
the explicit CPython Unicode whitespace set used by the upstream server
(including NBSP and U+3000), requires strict UTF-8, and applies the same pinned
secret detector as the nested examples before any token count is accepted.
Input is bounded before regex/tokenizer work: at most 64 examples, 16 KiB of
query text, and 64 KiB per example record.

The separate playground fine-tune action generates `query`/`tools`/`answers`
JSONL and runs an actual training job that writes a new checkpoint. UniGrok
keeps that path distinct from ephemeral nested-example conditioning.
[Fine-tune implementation](https://github.com/cactus-compute/needle/blob/ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2/needle/ui/server.py#L520-L647).

Needle remains a non-authoritative shadow ranker. It may score sanitized,
pre-enumerated candidate summaries, but its output cannot authorize reads,
execution, mutation, or promotion. Exact source and model artifacts must be
pinned and isolated before loading; benchmark recall and end-to-end answer
quality against simpler retrieval baselines first. Needle's dedicated repo and
model are MIT-licensed; its 26M/6,000-prefill/1,200-decode figures are vendor
claims until UniGrok reproduces them, and they are not retrieval or
nested-example benchmarks.

## SQLite boundary

Nothing in these profiles opens or changes the public consumer database. Public
MCP consumers retain private local SQLite as runtime truth. A future Insider
SQLite query view may index only trusted Git-DAG capsules; it must remain
deletable and reconstructible from trusted refs without tunnels or cloud calls
into localhost.
