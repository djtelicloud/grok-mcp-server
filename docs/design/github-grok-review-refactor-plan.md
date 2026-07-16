# `scripts/github-grok-review.py` refactor plan (Loop 71)

Status: **Ready for supervisor** — plan only.

## Baseline

| Metric | Value |
|--------|------:|
| LOC | 350 |
| Projected primary LOC | ~45 facade |
| % LOC change (primary file) | **−87%** |
| Hot | `main` ~94 · `_gateway_bearer_token` ~42 |

## Hive / swarm

Forge MCP Not connected — plan path.

## Proposed modules

| Module | Concern |
|--------|---------|
| `scripts/grok_review_github.py` | _github_request |
| `scripts/grok_review_auth.py` | _gateway_bearer_token |
| `scripts/grok_review_evidence.py` | _evidence_provenance |
| `scripts/grok_review_cli.py` | main orchestration |
| `scripts/github-grok-review.py` | thin entry ≤ 45 LOC |

Move-only; leave peer docs / PR #408 alone.
