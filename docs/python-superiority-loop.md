# Python superiority loop — projected LOC cut

> **Snapshot:** tracker `2026-07-16T17:28:04Z` · **201/210** files with refactor plans · pending **0** (full done set).
>
> These are **projected** facade/shim LOC targets from Ready draft PRs and in-tree plans — not landed merges yet.
> Codex Sol High can review Ready drafts; **merge stays the normal Codex land path**.

## Share this

- **Visual board (recommended):** [python-superiority-loop.html](python-superiority-loop.html) — teal/cyan Control-Center vibe, self-contained
- **This markdown:** GitHub-renderable KPI + full table (refresh-friendly)
- **Canvas source snapshot (history only):** [snapshots/python-superiority-loop-metrics.canvas.tsx](snapshots/python-superiority-loop-metrics.canvas.tsx) — not the public runtime; IDE canvas is a local companion under `~/.cursor/projects/.../canvases/`

## Headline KPIs

| KPI | Value |
|---|---:|
| **Overall LOC reduction** | **-88.2%** |
| Avg file % cut | -29.5% |
| Files with plans | 201 / 210 (96%) |
| Ready draft PRs | 126 (#342–#474, skipping docs #408) |
| In-tree plans | 75 (Loops 127–201 on `cursor/python-superiority-loop`) |
| Skipped (deferred) | 9 |
| Pending | 0 |
| LOC before → after (projected) | 101,328 → 11,985 (-89,343) |
| Next pending | none |

<div align="center">

<p style="font-size:3rem;font-weight:700;letter-spacing:-0.03em;margin:0.5rem 0;color:#16f0ae">-88.2% overall</p>
<p style="color:#8b9eb0;margin:0">Projected facade/shim cut across 201 plans · 201/210 inventory · pending 0</p>

</div>

## Biggest % wins

| File | Before | After | Δ% | PR |
|---|---:|---:|---:|---|
| `test_utils.py` | 7,848 | 200 | -97.5% | [#345](https://github.com/djtelicloud/grok-mcp-server/pull/345) |
| `test_provider_broker.py` | 4,707 | 150 | -96.8% | [#346](https://github.com/djtelicloud/grok-mcp-server/pull/346) |
| `utils.py` | 13,830 | 800 | -94.2% | [#342](https://github.com/djtelicloud/grok-mcp-server/pull/342) |
| `test_http_server.py` | 2,371 | 150 | -93.7% | [#349](https://github.com/djtelicloud/grok-mcp-server/pull/349) |
| `intelligence_payloads.py` | 1,749 | 120 | -93.1% | [#350](https://github.com/djtelicloud/grok-mcp-server/pull/350) |

## All done plans (refresh this table)

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
| 77 | `tests/test_supervisor_approval.py` | 326 | 40 | -286 | -87.7% | parse 0.99ms / compile 0.7ms | [#421](https://github.com/djtelicloud/grok-mcp-server/pull/421) | Ready |
| 78 | `src/swarm/analytics.py` | 299 | 40 | -259 | -86.6% | parse 1.85ms / compile 1.15ms | [#423](https://github.com/djtelicloud/grok-mcp-server/pull/423) | Ready |
| 79 | `evals/.../stage1_oracles.py` | 297 | 40 | -257 | -86.5% | parse 1.05ms / compile 0.78ms | [#424](https://github.com/djtelicloud/grok-mcp-server/pull/424) | Ready |
| 80 | `src/swarm/sandbox.py` | 296 | 35 | -261 | -88.2% | parse 1.06ms / compile 0.9ms | [#425](https://github.com/djtelicloud/grok-mcp-server/pull/425) | Ready |
| 81 | `src/tools/media.py` | 290 | 35 | -255 | -87.9% | parse 1.0ms / compile 0.85ms | [#426](https://github.com/djtelicloud/grok-mcp-server/pull/426) | Ready |
| 82 | `scripts/run_swarm_evals.py` | 286 | 35 | -251 | -87.8% | parse 1.43ms / compile 1.08ms | [#427](https://github.com/djtelicloud/grok-mcp-server/pull/427) | Ready |
| 83 | `tests/test_routing.py` | 277 | 35 | -242 | -87.4% | parse 0.95ms / compile 0.79ms | [#428](https://github.com/djtelicloud/grok-mcp-server/pull/428) | Ready |
| 84 | `evals/.../provider_transports.py` | 273 | 35 | -238 | -87.2% | parse 0.86ms / compile 1.13ms | [#429](https://github.com/djtelicloud/grok-mcp-server/pull/429) | Ready |
| 85 | `src/routing.py` | 272 | 35 | -237 | -87.1% | parse 1.3ms / compile 0.9ms | [#430](https://github.com/djtelicloud/grok-mcp-server/pull/430) | Ready |
| 86 | `src/tools/resources.py` | 266 | 35 | -231 | -86.8% | parse 0.82ms / compile 0.68ms | [#431](https://github.com/djtelicloud/grok-mcp-server/pull/431) | Ready |
| 87 | `src/tools/git.py` | 258 | 35 | -223 | -86.4% | parse 1.36ms / compile 0.97ms | [#432](https://github.com/djtelicloud/grok-mcp-server/pull/432) | Ready |
| 88 | `src/faq.py` | 253 | 35 | -218 | -86.2% | parse 0.99ms / compile 0.96ms | [#433](https://github.com/djtelicloud/grok-mcp-server/pull/433) | Ready |
| 89 | `tests/test_swarm_static_gate.py` | 250 | 35 | -215 | -86.0% | parse 0.96ms / compile 0.82ms | [#434](https://github.com/djtelicloud/grok-mcp-server/pull/434) | Ready |
| 90 | `tests/test_agent_attribution.py` | 247 | 35 | -212 | -85.8% | parse 1.01ms / compile 0.52ms | [#435](https://github.com/djtelicloud/grok-mcp-server/pull/435) | Ready |
| 91 | `tests/test_swarm_sandbox.py` | 243 | 35 | -208 | -85.6% | parse 0.95ms / compile 0.86ms | [#436](https://github.com/djtelicloud/grok-mcp-server/pull/436) | Ready |
| 92 | `scripts/generate_okf.py` | 240 | 35 | -205 | -85.4% | parse 1.33ms / compile 1.0ms | [#437](https://github.com/djtelicloud/grok-mcp-server/pull/437) | Ready |
| 93 | `evals/fakes.py` | 235 | 35 | -200 | -85.1% | parse 0.79ms / compile 0.69ms | [#438](https://github.com/djtelicloud/grok-mcp-server/pull/438) | Ready |
| 94 | `scripts/publish_okf_wiki_mirror.py` | 234 | 35 | -199 | -85.0% | parse 0.92ms / compile 0.79ms | [#439](https://github.com/djtelicloud/grok-mcp-server/pull/439) | Ready |
| 95 | `evals/.../stage1_artifacts.py` | 231 | 35 | -196 | -84.8% | parse 0.99ms / compile 0.66ms | [#440](https://github.com/djtelicloud/grok-mcp-server/pull/440) | Ready |
| 96 | `src/swarm/runner.py` | 228 | 35 | -193 | -84.6% | parse 0.78ms / compile 0.68ms | [#441](https://github.com/djtelicloud/grok-mcp-server/pull/441) | Ready |
| 97 | `scripts/mint_mcp_service_token.py` | 228 | 35 | -193 | -84.6% | parse 0.92ms / compile 0.66ms | [#442](https://github.com/djtelicloud/grok-mcp-server/pull/442) | Ready |
| 98 | `tests/test_harness.py` | 231 | 35 | -196 | -84.8% | parse 0.83ms / compile 0.67ms | [#443](https://github.com/djtelicloud/grok-mcp-server/pull/443) | Ready |
| 99 | `src/providers/vertex.py` | 224 | 35 | -189 | -84.4% | parse 0.95ms / compile 0.63ms | [#444](https://github.com/djtelicloud/grok-mcp-server/pull/444) | Ready |
| 100 | `tests/test_markdown_renderer.py` | 213 | 35 | -178 | -83.6% | parse 0.57ms / compile 0.49ms | [#445](https://github.com/djtelicloud/grok-mcp-server/pull/445) | Ready |
| 101 | `src/swarm/ast_utils.py` | 213 | 35 | -178 | -83.6% | parse 1.06ms / compile 0.7ms | [#446](https://github.com/djtelicloud/grok-mcp-server/pull/446) | Ready |
| 102 | `src/credentials.py` | 285 | 35 | -250 | -87.7% | parse 0.87ms / compile 0.68ms | [#447](https://github.com/djtelicloud/grok-mcp-server/pull/447) | Ready |
| 103 | `tests/test_agent_mode_plane_contract.py` | 209 | 35 | -174 | -83.3% | parse 0.79ms / compile 0.84ms | [#448](https://github.com/djtelicloud/grok-mcp-server/pull/448) | Ready |
| 104 | `src/swarm/preflight.py` | 203 | 35 | -168 | -82.8% | parse 0.8ms / compile 0.61ms | [#449](https://github.com/djtelicloud/grok-mcp-server/pull/449) | Ready |
| 105 | `tests/test_intelligence_capsule.py` | 200 | 35 | -165 | -82.5% | parse 0.64ms / compile 0.64ms | [#450](https://github.com/djtelicloud/grok-mcp-server/pull/450) | Ready |
| 106 | `tests/.../test_stage0_mechanical.py` | 194 | 35 | -159 | -82.0% | parse 0.5ms / compile 0.42ms | [#451](https://github.com/djtelicloud/grok-mcp-server/pull/451) | Ready |
| 107 | `src/hydration.py` | 194 | 35 | -159 | -82.0% | parse 0.88ms / compile 0.7ms | [#452](https://github.com/djtelicloud/grok-mcp-server/pull/452) | Ready |
| 108 | `src/swarm/transforms.py` | 193 | 35 | -158 | -81.9% | parse 0.77ms / compile 0.68ms | [#453](https://github.com/djtelicloud/grok-mcp-server/pull/453) | Ready |
| 109 | `evals/__main__.py` | 193 | 35 | -158 | -81.9% | parse 0.82ms / compile 0.56ms | [#454](https://github.com/djtelicloud/grok-mcp-server/pull/454) | Ready |
| 110 | `tests/test_dual_plane_model_discipline.py` | 186 | 35 | -151 | -81.2% | parse 0.72ms / compile 0.65ms | [#455](https://github.com/djtelicloud/grok-mcp-server/pull/455) | Ready |
| 111 | `src/cli.py` | 268 | 35 | -233 | -86.9% | parse 1.24ms / compile 0.83ms | [#456](https://github.com/djtelicloud/grok-mcp-server/pull/456) | Ready |
| 112 | `tests/test_mint_mcp_service_token.py` | 174 | 35 | -139 | -79.9% | parse 0.79ms / compile 0.6ms | [#457](https://github.com/djtelicloud/grok-mcp-server/pull/457) | Ready |
| 113 | `src/server.py` | 171 | 35 | -136 | -79.5% | parse 0.39ms / compile 0.26ms | [#458](https://github.com/djtelicloud/grok-mcp-server/pull/458) | Ready |
| 114 | `src/providers/__init__.py` | 170 | 35 | -135 | -79.4% | parse 0.24ms / compile 0.2ms | [#459](https://github.com/djtelicloud/grok-mcp-server/pull/459) | Ready |
| 115 | `src/swarm/pareto.py` | 169 | 35 | -134 | -79.3% | parse 0.77ms / compile 0.72ms | [#460](https://github.com/djtelicloud/grok-mcp-server/pull/460) | Ready |
| 116 | `src/identity.py` | 165 | 35 | -130 | -78.8% | parse 0.51ms / compile 0.42ms | [#461](https://github.com/djtelicloud/grok-mcp-server/pull/461) | Ready |
| 117 | `src/swarm/mutators.py` | 160 | 35 | -125 | -78.1% | parse 0.54ms / compile 0.34ms | [#462](https://github.com/djtelicloud/grok-mcp-server/pull/462) | Ready |
| 118 | `src/swarm/config.py` | 156 | 35 | -121 | -77.6% | parse 0.4ms / compile 0.38ms | [#463](https://github.com/djtelicloud/grok-mcp-server/pull/463) | Ready |
| 119 | `src/providers/openai.py` | 154 | 35 | -119 | -77.3% | parse 0.49ms / compile 0.43ms | [#464](https://github.com/djtelicloud/grok-mcp-server/pull/464) | Ready |
| 120 | `src/tools/workspace_memory.py` | 151 | 35 | -116 | -76.8% | parse 0.41ms / compile 0.37ms | [#465](https://github.com/djtelicloud/grok-mcp-server/pull/465) | Ready |
| 121 | `src/providers/anthropic.py` | 151 | 35 | -116 | -76.8% | parse 0.43ms / compile 0.45ms | [#466](https://github.com/djtelicloud/grok-mcp-server/pull/466) | Ready |
| 122 | `evals/cassettes.py` | 151 | 35 | -116 | -76.8% | parse 0.77ms / compile 0.55ms | [#470](https://github.com/djtelicloud/grok-mcp-server/pull/470) | Ready |
| 123 | `tests/test_swarm_eval_script.py` | 150 | 35 | -115 | -76.7% | parse 0.51ms / compile 0.47ms | [#471](https://github.com/djtelicloud/grok-mcp-server/pull/471) | Ready |
| 124 | `tests/test_git_tools.py` | 148 | 35 | -113 | -76.4% | parse 0.51ms / compile 0.48ms | [#472](https://github.com/djtelicloud/grok-mcp-server/pull/472) | Ready |
| 125 | `src/tools/knowledge.py` | 148 | 35 | -113 | -76.4% | parse 0.44ms / compile 0.37ms | [#473](https://github.com/djtelicloud/grok-mcp-server/pull/473) | Ready |
| 126 | `tests/test_hydration.py` | 137 | 35 | -102 | -74.5% | parse 0.37ms / compile 0.32ms | [#474](https://github.com/djtelicloud/grok-mcp-server/pull/474) | Ready |
| 127 | `tests/test_swarm_pareto.py` | 134 | 35 | -99 | -73.9% | parse 0.86ms / compile 0.71ms | `in-tree:9fbaa2e44a53` | in-tree |
| 128 | `tests/test_swarm_ast.py` | 128 | 35 | -93 | -72.7% | parse 0.61ms / compile 0.5ms | `in-tree:8eef324330b2` | in-tree |
| 129 | `evals/.../mechanical_mutators.py` | 124 | 35 | -89 | -71.8% | parse 0.53ms / compile 0.33ms | `in-tree:174ebdc2a16e` | in-tree |
| 130 | `tests/.../test_stage1_artifacts.py` | 119 | 35 | -84 | -70.6% | parse 0.75ms / compile 0.51ms | `in-tree:249927d7259c` | in-tree |
| 131 | `src/providers/gemini.py` | 119 | 35 | -84 | -70.6% | parse 0.44ms / compile 0.3ms | `in-tree:89ae6efbf89d` | in-tree |
| 132 | `tests/test_subagent_surface_contract.py` | 118 | 35 | -83 | -70.3% | parse 0.53ms / compile 0.45ms | `in-tree:90f46e92039f` | in-tree |
| 133 | `src/providers/config.py` | 118 | 35 | -83 | -70.3% | parse 0.52ms / compile 0.34ms | `in-tree:e75eb511c863` | in-tree |
| 134 | `src/swarm/router.py` | 116 | 35 | -81 | -69.8% | parse 0.52ms / compile 0.45ms | `in-tree:76f9ee3357f1` | in-tree |
| 135 | `tests/test_phase4.py` | 114 | 35 | -79 | -69.3% | parse 0.53ms / compile 0.39ms | `in-tree:589cda906ebd` | in-tree |
| 136 | `tests/test_faq.py` | 111 | 35 | -76 | -68.5% | parse 0.4ms / compile 0.3ms | `in-tree:aa1386cc99a7` | in-tree |
| 137 | `tests/test_export_pr.py` | 107 | 35 | -72 | -67.3% | parse 0.52ms / compile 0.33ms | `in-tree:5dbe898ffe20` | in-tree |
| 138 | `evals/graders.py` | 102 | 35 | -67 | -65.7% | parse 0.81ms / compile 0.5ms | `in-tree:22afc119170b` | in-tree |
| 139 | `src/providers/google_common.py` | 101 | 35 | -66 | -65.3% | parse 0.48ms / compile 0.35ms | `in-tree:7547193af176` | in-tree |
| 140 | `src/swarm/static_gate.py` | 99 | 35 | -64 | -64.6% | parse 0.36ms / compile 0.27ms | `in-tree:38e6510976f2` | in-tree |
| 141 | `src/tools/consistency.py` | 98 | 35 | -63 | -64.3% | parse 0.42ms / compile 0.32ms | `in-tree:9d4ccaa4a97c` | in-tree |
| 142 | `tests/test_swarm_router.py` | 94 | 35 | -59 | -62.8% | parse 0.6ms / compile 0.43ms | `in-tree:e4e0277dec35` | in-tree |
| 143 | `tests/test_generate_okf.py` | 92 | 35 | -57 | -62.0% | parse 0.38ms / compile 0.23ms | `in-tree:d354ee1abe72` | in-tree |
| 144 | `tests/test_publish_okf_wiki_mirror.py` | 89 | 35 | -54 | -60.7% | parse 0.55ms / compile 0.42ms | `in-tree:3086f3c0eb4e` | in-tree |
| 145 | `scripts/land-status.py` | 87 | 35 | -52 | -59.8% | parse 0.53ms / compile 0.37ms | `in-tree:c8b01cde5c3e` | in-tree |
| 146 | `src/tools/research.py` | 108 | 35 | -73 | -67.6% | parse 0.41ms / compile 0.27ms | `in-tree:9c3becda4adc` | in-tree |
| 147 | `src/swarm/fold.py` | 84 | 35 | -49 | -58.3% | parse 0.47ms / compile 0.28ms | `in-tree:7e088b970bb7` | in-tree |
| 148 | `src/swarm/generate.py` | 83 | 35 | -48 | -57.8% | parse 0.36ms / compile 0.24ms | `in-tree:1162a515fc2a` | in-tree |
| 149 | `tests/test_swarm_analytics.py` | 81 | 35 | -46 | -56.8% | parse 0.42ms / compile 0.28ms | `in-tree:dc71110a82d0` | in-tree |
| 150 | `tests/test_cli.py` | 208 | 35 | -173 | -83.2% | parse 0.96ms / compile 0.79ms | `in-tree:75cd0b8152c2` | in-tree |
| 151 | `src/tools/faq.py` | 77 | 35 | -42 | -54.5% | parse 0.26ms / compile 0.18ms | `in-tree:3cba81e288a7` | in-tree |
| 152 | `src/models/results.py` | 70 | 35 | -35 | -50.0% | parse 0.53ms / compile 0.36ms | `in-tree:7518266f0888` | in-tree |
| 153 | `tests/test_public_intelligence_packs.py` | 64 | 35 | -29 | -45.3% | parse 0.39ms / compile 0.26ms | `in-tree:06d02b0228da` | in-tree |
| 154 | `scripts/swarm_bench.py` | 62 | 35 | -27 | -43.5% | parse 0.35ms / compile 0.23ms | `in-tree:731c1d079d85` | in-tree |
| 155 | `src/xai_credentials.py` | 59 | 35 | -24 | -40.7% | parse 0.26ms / compile 0.17ms | `in-tree:9efbf11253a8` | in-tree |
| 156 | `tests/test_swarm_transforms.py` | 51 | 35 | -16 | -31.4% | parse 0.21ms / compile 0.14ms | `in-tree:ac20d505e963` | in-tree |
| 157 | `tests/test_consistency.py` | 52 | 35 | -17 | -32.7% | parse 0.36ms / compile 0.18ms | `in-tree:17327cfa2db6` | in-tree |
| 158 | `tests/test_codex_desktop_session_contract.py` | 51 | 35 | -16 | -31.4% | parse 0.9ms / compile 0.16ms | `in-tree:655fcc93b0cb` | in-tree |
| 159 | `src/providers/registry.py` | 49 | 35 | -14 | -28.6% | parse 0.2ms / compile 0.14ms | `in-tree:a295ff8c3f77` | in-tree |
| 160 | `evals/tasks/swarm_targets/nsquared_dedup/test_dedup.py` | 43 | 35 | -8 | -18.6% | parse 0.24ms / compile 0.21ms | `in-tree:e2e7fa2476ef` | in-tree |
| 161 | `tests/conftest.py` | 42 | 35 | -7 | -16.7% | parse 0.26ms / compile 0.18ms | `in-tree:668855c76789` | in-tree |
| 162 | `tests/test_swarm_runner.py` | 40 | 20 | -20 | -50.0% | parse 0.38ms / compile 0.19ms | `in-tree:e3108c60b013` | in-tree |
| 163 | `src/providers/errors.py` | 39 | 20 | -19 | -48.7% | parse 0.22ms / compile 0.13ms | `in-tree:aad393a5f31f` | in-tree |
| 164 | `.cursor/hooks/before-unigrok-agent.py` | 39 | 20 | -19 | -48.7% | parse 0.19ms / compile 0.11ms | `in-tree:9fb19b379835` | in-tree |
| 165 | `src/provider_redaction.py` | 37 | 20 | -17 | -45.9% | parse 0.21ms / compile 0.14ms | `in-tree:31faf0b2089b` | in-tree |
| 166 | `tests/test_namespace_human_radio_roots.py` | 35 | 20 | -15 | -42.9% | parse 0.22ms / compile 0.13ms | `in-tree:620ff189f855` | in-tree |
| 167 | `.cursor/hooks/session-unigrok-env.py` | 35 | 20 | -15 | -42.9% | parse 0.16ms / compile 0.1ms | `in-tree:b4a7f83a5a29` | in-tree |
| 168 | `tests/fixtures/swarm_target/slow_mod.py` | 34 | 20 | -14 | -41.2% | parse 0.24ms / compile 0.13ms | `in-tree:94ca0493bd62` | in-tree |
| 169 | `tests/test_codeql_contracts.py` | 33 | 20 | -13 | -39.4% | parse 0.22ms / compile 0.13ms | `in-tree:fc7288947e75` | in-tree |
| 170 | `tests/test_okf_vscode_guidance.py` | 32 | 20 | -12 | -37.5% | parse 0.21ms / compile 0.12ms | `in-tree:1c812462c432` | in-tree |
| 171 | `tests/test_okf_copilot_playbook.py` | 30 | 20 | -10 | -33.3% | parse 0.18ms / compile 0.12ms | `in-tree:e6000ec7c726` | in-tree |
| 172 | `tests/test_okf_team_check.py` | 29 | 20 | -9 | -31.0% | parse 0.23ms / compile 0.12ms | `in-tree:995967d6f0ac` | in-tree |
| 173 | `tests/test_campaign.py` | 25 | 20 | -5 | -20.0% | parse 0.29ms / compile 0.15ms | `in-tree:bfe42f4d9b9f` | in-tree |
| 174 | `evals/tasks/swarm_targets/slow_loop_optimize/bench_loop_opt.py` | 24 | 20 | -4 | -16.7% | parse 0.19ms / compile 0.1ms | `in-tree:084a56bf6174` | in-tree |
| 175 | `tests/test_public_mcp_transport_security.py` | 45 | 35 | -10 | -22.2% | parse 0.24ms / compile 0.24ms | `in-tree:b1a5fcd3729f` | in-tree |
| 176 | `evals/tasks/swarm_targets/nsquared_dedup/bench_dedup.py` | 20 | 20 | +0 | 0.0% | parse 0.22ms / compile 0.13ms | `in-tree:7d8b8d55eae1` | in-tree |
| 177 | `evals/tasks/swarm_targets/slow_loop_optimize/test_loop_opt.py` | 19 | 20 | +1 | +5.3% | parse 0.14ms / compile 0.07ms | `in-tree:1f5bedf5e704` | in-tree |
| 178 | `tests/fixtures/swarm_target/test_slow.py` | 16 | 20 | +4 | +25.0% | parse 0.18ms / compile 0.09ms | `in-tree:d0d72f857110` | in-tree |
| 179 | `tests/test_claude_md_runtime_contract.py` | 15 | 20 | +5 | +33.3% | parse 0.13ms / compile 0.07ms | `in-tree:e03288150c59` | in-tree |
| 180 | `evals/tasks/swarm_targets/slow_loop_optimize/loop_opt.py` | 15 | 20 | +5 | +33.3% | parse 0.13ms / compile 0.05ms | `in-tree:62c1e9bdd4d0` | in-tree |
| 181 | `evals/tasks/swarm_targets/nsquared_dedup/dedup.py` | 11 | 20 | +9 | +81.8% | parse 0.09ms / compile 0.05ms | `in-tree:86376bf61eac` | in-tree |
| 182 | `tests/fixtures/swarm_target/bench_unstable.py` | 10 | 20 | +10 | +100.0% | parse 0.13ms / compile 0.07ms | `in-tree:8b119a6d34ef` | in-tree |
| 183 | `tests/fixtures/swarm_target/bench_slow.py` | 10 | 20 | +10 | +100.0% | parse 0.12ms / compile 0.05ms | `in-tree:f8a4f6026238` | in-tree |
| 184 | `src/version.py` | 10 | 20 | +10 | +100.0% | parse 0.06ms / compile 0.03ms | `in-tree:633a08e9d8cf` | in-tree |
| 185 | `tests/fixtures/swarm_target/test_slow_suite.py` | 9 | 20 | +11 | +122.2% | parse 0.13ms / compile 0.06ms | `in-tree:310bd927c397` | in-tree |
| 186 | `tests/fixtures/multifile_pkg/test_policy.py` | 9 | 20 | +11 | +122.2% | parse 0.1ms / compile 0.05ms | `in-tree:5bfadb9fb2aa` | in-tree |
| 187 | `src/__init__.py` | 9 | 20 | +11 | +122.2% | parse 0.1ms / compile 0.04ms | `in-tree:6560dd876355` | in-tree |
| 188 | `tests/fixtures/multifile_pkg/policy.py` | 7 | 20 | +13 | +185.7% | parse 0.11ms / compile 0.05ms | `in-tree:87862797d2b9` | in-tree |
| 189 | `tests/fixtures/multifile_pkg/constants.py` | 7 | 20 | +13 | +185.7% | parse 0.09ms / compile 0.03ms | `in-tree:a410f99766ec` | in-tree |
| 190 | `src/swarm/__init__.py` | 7 | 20 | +13 | +185.7% | parse 0.05ms / compile 0.02ms | `in-tree:e9c5657754ef` | in-tree |
| 191 | `evals/__init__.py` | 7 | 20 | +13 | +185.7% | parse 0.03ms / compile 0.02ms | `in-tree:be4d9df1acd1` | in-tree |
| 192 | `tests/fixtures/multifile_pkg/__init__.py` | 5 | 20 | +15 | +300.0% | parse 0.07ms / compile 0.03ms | `in-tree:4705ce414b8c` | in-tree |
| 193 | `main.py` | 5 | 20 | +15 | +300.0% | parse 0.11ms / compile 0.04ms | `in-tree:0074ddbccc12` | in-tree |
| 194 | `tests/fixtures/swarm_target/test_nocov.py` | 3 | 20 | +17 | +566.7% | parse 0.1ms / compile 0.03ms | `in-tree:0a0afa6d10c4` | in-tree |
| 195 | `src/tools/__init__.py` | 2 | 20 | +18 | +900.0% | parse 0.04ms / compile 0.02ms | `in-tree:dbeb7893fec2` | in-tree |
| 196 | `tests/__init__.py` | 1 | 20 | +19 | +1900.0% | parse 0.04ms / compile 0.02ms | `in-tree:14aa89fbd841` | in-tree |
| 197 | `scripts/__init__.py` | 1 | 20 | +19 | +1900.0% | parse 0.06ms / compile 0.02ms | `in-tree:bb47f0d18f01` | in-tree |
| 198 | `tests/.../__init__.py` | 0 | 20 | +20 | 0.0% | parse 0.02ms / compile 0.01ms | `in-tree:84cb7076262a` | in-tree |
| 199 | `tests/campaigns/__init__.py` | 0 | 20 | +20 | 0.0% | parse 0.02ms / compile 0.01ms | `in-tree:d66c36ad11c7` | in-tree |
| 200 | `evals/.../__init__.py` | 0 | 20 | +20 | 0.0% | parse 0.03ms / compile 0.02ms | `in-tree:ae4437932cb7` | in-tree |
| 201 | `evals/campaigns/__init__.py` | 0 | 20 | +20 | 0.0% | parse 0.02ms / compile 0.02ms | `in-tree:bda882ffbf6e` | in-tree |
<!-- LOOP_METRICS_TABLE_END -->

## Notes

- Δ% = (after − before) / before × 100. Tiny stubs may show projected growth.
- Latency is current-file baseline only; after-parse not recorded in tracker.
- Docs PR for this board: [#408](https://github.com/djtelicloud/grok-mcp-server/pull/408).
