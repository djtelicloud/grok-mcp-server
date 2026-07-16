# `tests/test_phase5.py` refactor plan (Loop 43)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 798 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−89%** |
| Classes / tests | 10 / ~30 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/phase5/test_job_manager.py` | TestJobManager |
| `tests/phase5/test_research_tools.py` | TestResearchTools |
| `tests/phase5/test_resources_prompts.py` | TestResourcesAndPrompts |
| `tests/phase5/test_include_citations.py` | TestIncludeAndCitations |
| `tests/test_phase5.py` | shim ≤ 90 LOC |

Move-only.
