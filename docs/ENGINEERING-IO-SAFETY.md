# Engineering · I/O safety & fair dual-plane benches

**Audience:** UniGrok public core contributors  
**Status:** Live

## 1. Multi-writer state

Prefer **one durable store with real concurrency control** (for example SQLite WAL,
busy timeout, and explicit write serialization) over plain text files that many
workers append and rewrite.

If you must use a shared append-only text file across processes:

1. **Exclusive lock** around the critical section
2. **Hot path = append only**
3. **Trim rarely** (amortized); re-read truth from the file — do not trust a sticky
   in-process line counter across workers
4. Keep a **fixed tail** (bounded memory)

Anti-pattern: read the entire file and rewrite it on every line.

## 2. Size-gate math

If code says “when size > X, read the last Y bytes/window,” then **X and Y must be
consistent** (window ≤ gate, units match). Mismatched gates cause silent logic bugs.

## 3. Fair dual-plane benches

When comparing behaviors, change **one** control at a time:

- `depth` / shape **or**
- `fallback_policy` (`same_plane` vs `cross_plane`)

not both in the same comparison if you want a clean lesson.

Do not treat editorial “architecture scores” as CI gates. Prefer executable tests for
recovery and policy literals (already in-tree), for example:

- cross-plane and same-plane recovery behavior in the team harness tests
- fallback policy limited to `same_plane` | `cross_plane` (no third “local model”
  policy value)
- public tool surface that keeps normal callers on **intent**, not hidden plane knobs

## 4. Operator routing (behavior, not deployment nicknames)

Public UniGrok keeps **caller intent** — clients should not be taught to pass hidden
model or plane knobs for normal use.

For operators running **multiple deployments**, prefer deeper/safer paths for
concurrent file and multi-writer work; lighter paths for simple short answers. That is
operational guidance, not a public API change.
