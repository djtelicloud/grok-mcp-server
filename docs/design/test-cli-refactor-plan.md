# `tests/test_cli.py` refactor plan (Loop 150)

Status: **in-tree** — plan only (single-branch accumulate; no per-file PR).

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 208 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-83%** |
| Hot | `test_installed_cli_never_trusts_cwd_dotenv_for_runtime` ~54 · `test_trusted_env_rejects_unsafe_modes_links_and_non_files` ~24 · `test_init_project_copies_example_env_and_prints_ide_configs` ~18 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split / `test_installed_cli_never_trusts_cwd_dotenv_for_runtime` | extract hot path (~54 LOC) |
| split / `test_trusted_env_rejects_unsafe_modes_links_and_non_files` | extract hot path (~24 LOC) |
| split / `test_init_project_copies_example_env_and_prints_ide_configs` | extract hot path (~18 LOC) |
| `tests/test_cli.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
