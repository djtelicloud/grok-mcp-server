# Historical source-publication checklist — public graft go/no-go

This file records the 1.1 public-history graft. Do not rerun its unrelated-history
procedure without a new repository baseline and explicit publication decision. It is
not the Cloud Run release checklist; hosted deployments use
[`docs/remote-mcp-deployment.md`](docs/remote-mcp-deployment.md).

Status legend: [ ] open · [x] done · [~] partially proven

## Go/no-go gates (ordered; 1–2 failing = no-go)

1. [~] **No-key + broken-permission soak** — keyless container, concurrent
   sessions, permission-denying clients: assert zero unexpected metered calls
   and no silent infinite retry. (First pass automated in
   `scripts/soak_nokey.py`; a longer unattended soak is still recommended.)
2. [~] **SQLite job durability under chaos** — kill mid-hive, restart
   mid-merge, concurrent sessions: no corruption, "lost" jobs honest,
   completed jobs recoverable. (Restart-recovery proven; concurrent-writer
   discipline is single-writer by design — do not point two containers at one
   state volume.)
3. [ ] **Hive apply safety on real IDE diffs** — merge must fail closed on
   renumbered/mismatched dif-vote anchors. (Design present: anchors cite
   numbered draft lines; adversarial IDE-diff test not yet automated.)
4. [x] **Ladder + auto++ economics** — level sweeps benchmarked; auto++ never
   picks hive for trivial prompts (heuristic + router votes); voter counts
   dynamic; costs receipted per stage.
5. [x] **Graft freeze hygiene** — dogfood optimizer is a manual client-side
   script (never runs server-side or automatically); CHANGELOG + version
   (1.1.0) + README match the binary; kill switches: `UNIGROK_ENABLE_METERED_API`,
   `UNIGROK_SHADOW_DONE_VOTE`, `UNIGROK_VOTE_MAX_OUTPUT`.

## Graft procedure (when gates pass)

- Merge the staging checkout INTO the
  public repo (`djtelicloud/grok-mcp-server`) with
  `--allow-unrelated-histories`, on a branch + PR — never direct to main.
- Public repo keeps its 638-commit history; staging lineage lands on top.
- Before push: confirm `bench-results/` policy (commit or ignore), scrub any
  local-only paths from docs.

## Open decisions (not blockers)

- Default `ultra` voter count: sweep showed 1–3 voters match 5 on easy tasks;
  dynamic sizing already handles routine cases. Revisit after hard-task data.
- Bare `auto` fallback policy stays `cross_plane` (key-in-.env = consent);
  flip to `same_plane` only if soak shows surprise spend.
- Shadow done-vote soak: flag exists, off; enable during a soak window to
  gather regex-vs-vote agreement before any regex retirement.

## Post-launch backlog (approved ideas, deliberately deferred)

- Any third execution plane is deferred. It would require an independently reviewed
  credential boundary, billing policy, provider contract, and soak data; the shipping
  gateway supports only Grok Build CLI and the xAI developer API.
- Courier bridge: caller-run tests/bench couriered back for *proven* user-code
  optimization (forge loop for arbitrary files).
- Function-path router; self-healing error messages; readability hive-vote
  layered on the mechanical gate.
- Onboarding extraction (from internal donor research): dynamic settings plan (template +
  deprecation map + classify-before-write, IDE executes) and reflex/synapse
  memory (short auto-injected notes; per-turn relevance routing — maps onto
  the existing facts system). See docs/onboarding-extraction.md.
- omlx eval discipline (fixed-seed stratified sampling, thinking-budget
  provenance) for the benchmark suite.
- Structured micro-emit (silent-think full port) for short-answer hard tasks.
- Needle tier: keep `needle_active: false` truthful; revive only behind the
  promotion gates in the private design docs.
