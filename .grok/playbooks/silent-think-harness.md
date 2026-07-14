# Silent-think harness (the real force)

**Audience:** GitHub-authenticated UniGrok contributors only.  
**Not public product.** Index-diff is a *micro-emit* option, not the doctrine.

## Thesis

Intelligence cost is dominated by **printed tokens**. The donor GenFunc physics
separates:

| Dial | Role | Want |
| --- | --- | --- |
| **thinking_budget** / thinking_level | Internal compute the provider bills as *thought* tokens | High when hard |
| **include_thoughts** | Whether thought text is streamed into the user-visible channel | **False** (silent) |
| **max_output_tokens** | Visible completion budget | **Tiny** — only the emit |
| **response_schema / pydantic** | Forces the emit into a fixed shape | Tiny model, not an essay |

Token counts that matter for “model did real work” come from **usage metadata
thought/reasoning tokens**, not from CoT prose the user (or poll log) can read.

Index-diff (`L#|TAG|reason`) is one cheap **emit surface**. It is not the
mechanism that made small models cheap and fast.

## Control-token library (Dialect Matrix)

Donor name: **Token Control Language** / Native Latent IPC.

| Artifact | Location |
| --- | --- |
| Harvest (this PR) | [`.grok/harvest/dialect-control-tokens/`](../harvest/dialect-control-tokens/) |
| Matrix | `dialect_matrix.json` (gemini, gemma, llama3, grok, mistral, openai_o1) |
| Compiler | `dialect_compiler.py` — `compile(..., force_tool=…, force_reasoning=…)` |
| Optimizer loop | GenFunc `swarm_dialect_optimizer.py` (mutate lock/prefill until small models submit) |

Prefill + family locks force **visible** channel shape. Silent-think still owns
internal budget; control tokens own **first-token coercion** so small models
do not spill essays. Not the same as giant JSON schemas.

## Donor evidence (what actually exists)

### 1. ParamSet split (`optimize_params.py`)

```text
ParamSet:
  thinking_budget: int   # 0 = thinking disabled
  max_output_tokens: int # independent of thinking
  temperature, top_k, top_p, …
```

`thinking_budget` scales with complexity when the catalog marks the model
`thinking=true`. `max_output_tokens` is a separate ceiling. That split is the
load-bearing idea: **compute ≠ print**.

### 2. `ThinkingConfig` surface (google-genai)

```text
ThinkingConfig:
  include_thoughts: Optional[bool]
  thinking_budget:  Optional[int]
  thinking_level:   Optional[ThinkingLevel]
```

Wired into `GenerateContentConfig` alongside `max_output_tokens`,
`response_mime_type`, `response_schema`.

### 3. Incomplete donor wiring (honest gap)

| Commit era | Behavior |
| --- | --- |
| Early `core.py` | Loaded `include_thoughts` from env and **passed it into** `ThinkingConfig` when true |
| Post-optimizer `core.py` | Maps `thinking_budget` → `thinking_level` only; **still loads** `include_thoughts` in config but **does not pass it** into `ThinkingConfig` |
| Catalog default | `GOOGLE_VERTEX_INCLUDE_THOUGHTS` defaults **True** (loud thoughts) |

So the *doctrine* (silent think) is only half-landed in GenFunc. UniGrok should
port the **intended** physics, not the incomplete default.

### 4. Recursive maxing loop (`evolution_engine` + `EVOLUTION.md`)

Not “print a diff.” A **harness loop** over hyperparams:

- Genome: `(temp, top_k, top_p, max_output_tokens)`
- Fitness: high score only for strict short code, **penalize** markdown /
  “Here is…”, minimize latency and token proxy
- **N ≤ 10** generations, **patience = 3** early stop
- Victory genome written to git DAG; VPP ledger mines **USD saved** when
  `max_output_tokens` converges below baseline (e.g. 4096 → optimized)

That is “loop each harness until forced” in economic form: keep mutating until
the model delivers work under a **compressed visible budget**.

### 5. Structured emit after silent work

Walrus `SemanticValidation` pydantic + `response_mime_type=application/json` +
`response_schema=SemanticValidation` — the model’s **visible** channel is four
fields, not a monologue.

UniGrok already has the same family:

- `ReflectionVerdict` / `FactList` via `_parse_structured` + `chat.parse`
- CLI: `grok --json-schema` from pydantic `model_json_schema()`

### 6. Policy silence (UX twin of token silence)

- Terminal Silence Law / Crucible: fix in the dark; user sees victory only
- Chat looper: “The reflection loop is silent. Never expose policy scaffolding…”

Same invariant at two layers: **don’t print the private workspace**.

## The harness (target UniGrok port)

### Per-provider adapter contract

```text
SilentThinkRequest:
  task: str
  emit_model: type[BaseModel]     # tiny pydantic
  thinking: { budget|level|effort }  # plane-native
  include_thoughts: false            # always for this path
  max_output_tokens: len_budget(emit_model)  # e.g. 64–512
  plane_pref: cli_first | api_only
```

### Loop (bounded)

