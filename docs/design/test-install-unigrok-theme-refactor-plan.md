# `tests/test_install_unigrok_theme.py` refactor plan (Loop 65)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 395 |
| Projected primary LOC | ~50 shim |
| % LOC change (primary file) | **−87%** |
| Tests | ~13 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/theme_install/test_roundtrip.py` | install_check_roundtrip / force overwrite |
| `tests/theme_install/test_enable_rejects.py` | git checkout / symlink destination rejects |
| `tests/test_install_unigrok_theme.py` | shim ≤ 50 LOC |

Move-only.
