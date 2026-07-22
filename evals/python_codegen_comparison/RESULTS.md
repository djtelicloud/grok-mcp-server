# Python code-generation comparison

Date: 2026-07-19

## Question

Given the same synthetic implementation plan, does Codex or the connected Grok MCP
produce the stronger Python implementation?

This is a one-task comparison, not a universal model ranking. It includes a blind
first pass and a feedback-assisted Grok swarm revision; those rounds answer different
questions and should not be treated as equivalent samples.

## Method

1. Freeze `PLAN.md` before either candidate is evaluated.
2. Generate and freeze `candidate_codex.py` before viewing Grok's output.
3. Send the exact contents of `PLAN.md` to `grok.agent` with web search, X search,
   and remote code execution disabled.
4. Freeze `candidate_grok.py` before writing the behavioral evaluator.
5. Derive `evaluate.py` only from requirements stated in `PLAN.md`.
6. Run both candidates using Python 3.12 inside the `unigrok:1.1.0` image, with no
   network, a read-only filesystem, and identical tests.
7. Repeat the final suite 25 times per candidate to check async stability.
8. Freeze the evaluator, original plan, and both first-pass candidates.
9. Give Grok its complete first-pass code, the complete original plan, and precise
   descriptions of the three reproducible failures in `SECOND_PASS_REQUEST.md`.
10. Request an `ultra` hive revision with five reviewer personas. Disable web and X
    search; leave the harness's remote code-execution capability available.
11. Save the merged output as `candidate_grok_swarm.py`, then run the unchanged
    evaluator once and repeat it 25 times under the same container restrictions.

For the first pass, Grok used `grok-4.5` on the subscription CLI plane, selected a
direct route, and reported $0 metered API cost. Its verified telemetry receipt is ID
379.

For the second pass, Grok used the `ultra` hive route with five votes and a merge pass
across the CLI and API planes. The receipt reports five personas (`critic`, `gate`,
`bounty`, `spec`, and `failures`), a session telemetry recorded.
That receipt is recorded as a verified success. The known `xai_list_files` timeout was
not involved in either round; no file-list tool was used.

An initial evaluator run advanced a fake clock by `4.999 + 0.001`; binary floating
point left the value slightly below the exact TTL boundary and marked both candidates
wrong. The evaluator was corrected to set the boundary timestamp exactly. Neither
candidate was changed.

## Results

| Round | Candidate | Contract tests | Repeated outcome | Lines | Result |
|---|---|---:|---:|---:|---|
| Blind first pass | Codex | 15/15 | 25/25 identical passes | 179 | Pass |
| Blind first pass | Grok | 12/15 | 25/25 identical results | 209 | Fail |
| Informed second pass | Grok ultra hive | 15/15 | 25/25 identical passes | 224 | Pass |

All three candidates were syntactically valid Python 3.12, used only the standard
library, were fully annotated without `Any`, ran different-key loaders concurrently,
coalesced same-key loads, handled loader failures, detached invalidated loads, and
consumed unobserved background-task exceptions.

## First-pass Grok defects

### 1. Cancellation leaks between waiters

`candidate_grok.py` stores a shared `asyncio.Future` and every caller awaits it
directly. In asyncio, cancelling a task that directly awaits a future cancels that
future. A cancelled initial waiter therefore cancels the shared future and every other
waiter receives `CancelledError`, contrary to requirement 7.

Codex awaits the shared producer task through `asyncio.shield`, so cancelling one
waiter cannot cancel the producer or other waiters.

Severity: high. This breaks the central cancellation-safety contract under ordinary
request cancellation or timeout behavior.

### 2. Expired MRU entry can evict a fresh LRU entry

Grok inserts a completed value and immediately performs size-based LRU eviction without
first removing other expired entries. If an expired entry was recently touched before
its expiration, it can remain MRU while insertion evicts a still-fresh LRU entry. This
also incorrectly increments the eviction counter for a capacity event caused only by
stale data.

Codex purges expired entries at the loader-completion timestamp before applying the
size limit.

Severity: medium. It loses a valid cached value and produces inaccurate statistics.

### 3. Finite `Real` values are narrowed to `int | float`

The plan accepts finite real numbers and rejects booleans. Grok hard-codes an
`isinstance(ttl, (int, float))` check, rejecting standard-library values such as
`fractions.Fraction(1, 2)`. Codex validates against `numbers.Real` and then normalizes
to `float`.

Severity: low. This is a runtime contract mismatch, not a concurrency failure.

## What the swarm changed

The swarm corrected all three verified defects:

1. It shields each await of the shared delivery future so cancelling one waiter no
   longer cancels the shared result.
2. It purges expired entries before applying the cache size limit after a load.
3. It validates TTL values against `numbers.Real`, while continuing to reject booleans
   and non-finite values.

It also added an explicit callback that observes exceptions set on a delivery future.
The revised implementation passed the frozen evaluator without any evaluator or plan
change. Its SHA-256 is
`4c69aa3ff6a8296ae13c3585a462b3a2159f0792dcbe478f06c7e745ba5974c3`.

## Qualitative comparison

Grok's implementation is readable and has stronger explanatory docstrings. Its
separate producer task and delivery future are a reasonable architecture, and its
identity-based detachment logic is otherwise sound. The missing shield is a small code
change with a large correctness impact.

Codex's implementation is 30 lines shorter and uses a simpler shared-task design. It
more directly models the required cancellation semantics, handles stale-entry cleanup
before capacity enforcement, and follows the broader numeric requirement.

The revised Grok implementation preserves its separate producer-task and delivery-
future architecture. It is now correct under every stated test and has strong
explanatory documentation, but at 224 lines it is 45 lines longer than Codex's
candidate. The swarm demonstrated useful correction and review behavior, although it
required the exact failure signal, a second generation round, five reviewers, a merge
pass, and metered API usage.

## Verdict

For blind first-pass code generation, Codex produced the stronger implementation:
15/15 versus 12/15, with less code and no repair feedback.

After receiving the three failure descriptions and running a five-reviewer swarm, Grok
reached correctness parity at 15/15 and stayed stable in all 25 repetitions. Codex
still wins on concision and first-pass efficiency; the Grok swarm wins credit for
successfully diagnosing and repairing its own architecture once given concrete test
feedback. The second-pass result is evidence that the swarm is an effective optimizer,
not evidence that the original one-shot outputs were tied.

## Reproduce

From the project root:

```sh
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  -v "$PWD/evals/python_codegen_comparison:/eval:ro" \
  -w /eval --entrypoint /app/.venv/bin/python unigrok:1.1.0 \
  -B evaluate.py candidate_codex.py

docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  -v "$PWD/evals/python_codegen_comparison:/eval:ro" \
  -w /eval --entrypoint /app/.venv/bin/python unigrok:1.1.0 \
  -B evaluate.py candidate_grok.py

docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  -v "$PWD/evals/python_codegen_comparison:/eval:ro" \
  -w /eval --entrypoint /app/.venv/bin/python unigrok:1.1.0 \
  -B evaluate.py candidate_grok_swarm.py
```
