# `src/tools/media.py` refactor plan (Loop 81)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 290 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `generate_video` ~121 · `generate_image` ~87 · `extend_video` ~46 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/tools/media_image.py` | generate_image |
| `src/tools/media_video.py` | generate_video / extend_video |
| `src/tools/media.py` | register_media_tools facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
