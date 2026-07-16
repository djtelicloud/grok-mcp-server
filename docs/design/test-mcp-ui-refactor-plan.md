# `tests/test_mcp_ui.py` refactor plan (Loop 35)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 730 |
| Projected primary LOC | ~90 shim |
| % LOC change (primary file) | **−88%** |
| Tests | ~28 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| File | Concern |
|------|---------|
| `tests/mcp_ui/test_static_assets.py` | static file serving |
| `tests/mcp_ui/test_swarm_playground.py` | swarm playground honesty |
| `tests/mcp_ui/test_asset_version.py` | single-sourced asset version |
| `tests/mcp_ui/test_markdown_renderer.py` | shared escape-first renderer |
| `tests/test_mcp_ui.py` | shim ≤ 90 LOC |

Move-only.
