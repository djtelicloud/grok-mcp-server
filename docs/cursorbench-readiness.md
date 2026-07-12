# CursorBench readiness

UniGrok's goal is to compete at the top of CursorBench. The repository must
keep the internal evidence and the public claim separate until Cursor has run
the system through its private benchmark.

## Official boundary

[CursorBench](https://cursor.com/cursorbench) is a public leaderboard over an
internal task suite sourced from real Cursor sessions. Cursor does not publish
the tasks, harness, grader weights, or a public submission workflow. The live
target must be checked before every comparison; CursorBench 3.2 added
instruction-following and advanced-tool-use tasks on July 8, 2026.

Therefore this repository must not publish an "official CursorBench score" or
"#1 on CursorBench" claim from local fixtures, cassette replay, or a lookalike
benchmark. That claim requires an accepted Cursor-run result for the named
benchmark version.

## What the checked-in foundation proves

- Offline `agent_multifile` replay proves the real AgentLoop dispatches a
  project-file listing, two bounded file reads, and pytest. Structural graders
  derive success from the actual observations; the cassette's final sentence
  cannot turn a failing test run green.
- Swarm golden targets prove target discovery, AST focus extraction, oracle
  coverage, benchmark-contract parsing, candidate evaluation, and the durable
  status payload.
- The live Swarm sweep is explicit opt-in, contributor-only, sequential, CLI
  readiness-gated, zero-metered-cost enforced, and never a CI requirement.

These are capability and regression checks. Cassette replay scripts the model's
choices, and one live task has no statistical power, so neither estimates a
CursorBench score.

## Competitive evidence ladder

1. Keep the hermetic suite green on every change:

   ```bash
   uv run pytest -q
   uv run python -m evals run --check-baseline --no-calibration
   ```

   The opt-in live multi-file smoke must run through a contributor container
   that already has the server-side API credential; it fails the command when
   the selected task does not pass:

   ```bash
   docker compose -f docker-compose.dev.yml run --rm --no-deps \
     grok-mcp /app/.venv/bin/python -m evals run \
     --task agent_multifile --live --require-pass --no-calibration
   ```

2. Build a held-out, independently authored proxy suite spanning all published
   CursorBench families: multi-file edits, refactors, bug fixes, codebase
   understanding, bug finding, planning, review, instruction following, and
   advanced tool use. Use short ambiguous requests, accepted patches, tests,
   and blinded human or agentic grading.
3. Run real production-harness trajectories in disposable worktrees, with
   repeated trials and consistent score, cost, token, and step accounting.
4. Keep public examples outside the held-out scoring set and audit for network
   retrieval or training contamination.
5. Obtain a Cursor research/provider evaluation path. Publish a ranking only
   after Cursor accepts and reports the exact versioned result.

Until step 5, describe this work as a **CursorBench-aligned capability suite**,
not a CursorBench reproduction.
