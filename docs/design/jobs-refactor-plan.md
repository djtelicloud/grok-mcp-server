# `src/jobs.py` refactor plan (Loop 72)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 381 |
| Projected primary LOC | ~45 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `JobManager` ~284 LOC |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/jobs_owner.py` | resolve_job_owner / timeout / concurrency helpers |
| `src/jobs_manager.py` | JobManager |
| `src/jobs.py` | facade ≤ 45 LOC |

Move-only; job ownership semantics unchanged.
