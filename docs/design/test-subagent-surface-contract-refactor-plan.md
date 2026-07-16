# `tests/test_subagent_surface_contract.py` refactor plan (Loop 132)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 118 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-70%** |
| Hot | `test_isolated_cli_args_always_disable_subagents` ~22 · `test_using_unigrok_documents_research_fanout_not_local_spawn` ~15 · `test_research_mode_is_only_mode_that_requests_agent_count` ~15 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_isolated_cli_args_always_disable_subagents` | extract hot path (~22 LOC) |
| split / `test_using_unigrok_documents_research_fanout_not_local_spawn` | extract hot path (~15 LOC) |
| split / `test_research_mode_is_only_mode_that_requests_agent_count` | extract hot path (~15 LOC) |
| `tests/test_subagent_surface_contract.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
