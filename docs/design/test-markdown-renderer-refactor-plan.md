# `tests/test_markdown_renderer.py` refactor plan (Loop 100)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 213 |
| Projected primary LOC | ~35 facade |
| % LOC change (primary file) | **-84%** |
| Hot | `test_okf_corpus_renders_without_mangling_artifacts` ~20 · `render_fixture` ~17 · `test_html_injection_is_escaped_everywhere` ~12 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| split module | concern from hot path `test_okf_corpus_renders_without_mangling_artifacts` |
| split module | concern from hot path `render_fixture` |
| split module | concern from hot path `test_html_injection_is_escaped_everywhere` |
| `tests/test_markdown_renderer.py` | facade ≤ 35 LOC |

Move-only. Leave PR #408 alone.
