# `src/provider_harvest.py` refactor plan (Loop 28)

Status: **Ready for supervisor** — plan only.  
Pairs with: test plan #360.

## Why not a mega rewrite

**~816 LOC**. Hot: `XAIWorkerEpisodeUploader` ~242, `ProviderAttemptHarvester` ~205. Split upload vs harvest orchestration.

## Baseline (before)

| Metric | Value |
|--------|------:|
| LOC | 816 |
| Projected primary LOC | ~120 facade |
| % LOC change (primary file) | **−85%** |
| Classes / funcs | 5 / 14 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern | Projected LOC |
|--------|---------|--------------:|
| `src/provider_harvest/document.py` | `_build_worker_episode_document` | 100–140 |
| `src/provider_harvest/uploader.py` | XAIWorkerEpisodeUploader | 220–280 |
| `src/provider_harvest/harvester.py` | ProviderAttemptHarvester | 180–240 |
| `src/provider_harvest/authority.py` | effect authority helpers | 40–80 |
| `src/provider_harvest.py` | facade | ≤ 120 |

## Migration order

document → authority → uploader → harvester → facade. Pair #360. Move-only.

## Non-goals

Cloud harvest policy changes; landing `main`.
