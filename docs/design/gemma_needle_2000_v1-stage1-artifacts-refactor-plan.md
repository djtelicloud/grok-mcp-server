# `evals/campaigns/gemma_needle_2000_v1/stage1_artifacts.py` refactor plan (Loop 95)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 231 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-85%** |
| Hot | `PrivateArtifactStore` ~159 · `canonical_json_bytes` ~15 · `ArtifactRef` ~11 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `PrivateArtifactStore` |
| split module | concern from hot path `canonical_json_bytes` |
| split module | concern from hot path `ArtifactRef` |
| `evals/campaigns/gemma_needle_2000_v1/stage1_artifacts.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
