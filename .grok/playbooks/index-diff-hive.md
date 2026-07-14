# Index-diff hive (cheap parallel deep-think)

**Audience:** GitHub-authenticated UniGrok contributors only.  
**Public users:** never see this surface (root-as-lava / simple MCP product).

## Economics (locked)

| Plane | When | Cost shape |
| --- | --- | --- |
| **CLI / IDE subscription** (laptop open, Docker ported auth) | Default for all hive polls | Fixed plan cost — **max throughput** |
| **API** (OpenAI / Anthropic / Gemini / xAI metered) | Throttle, failure, or CLI capability gap | Metered — failover only; monthly envelope **$2000** |

Do not burn API thinking modes for index-diff. Use **single-shot low-output** calls (`fast` / CLI composer).  
Measured: UniGrok `thinking` on a 12-line protocol still spent ~$0.06 / ~25k tokens (internal reflection).  
`fast`+CLI returned 12 lines at **$0.00** subscription.

## Protocol v0

### Claim index (canonical document)

```text
L1: ...
L2: ...
```

Content-address the claim set (SHA of normalized lines). All polls bind to that SHA.

### Poll prompt (≤500 chars)

```text
PROTOCOL: index-diff only. No prose.
Output EXACTLY: L<n>|<GOOD|BAD|UGLY|STUPID|KEEP|KILL>|<≤12 words>
Max N lines. No fences.
```

Plus the claim index (or hash + git path for models that can pull).

### Valid tags

| Tag | Meaning |
| --- | --- |
| GOOD | Sound, ship |
| KEEP | Sound with minor caveats |
| BAD | Flawed but fixable |
| UGLY | Overengineered / undefined mechanism |
| STUPID | Buzzword / no mechanism |
| KILL | Drop from design |

### Git note store (insider)

```text
git notes --ref=unigrok-index-diff add -m "$(poll_body)" <claim-sha-or-commit>
```

Note body:

```text
model: <id>
plane: cli|api
cost_usd: <n>
claim_sha: <sha>
L1|KEEP|...
...
```

### Aggregation (not ARGPO-by-name until formalized)

Default **mechanical** aggregator (donor-aligned UCB1 / majority, not invented ARGPO):

1. Map tags → scores: GOOD=2 KEEP=1 BAD=0 UGLY=-1 STUPID=-2 KILL=-3  
2. Per-line mean across models; discard models with parse failure.  
3. Decision: mean ≥ 1 → KEEP; ≤ -1 → KILL; else revise claim and re-poll.  
4. Optional UCB1 weight by model historical parse-success / agreement (from donor `model_arena` idea).

Deep-Think and Claude both flagged raw “ARGPO over index scores” as **UGLY/undefined** until a formal update rule exists. Ship majority+UCB1 first.

## Parallel polls (emit surface only)

Index-diff is a **micro-emit format**, not the intelligence engine.

| Step | Who | Tokens |
| --- | --- | --- |
| 1 Emit claim index + SHA | UniGrok / Grok Build | local |
| 2 Fan-out index-diff polls | Grok CLI, Claude CLI, Gemini CLI, … **CLI first** | low out |
| 3 Write notes | agent | free |
| 4 Aggregate | local script | free |
| 5 Only if contested lines remain | one Deep-Think **on contested subset only** | metered |

This is **not** multi-vendor essay democracy. It is **index consensus** then optional expensive reflection on disagreements only.

**Real force:** silent-think harness — high internal thinking budget, `include_thoughts=false`, tiny structured emit (pydantic / index-diff / any fixed shape). See [silent-think-harness.md](silent-think-harness.md). Token counts that prove work are **thought tokens in usage**, not printed CoT.

## Donor physics this reuses

- **omni_router** TF-IDF vector spaces over skills/synapses/models (cheap local).  
- **UCB1** champion selection (`swarm_rank_models` / `ucb_router`).  
- **optimize_params** `thinking_budget` vs `max_output_tokens` split (compute ≠ print).  
- **Recursive maxing** evolution loop: compress `max_output_tokens` until fitness holds.  
- **response_schema + pydantic** tiny emit after silent work.  
- **synapse_recorder / walrus notes** for immutable harvest.  
- **Chrysalis** AST gate before anything becomes real code.  
- Public never sees hive; insiders opt into GitHub-auth cloud mind.

## Public vs insider (non-negotiable)

| Surface | Hive / index-diff / notes |
| --- | --- |
| Public MCP `:4765` / vibe README | **OFF** |
| Contributor Forge / Control write+ | **ON** (opt-in) |
| Hosted review | separate metered path; still CLI-first where possible |

## Live poll sample (2026-07-14)

Claim C (hive index-diff design). Models:

| Model | Plane | Cost | Notable |
| --- | --- | --- | --- |
| grok-composer-2.5-fast | CLI | $0 | L4 ARGPO→UGLY; schema missing→STUPID without claim index |
| Deep-Think grok-4.5 thinking | API | ~$0.058 | GOOD on cheap poll + insider gate; UGLY on ARGPO |
| Claude (host CLI, Sonnet-class) | sub | plan | L5 notes UGLY fragility; L6 ARGPO UGLY; L7 STUPID without mechanism |

Consensus: **index-diff protocol GOOD/KEEP; formal ARGPO name deferred; claim schema required; public off; CLI first.**
