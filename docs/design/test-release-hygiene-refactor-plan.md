# `tests/test_release_hygiene.py` refactor plan (Loop 56)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 468 |
| Projected primary LOC | ~60 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~21 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/release/test_scratchpad_cleanup.py` | disposable scratchpad consistency |
| `tests/release/test_credential_ignore.py` | local provider credentials ignored |
| `tests/release/test_dual_supervisor.py` | dual-supervisor land law |
| `tests/release/test_human_radio.py` | agent human-radio silence |
| `tests/test_release_hygiene.py` | shim ≤ 60 LOC |

Move-only; no README/public-artifact edits.
