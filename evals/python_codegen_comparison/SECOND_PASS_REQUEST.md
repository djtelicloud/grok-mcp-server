# Grok second-pass swarm request

Revise your previous `async_ttl_cache.py` implementation using a full swarm review.
Correctness against the frozen implementation plan is the hard gate; after correctness,
prefer the simplest clear implementation with no unnecessary allocations or machinery.

You are receiving your complete first-pass code and the complete original plan below.
The evaluator is frozen and will not be changed during this second pass.

## Verified first-pass results

The first-pass candidate passed 12 of 15 contract tests. The same three failures were
reproduced in all 25 runs:

1. **Cancellation isolation:** cancelling the first task directly awaiting the shared
   `asyncio.Future` cancelled that future. Another existing waiter then received
   `CancelledError`. Requirement 7 says one waiter must not cancel the loader or affect
   other waiters.
2. **Expiration before capacity enforcement:** when an expired entry had become MRU,
   completing a new load evicted a fresh LRU entry instead of first removing expired
   entries. The result lost a valid value and incremented `evictions` incorrectly.
3. **Finite real TTL:** `fractions.Fraction(1, 2)` was rejected because validation was
   restricted to `(int, float)`, although the plan accepts finite real values and rejects
   booleans.

Review the entire implementation rather than applying blind line edits. Preserve all
behavior that already satisfies the plan, fix the verified defects, and look for any
closely related cancellation, stale-flight, exception-observation, TTL, or LRU issue.

## Output contract

Return only the complete revised contents of `async_ttl_cache.py`. Do not return a diff,
Markdown fences, tests, explanation, vote commentary, or changelog.

## Frozen artifact hashes

- Original plan SHA-256: `7b3830c166fd618d95e4d911afc0069750e2624a0e4909d4bf3d6e38ef94f5a8`
- First-pass Grok candidate SHA-256: `5a6cc93e89feba772287a381874ad09ebfaa52275211e9cd7bbfb214ed352508`
- Evaluator SHA-256: `cf00273eaf55546b024e2102e9ccf32f1bfb87a62a493263fcba33351e5c27bb`

The original plan and first-pass code are appended verbatim to the task sent to the
swarm after this request header.
