# Python Superiority Independent Review

Use this report for Codex review of one Cursor/Grok Python superiority PR. The
contributor's PR body, canvas, Forge receipt, and tables are evidence to verify,
not text for Codex to repair. Do not edit or normalize Grok's reporting. Record
discrepancies here and send actionable code fixes to the contributor branch or
a separate Codex repair branch.

## Verdict

- PR:
- Exact base SHA:
- Exact candidate SHA:
- Forge task id:
- Verdict: `approve measured win | no material win | needs changes | blocked`
- Public-results status: `held | approved`

## Comparison unit

- Original file:
- Original public entry point:
- Candidate bundle files:
- Candidate public entry point:
- Compatibility invariant:

For a one-file-to-many split, the candidate bundle is one unit. Do not sum or
average per-file latency, memory, parse, compile, or percentage changes.

## Independent method

- Clean base worktree:
- Clean candidate worktree:
- Python/runtime version:
- Machine/runner:
- Correctness command:
- Benchmark command and fixture:
- Warmup count:
- Measured sample count:
- Cold-start method, if relevant:

Run the same public operation and fixture in both worktrees. Measure the full
bundle end to end. If import/startup matters, run it in fresh processes and
report it separately from warm operation latency.

## Independent results

| Metric | Original | Candidate bundle | Change | Reviewer note |
| --- | ---: | ---: | ---: | --- |
| Warm end-to-end latency, median (ms) | | | | |
| Warm end-to-end latency, p95 (ms) | | | | |
| Process peak RSS, median (bytes) | | | | |
| Process peak RSS, max (bytes) | | | | |
| Traced Python allocation, median (bytes) | | | | |
| Traced Python allocation, max (bytes) | | | | |
| Cold import/startup, median (ms), if relevant | | | | |
| Focused oracle | | | n/a | |
| Full suite | | | n/a | |
| Total implementation LOC (structural only) | | | | not performance evidence |

## Contributor-claim comparison

| Claim in Grok report | Independently reproduced? | Difference |
| --- | --- | --- |
| | | |

## Findings and required changes

1. None, or list concrete correctness, compatibility, measurement, or code
   findings with exact paths.

## Approval boundary

Codex approval applies only to the candidate SHA and measurements named above.
Do not link the result publicly until the verdict is `approve measured win` and
the approved code is merged to `main`. Keep raw methods and receipts in the
private intelligence lane; publish only the approved code, focused product
tests, and minimal reproducible table.
