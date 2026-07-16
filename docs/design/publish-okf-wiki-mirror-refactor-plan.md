# `scripts/publish_okf_wiki_mirror.py` refactor plan (Loop 94)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 234 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `build_mirror` ~69 · `_pack_files` ~30 · `main` ~21 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `build_mirror` |
| split module | concern from hot path `_pack_files` |
| split module | concern from hot path `main` |
| `scripts/publish_okf_wiki_mirror.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
