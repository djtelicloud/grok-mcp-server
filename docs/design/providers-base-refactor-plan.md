# `src/providers/base.py` refactor plan (Loop 68)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 380 |
| Projected primary LOC | ~45 facade |
| % LOC change (primary file) | **−88%** |
| Hot | `HTTPProviderAdapter` ~329 LOC |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `src/providers/http_adapter_core.py` | HTTPProviderAdapter core request path |
| `src/providers/http_adapter_headers.py` | auth / header helpers |
| `src/providers/fingerprint.py` | opaque_fingerprint |
| `src/providers/base.py` | facade ≤ 45 LOC |

Move-only; adapter public surface unchanged.
