# `scripts/install_unigrok_theme.py` refactor plan (Loop 53)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 478 |
| Projected primary LOC | ~60 facade |
| % LOC change (primary file) | **−87%** |
| Funcs | 16 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/theme_install_core.py` | install |
| `scripts/theme_check.py` | check |
| `scripts/theme_enable.py` | enable_config |
| `scripts/install_unigrok_theme.py` | main facade ≤ 60 LOC |

Move-only; no README/public-artifact edits in this packet.
