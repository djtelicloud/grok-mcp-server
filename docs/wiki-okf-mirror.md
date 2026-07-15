# GitHub Wiki as OKF mirror (only)

## Decision

The GitHub Wiki tab may be a **human-friendly mirror** of the public OKF bundle
and public intelligence packs. It is **never** source of truth.

| Role | Surface |
| --- | --- |
| **Source of truth** | `docs/okf/` in this repo + https://grokmcp.org/docs/okf/ |
| **Optional mirror** | GitHub Wiki (auto or script-generated) |
| **Agents** | Prefer OKF via `discover_self` / site / local `/docs/okf/` |

## Rules

1. **Do not hand-edit** wiki pages as product docs.
2. **Regenerate** from OKF on release via
   `scripts/publish_okf_wiki_mirror.py`; it prunes stale pages and includes the
   manifest-listed JSON schema/data artifacts as rendered pages.
3. Wiki pages must say they are a **mirror** and link to OKF index.
4. If the mirror is stale or missing, that is not a product outage — use OKF.
5. Publish with a deletion-aware copy such as the script's printed
   `rsync --delete` recipe so removed sources cannot survive in the wiki.

## Why this is better than an empty wiki

Humans open the Wiki tab. An empty tab feels broken. A **generated** mirror
matches OKF without a second brain to maintain by hand.

## Why this is safer than a free-form wiki

Hand-edited wikis drift. Agents might trust them. Mirror-only + regen keeps
one knowledge pipeline: **repo OKF → site → optional wiki**.
