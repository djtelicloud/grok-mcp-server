# Public product vs private intelligence git

- **Status:** Accepted (2026-07-14)
- **Owner:** Project sponsor; agents follow this split without re-asking

## Decision

UniGrok uses **two git systems of record**:

| System | Repo | Visibility |
| --- | --- | --- |
| **Product** | `djtelicloud/grok-mcp-server` | **Public** |
| **Intelligence** | `djtelicloud/unigrok-intelligence` | **Private** |

This restores the old Agentix pattern (public surface + private brain) without
pretending public history can be unpublished.

## What stays public

- MCP gateway, dual planes, OKF product knowledge
- Control / hosted review **product** wiring
- Public schemas needed for interoperability
- Tests and release hygiene for the product

## What stays private

- Silent-think / hive / dialect harvest / donor reverse-eng playbooks
- Experimental control-token libraries and optimizer loops
- Campaign ops, live datasets, DPO notes, tournament harvests
- Strategic AKE “domination” writeups beyond product authority docs
- Contributor-only parallel-ship ops detail

## What is never git

- API keys, OAuth signing secrets, GitHub app private keys
- Live Cloud Run env, membership bindings, customer data

## History note

Files previously committed to public `main` remain in git history. Hygiene
removes them from the **current tree** and stops the bleed. Competitive IP
already disclosed is accepted; future IP must not re-enter the public tree.

## Agent rule

If a task is intelligence/process research, open a PR against
`unigrok-intelligence`. If a task is product user-facing behavior, PR against
`grok-mcp-server` only.