```text
for adapter in ordered_harnesses:          # UCB1 / catalog, CLI first
  for attempt in 1..K:                     # small K, shared $ budget
    cfg = silent_think_cfg(adapter, attempt)
    result = adapter.generate(task, cfg)
    ok = (
      schema_valid(result.emit, emit_model)
      and not thought_leak(result.text)    # no CoT markers / fences of thought
      and (result.thought_tokens > 0       # when provider reports them
           or adapter.guarantees_silent_think)
    )
    record_ucb(adapter, ok, cost, latency)
    if ok: return result
  # else next adapter
fail closed / escalate one expensive deep-think on residual only
```

**“Forced”** means: the run is not accepted until the emit is schema-valid and
the visible channel stayed short. Thought tokens may be large; **printed**
tokens must not be.

### What “smaller models below normal cost” means

| Naive path | Silent-think path |
| --- | --- |
| Small model, high `max_tokens`, freeform essay CoT | Small/flash model, high internal think, **tiny** structured emit |
| Pay for printed reasoning every poll | Pay thought tokens once; emit ~dozen–hundred tokens |
| Index-diff only if you need multi-model consensus | Index-diff = optional poll format, not the compute policy |

CLI / subscription planes amplify this: many silent structured calls ride fixed
plan cost; API is failover.

## Schemas: do we give two shits? (pydantic + Needle)

**Three different “schema” objects. Only two matter.**

| Object | Care? | Why |
| --- | --- | --- |
| **Capsule / Needle pin digests** (`intelligence_payloads` PROFILE_SCHEMA_SHA256, Needle 1024-encoder packing) | **Yes, fail-closed** | Byte-stable storage + packing geometry. Not model creativity. |
| **Tiny pydantic emit** (`ReflectionVerdict`, `SemanticValidation`, `FactList`, poll rows) | **Yes** | Cheap channel for silent compute. The schema *is* the max-output discipline. |
| **Giant narrative OpenAPI / “describe your whole answer as schema” theater** | **No** | You pay tokens for schema prose and train the model to fill fields with essays. |

### Needle is not a thinking schema

Needle v1 (`org.grokmcp.needle_tools_context.v1`) packs **verified examples**
into a tools-JSON channel under a **fixed 1024 encoder-token** budget. Whole
records only; never string-sliced. That is **input packing**, not CoT structure.

Judging Needle as “small model must print the right answer” is the wrong
test. Needle is a **synapse/reflex prior** for Grok (authority inversion:
LLM first → Needle second → code floor). Silent-think still belongs to the
LLM tier; Needle does not replace it.

### With pydantic + Needle, the practical rule

1. **Pin** envelope schemas (capsule digests) — structural integrity only.  
2. **Emit** with the smallest pydantic model that a downstream machine can trust.  
3. **Do not** invent large freeform schemas for hive polls or reflection.  
4. **Do not** require index-diff as the only emit — any tiny structured type works.  
5. **Do** force `include_thoughts=false` (or provider equivalent: no visible
   reasoning stream) and cap **visible** `max_output_tokens` to the emit.

If pydantic validates the emit and Needle (or FTS/AKE) packed the priors, you
already have the only schemas that pay rent. Everything else is ceremony.

## UniGrok mapping (current → target)

| Current | Gap | Target |
| --- | --- | --- |
| `mode=thinking` → API plane, full ReAct + reflection loop | Reflection is structured; main attempt can still print a lot | Silent-think **profile** on tool-free structured parse and on CLI `--json-schema` paths |
| `_parse_structured` + pydantic | Already tiny emit | Pair with explicit low max-out + effort/thinking knobs |
| CLI `--effort` + `--json-schema` | No unified “silent” receipt (thought vs completion) | Receipt fields: `thought_tokens`, `completion_tokens`, `include_thoughts=false` |
| Index-diff hive playbook | Over-emphasized as the force | Demote to **emit option** under this harness |
| Needle projection | Pack only; no runtime specialist yet | Keep fail-closed; never confuse packing schema with silent-think |

## Anti-patterns

- Burning Deep-Think API for 12-line polls (measured ~$0.06 / ~25k tokens).  
- Setting high `max_output_tokens` “just in case” while thinking is on.  
- `include_thoughts=true` in production hive / swarm paths.  
- Multi-vendor essay democracy instead of structured emit + UCB1.  
- Treating Needle JSON Schema as the place intelligence lives.

## Relationship to index-diff

```text
silent-think harness  →  produces tiny structured emit
                              ↓
                    optional formats:
                      - pydantic JSON
                      - index-diff lines
                      - FactList / ReflectionVerdict
                              ↓
                    git notes / capsules / UCB1
```

Index-diff remains useful for **parallel cheap consensus**. It is not the
silent-think engine.

## Implementation order (when coding)

1. Document + receipt contract (this file) — **done**.  
2. Harvest dialect matrix/compiler as reference — **done** under
   `.grok/harvest/dialect-control-tokens/`.  
3. Wire silent profile into `_parse_structured` / CLI schema path (max-out +
   no visible thoughts + pydantic).  
4. Port safe family prefills from dialect matrix (Grok CLI/API first; Gemma
   local via mlx_lm think stripping).  
5. Port UCB1 model telemetry for silent-think success rate / cost.  
6. Optional: recursive maxing on `max_output_tokens` + dialect gene mutation.  
7. Keep Needle as packing + future reflex — separate track.
