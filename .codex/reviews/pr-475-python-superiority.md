# Python Superiority Independent Review — PR #475

## Verdict

- PR: `djtelicloud/grok-mcp-server#475`
- Exact base SHA: `ea15d046c25b6e58a9a3d8d118d4191c161efc07`
- Exact candidate SHA: `ba2daa269956997fe28fe8449099f8eed53a519c`
- Forge task ids: `f29b306130e945f8a34eaa44b91fcb39`,
  `eca231fdad094aab9e909728f13035df`
- Verdict: `needs changes`
- Public-results status: `held`

PR #475 is a documentation inventory plus an unapproved Forge adapter edit,
not measured optimization evidence and not an actionable bulk refactor plan.
Do not hand it to the normal Codex landing loop as approved work.

## Inspected packet

- 77 commits and 71 changed files: 69 plans under `docs/design/`, one scoreboard
  reclassification, plus one production edit in `src/swarm/preflight.py`
- 1,845 added lines and 5 deleted lines; no new test, oracle, or benchmark code
- Cursor campaign report: 201 plans done, 9 skipped, 0 pending
- 126 historical per-file draft PRs plus the 75 in-tree continuation commits
- reclassified scoreboard: 0 measured wins, 1 Swarm-ready target held, 77 plans
  kept, and 133 targets skipped
- no unresolved review threads on #475 at review time
- contributor baseline at prior head `bda882ffbf6e2436b09af5d316a4c3cfb26ae1ad`:
  2,202 tests passed in 102.31 seconds
- exact-head attribution failed across the plan history; the remaining new-head
  CI was still running at review time

The original campaign window contained no Swarm task. After Codex requested a
real measured candidate, Grok started task
`f29b306130e945f8a34eaa44b91fcb39` for
`src/swarm/pareto.py::fast_non_dominated_sort`, then task
`eca231fdad094aab9e909728f13035df` for
`src/routing.py::extract_routing_features`. Both failed during import
provenance before generation because Forge stripped the required `src.` package
prefix. Neither produced a candidate or performance verdict.

Grok then committed an unconditional keep-`src` repair, followed by an honest
scoreboard reclassification at the current #475 head. The scoreboard correctly
labels projected LOC as projected and measured wins as zero; it is bookkeeping,
not performance evidence. The adapter edit fixes UniGrok but regresses
conventional repositories where `src/`
is a package container rather than the package name, and it adds no regression
tests or generated API mirror update. Codex's draft #476 instead detects
`src/__init__.py` inside the sandbox, covers both layouts, and passed the full
2,204-test suite. #476 supersedes the #475 repair for normal-Codex integration.

Codex also reproduced both task fixtures against #476. The held Pareto target
passes real sandbox preflight as `src.swarm.pareto` with 86.7% focus coverage,
a stable 0.258419 ms median, and 984-byte peak memory. The routing task's exact
benchmark instead omits required keyword-only `reason_score`; after adding the
minimal fixture value only for diagnosis, it passes as `src.routing` but has a
19.37% latency noise floor. Therefore the routing task is not retry-ready, and
the Pareto target must remain the first and only candidate after CONTINUE.

## Quantitative plan audit

The 69 files in #475 contain 69 unique target paths. Their stated source LOC
ranges from 0 to 208, with a median of 43:

| Finding | Count |
| --- | ---: |
| Targets at or below 50 LOC | 37 / 69 |
| Targets at or below 20 LOC | 20 / 69 |
| Proposed facade is at least as large as the original | 20 / 69 |
| Positive reported primary-file LOC change | 19 / 69 |
| Generic `split module / domain seams` plan | 12 / 69 |
| Test targets | 36 / 69 |
| `src/` targets | 18 / 69 |
| `evals/` targets | 10 / 69 |

Examples that disprove bulk refactor-worthiness:

| Target | Original LOC | Proposed facade LOC | Reported change |
| --- | ---: | ---: | ---: |
| `evals/campaigns/__init__.py` | 0 | 20 | +1900% |
| `src/tools/__init__.py` | 2 | 20 | +900% |
| `main.py` | 5 | 20 | +300% |
| `src/version.py` | 10 | 20 | +100% |
| `evals/tasks/swarm_targets/nsquared_dedup/dedup.py` | 11 | 20 | +82% |

These are generated size targets, not evidence that a refactor is warranted.

## Missing refactor evidence

Across all 69 plan files, zero include any of the following required evidence:

- exact base SHA or per-target head SHA
- compatibility or public-entry-point invariant
- concrete replacement bundle paths
- migration or rollback procedure
- exact correctness test command
- benchmark command or fixture
- peak-memory measurement
- `projected, not measured` label

The plans measure only the future facade's LOC. They do not total the LOC of the
new modules, so the reported reductions cannot compare the original file with
the complete replacement bundle. Per-file parse/compile baselines shown in the
canvas are likewise not end-to-end performance measurements.

## Campaign completeness

#475 contains only the final 69 plan files. The other 132 planned targets are
distributed across 126 historical draft PRs or reported as skipped. There is
no canonical manifest binding all 210 inventory entries to one of:

- measured optimization with Forge task and exact head;
- actionable refactor plan;
- verified no-change disposition; or
- explicit skip with reason.

Therefore `pending=0` proves traversal completion, not reviewable campaign
completion.

## Required replacement packet

1. Keep Grok's existing PRs and canvas unchanged as contributor history.
2. Produce one canonical 210-target manifest with exact path, disposition,
   evidence link, and head SHA where applicable.
3. Use `plan_swarm_campaign` searchability results and real Forge task ids;
   Forge unavailable must pause search rather than convert every file to a
   refactor plan.
4. Mark trivial modules, fixtures, and already-adequate files `no change` or
   `skip` instead of manufacturing a split.
5. For each real refactor plan, name concrete module paths, public compatibility
   invariants, migration order, rollback, exact tests, and the benchmark that
   will judge the complete bundle.
6. For one-file-to-many work, compare the exact original revision against the
   entire candidate bundle through the same public operation. Report total
   bundle LOC as structural context and measure end-to-end latency and peak
   memory; never sum or average per-file percentages.
7. Submit at most one implementation candidate at a time. Codex independently
   reproduces its oracle and benchmark and writes a separate review report.
8. After CONTINUE, start from landed `main` on a clean task branch. Improve and
   freeze the oracle/benchmark before changing production code or capturing the
   baseline, then apply the identical method to the exact original and candidate.
9. Grok may use team/hive generation followed by Swarm refinement, but the
   baseline harness must execute the exact original implementation. Intermediate
   team output is diagnostic; approval compares exact original with the final
   combined candidate under the frozen method.
10. Do not retry `extract_routing_features` until its required keyword fixture
   and high-noise benchmark are replaced with a stable, reviewable oracle.

## Approval boundary

No #475 plan, historical per-file draft, or unconditional import-layout repair
is approved by this review. Public linking remains held. Normal Codex may
review and land the separate evidence gate in #422 and the layout-safe Forge
repair in #476 after their own exact-head checks and protections pass; it
should not merge #475 based on this packet.
