---
name: python-superiority
description: >-
  Serial Forge Swarm optimization loop for UniGrok Python files. Use only when
  the sponsor asks for the Python superiority campaign or a measured per-file
  optimization pass.
---

# Python superiority loop

Optimize one logical production target at a time with measured Forge Swarm
evidence. A target may remain one file or become a bounded split bundle. This
is a search-and-measure campaign, not a documentation factory.

## Non-negotiable gates

1. Work serially. Never run parallel PR-creation batches, and never maintain
   more than one open draft PR from this campaign.
2. Start each cycle from current `origin/main` in a fresh
   `cursor/python-superiority-loop-<slug>` task branch and contained worktree.
3. Verify the contributor Forge is connected to that exact worktree and reports
   `can_use_swarm=true`. If Forge is unavailable, stop the cycle as **Blocked**.
   Never fall back to a plan-only PR because Forge is unavailable.
4. Run `plan_swarm_campaign` for the target scope. Select one function marked
   `searchability=ready`; do not infer searchability from file size or parse
   time. Run `analyze_code_for_swarm` when a pasted-source preview is useful.
5. Define a real correctness oracle and a deterministic, non-trivial benchmark
   before `start_code_swarm`. The benchmark must exercise the focus function,
   include warmup and repeated samples, and emit the `SWARM_BENCH` contract.
6. Treat Forge status JSON as the source of truth. Projected LOC, complexity,
   parse time, compile time, and proposed architecture are not measured
   performance improvements.
7. When one original file becomes multiple files, treat the new files as one
   comparison bundle. Measure the same public entry point end to end on both
   revisions; never sum or average isolated per-file performance percentages.

## Cycle outcomes

Every file ends in exactly one of these outcomes:

### Measured optimization

Open one draft PR only when all of the following are true:

- the Swarm completed and produced a feasible champion;
- the focused tests and full repository suite pass on the applied champion;
- the same benchmark command and fixture measured baseline and champion;
- the result is stable enough to distinguish from noise; and
- the diff changes one logical production target. A split may create multiple
  implementation files only when the PR includes an explicit bundle manifest
  and preserves the original public behavior; supporting tests must prove that
  same target boundary.

The PR body must include the exact Forge task id, target path, focus node,
benchmark command, sample count, current head SHA, and this table populated
with measured values:

| Metric | Baseline | Champion | Change |
| --- | ---: | ---: | ---: |
| End-to-end latency (ms) | measured | measured | measured % |
| Bundle peak memory (bytes) | measured | measured | measured % |
| Focused oracle | exact command | pass | n/a |
| Full suite | exact command | pass | n/a |

Do not write `n/a`, `projected`, or estimates into the latency or memory cells.
If the champion has no meaningful measured win, record **No change** locally
and open no PR.

### Split refactors (one file to many)

The PR must define one comparison unit:

- original file and exact base SHA;
- candidate head SHA and every file in the replacement bundle;
- stable public import, callable, or request path exercised on both revisions;
- identical fixture, Python version, environment, warmup, and sample count; and
- one correctness oracle that crosses the whole bundle boundary.

Measure warm end-to-end operation latency and peak memory for the whole logical
operation. If import or startup cost matters, measure it separately in a fresh
process on both revisions. File-level parse/compile timings may be diagnostic,
but they must not be summed, averaged, or presented as the bundle's performance
result. LOC may be totaled across the bundle only as a structural metric.

### Refactor plan

A refactor-plan PR is allowed only when the file has a concrete structural
blocker that prevents safe function-level search and the plan names a bounded,
reviewable migration. It must:

- cite the Forge searchability result or specific oracle/benchmark blocker;
- separate measured baseline facts from projections;
- label every forecast as **projected, not measured**;
- include compatibility, test, migration, and rollback steps; and
- remain the campaign's only open draft PR.

Do not mark a plan Ready merely because it reduces projected LOC. Do not create
one plan PR per unsearchable file; group only tightly coupled files when one
shared architectural decision is genuinely required.

### No change

When the file is already adequate, the benchmark is inconclusive, or no
feasible champion wins, record the disposition in the local campaign tracker
and continue only after the current cycle is closed. No PR is the correct result.

## Handoff

Before pushing, recheck that no other open `cursor/python-superiority-loop-*`
draft exists. Push the one task branch, open or update one draft PR, and hand
the exact head to Codex as **Ready for supervisor**. Grok's PR body, canvas, and
Forge receipt are contributor evidence; they are not final approval. Codex must
independently check out the exact base and head, rerun the oracle and benchmark,
and create a separate report from
`.codex/threads/templates/python-superiority-review.md`. Do not rewrite Grok's
report to reconcile discrepancies. Never land or merge from this campaign.
