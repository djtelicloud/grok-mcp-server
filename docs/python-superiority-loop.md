# Python superiority loop — projected LOC cut

> **Snapshot:** tracker `2026-07-16T17:06:47Z` · **46/210** files with Ready refactor plans · loop still in progress.
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
| **Overall LOC reduction** | **-91.1%** |
| Avg file % cut | -88.9% |
| Files with plans | 46 / 210 (22%) |
| Ready draft PRs | 46 (#342–#389) |
| LOC before → after (projected) | 73,561 → 6,550 (-67,011) |
| Next pending | `tests/test_swarm_tools.py (~539 LOC)` |

<div align="center">

<p style="font-size:3rem;font-weight:700;letter-spacing:-0.03em;margin:0.5rem 0;color:#16f0ae">-91.1% overall</p>
<p style="color:#8b9eb0;margin:0">Projected facade/shim cut across 46 Ready plans · 46/210 inventory</p>

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
| 1 | `src/utils.py` | 13,830 | 800 | -13,030 | -94.2% | parse 53ms / compile 42ms | [#342](https://github.com/djtelicloud/grok-mcp-server/pull/342) | Ready |
| 2 | `tests/test_utils.py` | 7,848 | 200 | -7,648 | -97.5% | parse 29ms / compile 26ms | [#345](https://github.com/djtelicloud/grok-mcp-server/pull/345) | Ready |
| 3 | `tests/test_provider_broker.py` | 4,707 | 150 | -4,557 | -96.8% | parse 17ms / compile 14ms | [#346](https://github.com/djtelicloud/grok-mcp-server/pull/346) | Ready |
| 4 | `src/providers/broker.py` | 3,122 | 600 | -2,522 | -80.8% | parse 9ms / compile 8ms | [#347](https://github.com/djtelicloud/grok-mcp-server/pull/347) | Ready |
| 5 | `src/http_server.py` | 2,874 | 400 | -2,474 | -86.1% | parse 11ms / compile 9ms | [#348](https://github.com/djtelicloud/grok-mcp-server/pull/348) | Ready |
| 6 | `tests/test_http_server.py` | 2,371 | 150 | -2,221 | -93.7% | parse 10ms / compile 7ms | [#349](https://github.com/djtelicloud/grok-mcp-server/pull/349) | Ready |
| 7 | `src/intelligence_payloads.py` | 1,749 | 120 | -1,629 | -93.1% | parse 8ms / compile 5ms | [#350](https://github.com/djtelicloud/grok-mcp-server/pull/350) | Ready |
| 8 | `evals/.../stage1_harness.py` | 1,644 | 200 | -1,444 | -87.8% | parse 5ms / compile 5ms | [#351](https://github.com/djtelicloud/grok-mcp-server/pull/351) | Ready |
| 9 | `src/tools/system.py` | 1,525 | 120 | -1,405 | -92.1% | parse 6ms / compile 5ms | [#352](https://github.com/djtelicloud/grok-mcp-server/pull/352) | Ready |
| 10 | `src/providers/subscription.py` | 1,427 | 150 | -1,277 | -89.5% | parse 5ms / compile 3ms | [#353](https://github.com/djtelicloud/grok-mcp-server/pull/353) | Ready |
| 11 | `tests/test_mcp_session_guard.py` | 1,397 | 120 | -1,277 | -91.4% | parse 6ms / compile 5ms | [#354](https://github.com/djtelicloud/grok-mcp-server/pull/354) | Ready |
| 12 | `tests/test_mcp_sampling_bridge.py` | 1,379 | 120 | -1,259 | -91.3% | parse 5ms / compile 4ms | [#355](https://github.com/djtelicloud/grok-mcp-server/pull/355) | Ready |
| 13 | `evals/.../attempt_ledger.py` | 1,298 | 200 | -1,098 | -84.6% | parse 4ms / compile 4ms | [#356](https://github.com/djtelicloud/grok-mcp-server/pull/356) | Ready |
| 14 | `tests/test_intelligence_payloads.py` | 1,278 | 100 | -1,178 | -92.2% | parse 5ms / compile 4ms | [#357](https://github.com/djtelicloud/grok-mcp-server/pull/357) | Ready |
| 15 | `tests/test_subscription_transports.py` | 1,235 | 100 | -1,135 | -91.9% | parse 4ms / compile 3ms | [#358](https://github.com/djtelicloud/grok-mcp-server/pull/358) | Ready |
| 16 | `tests/test_task_rag.py` | 1,201 | 100 | -1,101 | -91.7% | parse 5ms / compile 4ms | [#359](https://github.com/djtelicloud/grok-mcp-server/pull/359) | Ready |
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
| 33 | `tests/campaigns/.../test_stage1_schema_safety.py` | 721 | 90 | -631 | -87.5% | parse 1.99ms / compile 1.95ms | [#376](https://github.com/djtelicloud/grok-mcp-server/pull/376) | Ready |
| 34 | `tests/test_provider_attempt_ledger.py` | 714 | 90 | -624 | -87.4% | parse 2.48ms / compile 1.86ms | [#377](https://github.com/djtelicloud/grok-mcp-server/pull/377) | Ready |
| 35 | `tests/test_mcp_ui.py` | 730 | 90 | -640 | -87.7% | parse 2.93ms / compile 2.07ms | [#378](https://github.com/djtelicloud/grok-mcp-server/pull/378) | Ready |
| 36 | `tests/test_observability.py` | 712 | 90 | -622 | -87.4% | parse 2.51ms / compile 2.13ms | [#379](https://github.com/djtelicloud/grok-mcp-server/pull/379) | Ready |
| 37 | `tests/test_migrations.py` | 696 | 90 | -606 | -87.1% | parse 2.38ms / compile 1.59ms | [#380](https://github.com/djtelicloud/grok-mcp-server/pull/380) | Ready |
| 38 | `src/workspace_memory.py` | 668 | 100 | -568 | -85.0% | parse 3.19ms / compile 2.47ms | [#381](https://github.com/djtelicloud/grok-mcp-server/pull/381) | Ready |
| 39 | `evals/runner.py` | 659 | 100 | -559 | -84.8% | parse 3.25ms / compile 2.63ms | [#382](https://github.com/djtelicloud/grok-mcp-server/pull/382) | Ready |
| 40 | `evals/.../provider_adapters.py` | 623 | 80 | -543 | -87.2% | parse 2.46ms / compile 1.69ms | [#383](https://github.com/djtelicloud/grok-mcp-server/pull/383) | Ready |
| 41 | `tests/campaigns/.../test_attempt_ledger_safety.py` | 612 | 80 | -532 | -86.9% | parse 2.4ms / compile 1.88ms | [#384](https://github.com/djtelicloud/grok-mcp-server/pull/384) | Ready |
| 42 | `src/swarm/engine.py` | 570 | 80 | -490 | -86.0% | parse 2.33ms / compile 1.62ms | [#385](https://github.com/djtelicloud/grok-mcp-server/pull/385) | Ready |
| 43 | `tests/test_phase5.py` | 798 | 90 | -708 | -88.7% | parse 2.98ms / compile 2.19ms | [#386](https://github.com/djtelicloud/grok-mcp-server/pull/386) | Ready |
| 44 | `tests/test_service_workspace_boundary.py` | 580 | 80 | -500 | -86.2% | parse 2.28ms / compile 1.73ms | [#387](https://github.com/djtelicloud/grok-mcp-server/pull/387) | Ready |
| 45 | `evals/.../role_schemas.py` | 559 | 70 | -489 | -87.5% | parse 2.08ms / compile 1.48ms | [#388](https://github.com/djtelicloud/grok-mcp-server/pull/388) | Ready |
| 46 | `evals/.../schemas.py` | 558 | 70 | -488 | -87.5% | parse 1.65ms / compile 1.41ms | [#389](https://github.com/djtelicloud/grok-mcp-server/pull/389) | Ready |
<!-- LOOP_METRICS_TABLE_END -->

## How to refresh

1. Update file rows from the private tracker (`unigrok-intelligence/codex/continuity/python-superiority-loop.md`).
2. Recompute KPIs (overall % = `(after − before) / before × 100` on summed LOC).
3. Sync the HTML board data blob and the `docs/snapshots/*.canvas.tsx` snapshot.
4. Bump the snapshot timestamp in this doc + HTML.

## Notes

- Δ% = (after − before) / before × 100. Negative = reduction.
- Latency is current-file baseline only; after-parse not recorded in tracker. Memory: n/a.
- Loop worker continues; do not treat this board as merge authorization.

