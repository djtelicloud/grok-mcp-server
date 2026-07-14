# GenFuncAgentixAI deep harvest (not orphaned — unfinished DAG)

**Donor:** `djtelicloud/GenFuncAgentixAI` (private)  
**Mistake corrected:** `main` is a shallow snapshot (1 commit). Real work lives on
**long-lived remote branches** (50–119 commits ahead of main). Those branches
were not trash; they are the crucible / twin / sky-command continuum.

## Branch inventory (fetched 2026-07-14)

| Branch | Ahead of main | What it actually is |
| --- | ---: | --- |
| `twin/chrysalis` | 50 | NSGA-II / Pareto flywheel commits (`gen=1058000`), **Sky Command Apex**, Pub/Sub spine, daemons (`chrysalis_daemon`, `swarm_flywheel`, `reality_sync`), cloud DNS, swarm evolver |
| `twin/gemma_dpo_reflection` | 103 | Cortex brain: `omni_router`, `synapse_recorder` → **git branch as memory**, DPO/telemetry, Cloud Twin GH Actions, WalrusSynapse |
| `twin/ruff_sanitation` | 119 | Symbiotic playground FastAPI, `apps/swarm_evolver` evolution engine, compliance gates |
| `twin/singularity` | 65 | **PHASE5_CLOUD_TWIN** topology, GitOps intent API, deploy twin, WIF forge PR #3 |
| `snaps` | 50 | “Assimilate redundant cloud twin + gitops CI/CD” snapshot |
| `app/singularity` / `app/cortex_enclave` | 50–57 | App/enclave / Antigravity extension streams |
| `dev/app/sovereign_terminal` | 50 | Dev terminal (commits into 2026-07) |
| `prod/apps/sovereign_terminal` | 50 | Prod terminal stream |
| Open PRs #1–#3 | — | WIF bootstrap, auth harden, architecture matrix — still open |

## Proven doctrine (from branch docs, not main marketing)

### 1. Redundancy Physics / Cloud-Truth
- Local IDE = **hologram** (volatile projection).
- **Cloud orchestration + GitHub GitOps** = source of truth.
- Local agents should emit **intent**, not edit control-plane guts.
- Return path: cloud mutates **git**; local machines **fetch** truth.

### 2. Cloud Twin purpose (PHASE5)
Cloud Twin converts **intent envelopes** into **deterministic Git-backed mutations**:

```text
local intent → WebMCP/relay → Cloud Twin API /v1/swarm/intent
  → isolated workspace clone → agents produce Change Envelope
  → GitOps commit/push → local IDE re-renders from git
```

Not: “put the chat model in a mutable container and git reset yourself.”

### 3. Sky Command vs Twin vs Ground
- **Sky Command** — control plane / dashboard / command Pub/Sub (push).
- **Cloud Twin** — maintenance shadow / GitOps / Eventarc-cron style work.
- **Ground** — heavy compute pulls commands (zero inbound ports).

### 4. Agent memory on git DAG
- `synapse_recorder` commits golden/toxic trajectories to `brain/synapse`.
- `omni_router` TF-IDF ranks skills/workflows/reflexes/**models** (UCB weights).
- `walrus_dpo_chosen` git notes = DPO/victory memory (when present).
- Chrysalis daemon: AST firewall + ruff + notes on verified mutations.

### 5. Crucible / benchmarks (evidence of seriousness)
- `evolution_engine.py`: NSGA-II, multi-objective Pareto, GPU-saturating design,
  feeds DPO via git notes.
- Twin commit messages encode live Pareto metrics (f1…f5).
- Swarm compliance master gate before commit (ruff, GNO DAG, root litter, etc.).

## What to port into UniGrok (mechanically)

| Harvest | UniGrok target | Notes |
| --- | --- | --- |
| Intent → GitOps worker | Future **maintenance twin** (not public MCP) | Separate service from `mcp.grokmcp.org` inference |
| Cloud-truth + local hologram | Already: land receipts + workspace memory notes | Strengthen: agents never ask human for git |
| Multi-agent bus = branches/PRs/notes | Draft PRs + exact heads + `Agent-*` trailers | Restore cron/automation that wakes lander |
| UCB1 / model catalog ranking | `model_catalog` + routing telemetry | Prefer Grok stack for default intelligence |
| **Silent-think harness** | `_parse_structured` + CLI `--json-schema` + receipts | High think budget, `include_thoughts=false`, tiny pydantic emit; see [silent-think-harness.md](silent-think-harness.md) |
| **Dialect control-token library** | Reference harvest (not runtime yet) | [`.grok/harvest/dialect-control-tokens/`](../harvest/dialect-control-tokens/) — matrix + `DialectCompiler`; optimizer loop forced small models |
| Recursive maxing (max_out compress) | Optional specialist param search | EVOLUTION.md patience loop; VPP-style cost ledger later |
| Swarm compliance pre-commit | Existing tests + land scripts | Keep fail-closed |
| Sky vs Twin split | Control vs MCP twin | Control = AS/broker; MCP = API inference only |
| Isolated clone per mutation | Cloud worker pattern | Never shared mutable working tree |

## What NOT to port

| Reject | Why |
| --- | --- |
| Mutable Cloud Run + `git reset --hard` as product MCP | Ephemeral disk, secret sprawl, un-auditable |
| Static long-lived twin secrets as sole auth | Prefer OIDC/OAuth |
| Committed SA/Firebase JSON | Credential leak class |
| Unbounded multi-provider fan-out “for opinions” | Cost + often worse than Grok self-parallel |
| Forcing human into git midwifery | Violates donor’s own redundancy physics |

## Implication for UniGrok agent sessions

1. **Continue until handoff triad is on GitHub** — do not stop to ask humans for mathematically settled choices.
2. **Default intelligence = Grok modes on UniGrok** (CLI first; API for thinking when required). Fan-out to other vendors only for specialized tools/data, not democracy of chatbots.
3. **Budget envelope** (operator-set): e.g. **$2000/month** total API; session hard-stops leave notes on PR, not questions.
4. **Orphaned donor branches are a harvest backlog**, not abandoned code — work them via read-only analysis → UniGrok design PRs → Codex land.
