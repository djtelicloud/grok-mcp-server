# Python superiority loop — projected LOC cut

> **Snapshot:** tracker `2026-07-16T17:12:45Z` · **76/210** files with Ready refactor plans · loop still in progress.
>
> These are **projected** facade/shim LOC targets from Ready draft PRs — not landed merges yet.
> Codex Sol High can review Ready drafts; **merge stays the normal Codex land path**.

## Share this

- **Visual board (recommended):** [python-superiority-loop.html](python-superiority-loop.html) — teal/cyan Control-Center vibe, self-contained
- **This markdown:** GitHub-renderable KPI + full table (refresh-friendly)
- **Canvas source snapshot (history only):** [snapshots/python-superiority-loop-metrics.canvas.tsx](snapshots/python-superiority-loop-metrics.canvas.tsx) — not the public runtime; IDE canvas is a local companion under `~/.cursor/projects/.../canvases/`

## Headline KPIs

| KPI | Value |
|---|---:|
| **Overall LOC reduction** | **-90.5%** |
| Avg file % cut | -88.3% |
| Files with plans | 76 / 210 (36%) |
| Ready draft PRs | 76 (#342–#420, skipping docs #408) |
| LOC before → after (projected) | 86,509 → 8,180 (-78,329) |
| Next pending | `tests/test_supervisor_approval.py (~301 LOC)` |

<div align="center">

<p style="font-size:3rem;font-weight:700;letter-spacing:-0.03em;margin:0.5rem 0;color:#16f0ae">-90.5% overall</p>
<p style="color:#8b9eb0;margin:0">Projected facade/shim cut across 76 Ready plans · 76/210 inventory</p>

</div>

## Biggest % wins

| File | Before | After | Δ% | PR |
|---|---:|---:|---:|---|
| `test_utils.py` | 7,848 | 200 | -97.5% | [#345](https://github.com/djtelicloud/grok-mcp-server/pull/345) |
| `test_provider_broker.py` | 4,707 | 150 | -96.8% | [#346](https://github.com/djtelicloud/grok-mcp-server/pull/346) |
| `utils.py` | 13,830 | 800 | -94.2% | [#342](https://github.com/djtelicloud/grok-mcp-server/pull/342) |
| `test_http_server.py` | 2,371 | 150 | -93.7% | [#349](https://github.com/djtelicloud/grok-mcp-server/pull/349) |
| `intelligence_payloads.py` | 1,749 | 120 | -93.1% | [#350](https://github.com/djtelicloud/grok-mcp-server/pull/350) |

## All Ready plans (refresh this table)

<!-- LOOP_METRICS_TABLE_START -->
| Loop | File | Before | After | Δ LOC | Δ% | Latency (baseline) | PR | Status |
|---:|---|---:|---:|---:|---:|---|---|---|
| 1 | `src/utils.py` | 13,830 | 800 | -13,030 | -94.2% | parse 53.0ms / compile 42.0ms | [#342](https://github.com/djtelicloud/grok-mcp-server/pull/342) | Ready |
| 2 | `tests/test_utils.py` | 7,848 | 200 | -7,648 | -97.5% | parse 29.0ms / compile 26.0ms | [#345](https://github.com/djtelicloud/grok-mcp-server/pull/345) | Ready |
| 3 | `tests/test_provider_broker.py` | 4,707 | 150 | -4,557 | -96.8% | parse 17.0ms / compile 14.0ms | [#346](https://github.com/djtelicloud/grok-mcp-server/pull/346) | Ready |
| 4 | `src/providers/broker.py` | 3,122 | 600 | -2,522 | -80.8% | parse 9.0ms / compile 8.0ms | [#347](https://github.com/djtelicloud/grok-mcp-server/pull/347) | Ready |
| 5 | `src/http_server.py` | 2,874 | 400 | -2,474 | -86.1% | parse 11.0ms / compile 9.0ms | [#348](https://github.com/djtelicloud/grok-mcp-server/pull/348) | Ready |
| 6 | `tests/test_http_server.py` | 2,371 | 150 | -2,221 | -93.7% | parse 10.0ms / compile 7.0ms | [#349](https://github.com/djtelicloud/grok-mcp-server/pull/349) | Ready |
| 7 | `src/intelligence_payloads.py` | 1,749 | 120 | -1,629 | -93.1% | parse 8.0ms / compile 5.0ms | [#350](https://github.com/djtelicloud/grok-mcp-server/pull/350) | Ready |
| 8 | `evals/.../stage1_harness.py` | 1,644 | 200 | -1,444 | -87.8% | parse 5.0ms / compile 5.0ms | [#351](https://github.com/djtelicloud/grok-mcp-server/pull/351) | Ready |
| 9 | `src/tools/system.py` | 1,525 | 120 | -1,405 | -92.1% | parse 6.0ms / compile 5.0ms | [#352](https://github.com/djtelicloud/grok-mcp-server/pull/352) | Ready |
| 10 | `src/providers/subscription.py` | 1,427 | 150 | -1,277 | -89.5% | parse 5.0ms / compile 3.0ms | [#353](https://github.com/djtelicloud/grok-mcp-server/pull/353) | Ready |
| 11 | `tests/test_mcp_session_guard.py` | 1,397 | 120 | -1,277 | -91.4% | parse 6.0ms / compile 5.0ms | [#354](https://github.com/djtelicloud/grok-mcp-server/pull/354) | Ready |
| 12 | `tests/test_mcp_sampling_bridge.py` | 1,379 | 120 | -1,259 | -91.3% | parse 5.0ms / compile 4.0ms | [#355](https://github.com/djtelicloud/grok-mcp-server/pull/355) | Ready |
| 13 | `evals/.../attempt_ledger.py` | 1,298 | 200 | -1,098 | -84.6% | parse 4.0ms / compile 4.0ms | [#356](https://github.com/djtelicloud/grok-mcp-server/pull/356) | Ready |
| 14 | `tests/test_intelligence_payloads.py` | 1,278 | 100 | -1,178 | -92.2% | parse 5.0ms / compile 4.0ms | [#357](https://github.com/djtelicloud/grok-mcp-server/pull/357) | Ready |
| 15 | `tests/test_subscription_transports.py` | 1,235 | 100 | -1,135 | -91.9% | parse 4.0ms / compile 3.0ms | [#358](https://github.com/djtelicloud/grok-mcp-server/pull/358) | Ready |
| 16 | `tests/test_task_rag.py` | 1,201 | 100 | -1,101 | -91.7% | parse 5.0ms / compile 4.0ms | [#359](https://github.com/djtelicloud/grok-mcp-server/pull/359) | Ready |
| 17 | `tests/test_provider_harvest.py` | 1,188 | 100 | -1,088 | -91.6% | parse 5.28ms / compile 4.75ms | [#360](https://github.com/djtelicloud/grok-mcp-server/pull/360) | Ready |
| 18 | `src/tools/swarm.py` | 1,155 | 120 | -1,035 | -89.6% | parse 5.21ms / compile 4.12ms | [#361](https://github.com/djtelicloud/grok-mcp-server/pull/361) | Ready |
| 19 | `src/mcp_session_guard.py` | 1,105 | 120 | -985 | -89.1% | parse 4.25ms / compile 3.02ms | [#362](https://github.com/djtelicloud/grok-mcp-server/pull/362) | Ready |
| 20 | `tests/test_server.py` | 1,110 | 100 | -1,010 | -91.0% | parse 3.86ms / compile 3.92ms | [#363](https://github.com/djtelicloud/grok-mcp-server/pull/363) | Ready |
| 21 | `tests/test_provider_adapters.py` | 1,061 | 100 | -961 | -90.6% | parse 4.49ms / compile 2.88ms | [#364](https://github.com/djtelicloud/grok-mcp-server/pull/364) | Ready |
| 22 | `tests/test_multiagent.py` | 1,102 | 100 | -1,002 | -90.9% | parse 4.4ms / compile 3.52ms | [#365](https://github.com/djtelicloud/grok-mcp-server/pull/365) | Ready |
| 23 | `src/rag.py` | 1,001 | 150 | -851 | -85.0% | parse 3.37ms / compile 3.01ms | [#366](https://github.com/djtelicloud/grok-mcp-server/pull/366) | Ready |
| 24 | `src/providers/mcp_sampling.py` | 986 | 120 | -866 | -87.8% | parse 3.13ms / compile 2.66ms | [#367](https://github.com/djtelicloud/grok-mcp-server/pull/367) | Ready |
| 25 | `tests/test_knowledge.py` | 1,021 | 100 | -921 | -90.2% | parse 4.53ms / compile 3.34ms | [#368](https://github.com/djtelicloud/grok-mcp-server/pull/368) | Ready |
| 26 | `tests/test_evals.py` | 926 | 100 | -826 | -89.2% | parse 3.66ms / compile 3.19ms | [#369](https://github.com/djtelicloud/grok-mcp-server/pull/369) | Ready |
| 27 | `src/providers/contracts.py` | 877 | 100 | -777 | -88.6% | parse 3.35ms / compile 2.42ms | [#370](https://github.com/djtelicloud/grok-mcp-server/pull/370) | Ready |
| 28 | `src/provider_harvest.py` | 816 | 120 | -696 | -85.3% | parse 2.54ms / compile 2.14ms | [#371](https://github.com/djtelicloud/grok-mcp-server/pull/371) | Ready |
| 29 | `tests/test_credentials.py` | 804 | 100 | -704 | -87.6% | parse 2.67ms / compile 2.27ms | [#372](https://github.com/djtelicloud/grok-mcp-server/pull/372) | Ready |
| 30 | `src/tools/chats.py` | 800 | 100 | -700 | -87.5% | parse 2.35ms / compile 2.0ms | [#373](https://github.com/djtelicloud/grok-mcp-server/pull/373) | Ready |
| 31 | `src/completion_envelope.py` | 791 | 100 | -691 | -87.4% | parse 2.98ms / compile 2.04ms | [#374](https://github.com/djtelicloud/grok-mcp-server/pull/374) | Ready |
| 32 | `tests/test_completion_envelope.py` | 733 | 90 | -643 | -87.7% | parse 2.48ms / compile 1.83ms | [#375](https://github.com/djtelicloud/grok-mcp-server/pull/375) | Ready |
| 33 | `tests/.../test_stage1_schema_safety.py` | 721 | 90 | -631 | -87.5% | parse 1.99ms / compile 1.95ms | [#376](https://github.com/djtelicloud/grok-mcp-server/pull/376) | Ready |
| 34 | `tests/test_provider_attempt_ledger.py` | 714 | 90 | -624 | -87.4% | parse 2.48ms / compile 1.86ms | [#377](https://github.com/djtelicloud/grok-mcp-server/pull/377) | Ready |
| 35 | `tests/test_mcp_ui.py` | 730 | 90 | -640 | -87.7% | parse 2.93ms / compile 2.07ms | [#378](https://github.com/djtelicloud/grok-mcp-server/pull/378) | Ready |
| 36 | `tests/test_observability.py` | 712 | 90 | -622 | -87.4% | parse 2.51ms / compile 2.13ms | [#379](https://github.com/djtelicloud/grok-mcp-server/pull/379) | Ready |
| 37 | `tests/test_migrations.py` | 696 | 90 | -606 | -87.1% | parse 2.38ms / compile 1.59ms | [#380](https://github.com/djtelicloud/grok-mcp-server/pull/380) | Ready |
| 38 | `src/workspace_memory.py` | 668 | 100 | -568 | -85.0% | parse 3.19ms / compile 2.47ms | [#381](https://github.com/djtelicloud/grok-mcp-server/pull/381) | Ready |
| 39 | `evals/runner.py` | 659 | 100 | -559 | -84.8% | parse 3.25ms / compile 2.63ms | [#382](https://github.com/djtelicloud/grok-mcp-server/pull/382) | Ready |
| 40 | `evals/.../provider_adapters.py` | 623 | 80 | -543 | -87.2% | parse 2.46ms / compile 1.69ms | [#383](https://github.com/djtelicloud/grok-mcp-server/pull/383) | Ready |
| 41 | `tests/.../test_attempt_ledger_safety.py` | 612 | 80 | -532 | -86.9% | parse 2.4ms / compile 1.88ms | [#384](https://github.com/djtelicloud/grok-mcp-server/pull/384) | Ready |
| 42 | `src/swarm/engine.py` | 570 | 80 | -490 | -86.0% | parse 2.33ms / compile 1.62ms | [#385](https://github.com/djtelicloud/grok-mcp-server/pull/385) | Ready |
| 43 | `tests/test_phase5.py` | 798 | 90 | -708 | -88.7% | parse 2.98ms / compile 2.19ms | [#386](https://github.com/djtelicloud/grok-mcp-server/pull/386) | Ready |
| 44 | `tests/test_service_workspace_boundary.py` | 580 | 80 | -500 | -86.2% | parse 2.28ms / compile 1.73ms | [#387](https://github.com/djtelicloud/grok-mcp-server/pull/387) | Ready |
| 45 | `evals/.../role_schemas.py` | 559 | 70 | -489 | -87.5% | parse 2.08ms / compile 1.48ms | [#388](https://github.com/djtelicloud/grok-mcp-server/pull/388) | Ready |
| 46 | `evals/.../schemas.py` | 558 | 70 | -488 | -87.5% | parse 1.65ms / compile 1.41ms | [#389](https://github.com/djtelicloud/grok-mcp-server/pull/389) | Ready |
| 47 | `tests/test_swarm_tools.py` | 539 | 70 | -469 | -87.0% | parse 2.5ms / compile 1.65ms | [#390](https://github.com/djtelicloud/grok-mcp-server/pull/390) | Ready |
| 48 | `src/intelligence_capsule.py` | 539 | 70 | -469 | -87.0% | parse 2.2ms / compile 1.81ms | [#391](https://github.com/djtelicloud/grok-mcp-server/pull/391) | Ready |
| 49 | `tests/.../test_provider_contract.py` | 533 | 70 | -463 | -86.9% | parse 1.94ms / compile 1.39ms | [#392](https://github.com/djtelicloud/grok-mcp-server/pull/392) | Ready |
| 50 | `tests/test_intelligence_upgrade.py` | 549 | 70 | -479 | -87.2% | parse 1.49ms / compile 1.41ms | [#393](https://github.com/djtelicloud/grok-mcp-server/pull/393) | Ready |
| 51 | `evals/.../provider_smoke.py` | 491 | 60 | -431 | -87.8% | parse 1.91ms / compile 1.43ms | [#394](https://github.com/djtelicloud/grok-mcp-server/pull/394) | Ready |
| 52 | `tests/test_swarm_engine.py` | 481 | 60 | -421 | -87.5% | parse 2.27ms / compile 1.52ms | [#395](https://github.com/djtelicloud/grok-mcp-server/pull/395) | Ready |
| 53 | `scripts/install_unigrok_theme.py` | 478 | 60 | -418 | -87.4% | parse 1.81ms / compile 1.36ms | [#396](https://github.com/djtelicloud/grok-mcp-server/pull/396) | Ready |
| 54 | `tests/.../test_stage1_mock_harness.py` | 477 | 60 | -417 | -87.4% | parse 2.22ms / compile 1.5ms | [#397](https://github.com/djtelicloud/grok-mcp-server/pull/397) | Ready |
| 55 | `scripts/land.py` | 489 | 70 | -419 | -85.7% | parse 1.84ms / compile 1.53ms | [#398](https://github.com/djtelicloud/grok-mcp-server/pull/398) | Ready |
| 56 | `tests/test_release_hygiene.py` | 468 | 60 | -408 | -87.2% | parse 2.13ms / compile 1.46ms | [#399](https://github.com/djtelicloud/grok-mcp-server/pull/399) | Ready |
| 57 | `evals/.../validators.py` | 465 | 50 | -415 | -89.2% | parse 1.46ms / compile 1.61ms | [#400](https://github.com/djtelicloud/grok-mcp-server/pull/400) | Ready |
| 58 | `src/semantic_evals.py` | 463 | 60 | -403 | -87.0% | parse 1.18ms / compile 1.14ms | [#401](https://github.com/djtelicloud/grok-mcp-server/pull/401) | Ready |
| 59 | `tests/test_github_review_integration.py` | 484 | 60 | -424 | -87.6% | parse 1.57ms / compile 1.25ms | [#402](https://github.com/djtelicloud/grok-mcp-server/pull/402) | Ready |
| 60 | `tests/test_semantic_evals.py` | 441 | 55 | -386 | -87.5% | parse 1.75ms / compile 1.31ms | [#403](https://github.com/djtelicloud/grok-mcp-server/pull/403) | Ready |
| 61 | `scripts/bootstrap_intelligence_refs.py` | 435 | 55 | -380 | -87.4% | parse 1.84ms / compile 1.28ms | [#404](https://github.com/djtelicloud/grok-mcp-server/pull/404) | Ready |
| 62 | `scripts/supervisor_approval.py` | 394 | 50 | -344 | -87.3% | parse 1.65ms / compile 1.75ms | [#405](https://github.com/djtelicloud/grok-mcp-server/pull/405) | Ready |
| 63 | `src/metrics.py` | 422 | 55 | -367 | -87.0% | parse 1.64ms / compile 1.46ms | [#406](https://github.com/djtelicloud/grok-mcp-server/pull/406) | Ready |
| 64 | `tests/.../test_provider_smoke.py` | 407 | 50 | -357 | -87.7% | parse 1.36ms / compile 1.09ms | [#407](https://github.com/djtelicloud/grok-mcp-server/pull/407) | Ready |
| 65 | `tests/test_install_unigrok_theme.py` | 395 | 50 | -345 | -87.3% | parse 1.25ms / compile 0.87ms | [#409](https://github.com/djtelicloud/grok-mcp-server/pull/409) | Ready |
| 66 | `tests/test_intelligence_refs_bootstrap.py` | 389 | 50 | -339 | -87.1% | parse 1.5ms / compile 1.39ms | [#410](https://github.com/djtelicloud/grok-mcp-server/pull/410) | Ready |
| 67 | `tests/test_metrics.py` | 388 | 50 | -338 | -87.1% | parse 1.93ms / compile 1.26ms | [#411](https://github.com/djtelicloud/grok-mcp-server/pull/411) | Ready |
| 68 | `src/providers/base.py` | 380 | 45 | -335 | -88.2% | parse 1.11ms / compile 1.05ms | [#412](https://github.com/djtelicloud/grok-mcp-server/pull/412) | Ready |
| 69 | `scripts/check_agent_attribution.py` | 369 | 45 | -324 | -87.8% | parse 1.69ms / compile 1.54ms | [#413](https://github.com/djtelicloud/grok-mcp-server/pull/413) | Ready |
| 70 | `tests/test_workspace_memory.py` | 365 | 45 | -320 | -87.7% | parse 1.75ms / compile 1.14ms | [#414](https://github.com/djtelicloud/grok-mcp-server/pull/414) | Ready |
| 71 | `scripts/github-grok-review.py` | 350 | 45 | -305 | -87.1% | parse 1.61ms / compile 1.46ms | [#415](https://github.com/djtelicloud/grok-mcp-server/pull/415) | Ready |
| 72 | `src/jobs.py` | 381 | 45 | -336 | -88.2% | parse 1.23ms / compile 1.5ms | [#416](https://github.com/djtelicloud/grok-mcp-server/pull/416) | Ready |
| 73 | `tests/test_land_workflow.py` | 388 | 45 | -343 | -88.4% | parse 2.01ms / compile 1.7ms | [#417](https://github.com/djtelicloud/grok-mcp-server/pull/417) | Ready |
| 74 | `tests/test_xai_client_authority.py` | 334 | 45 | -289 | -86.5% | parse 1.13ms / compile 1.02ms | [#418](https://github.com/djtelicloud/grok-mcp-server/pull/418) | Ready |
| 75 | `src/storage.py` | 342 | 40 | -302 | -88.3% | parse 1.27ms / compile 0.98ms | [#419](https://github.com/djtelicloud/grok-mcp-server/pull/419) | Ready |
| 76 | `tests/test_swarm_storage.py` | 312 | 40 | -272 | -87.2% | parse 1.32ms / compile 1.1ms | [#420](https://github.com/djtelicloud/grok-mcp-server/pull/420) | Ready |
<!-- LOOP_METRICS_TABLE_END -->

## How to refresh

1. Update file rows from the private tracker (`unigrok-intelligence/codex/continuity/python-superiority-loop.md`).
2. Recompute KPIs (overall % = `(after − before) / before × 100` on summed LOC).
3. Sync the HTML board data blob and the `docs/snapshots/*.canvas.tsx` snapshot.
4. Bump the snapshot timestamp in this doc + HTML + README §8 callout counts.

## Notes

- Δ% = (after − before) / before × 100. Negative = reduction.
- Latency is current-file baseline only; after-parse not recorded in tracker. Memory: n/a.
- Loop worker continues; do not treat this board as merge authorization.
- PR #408 is this public metrics artifact (not a refactor plan).
