# Consent-first onboarding and fact-routing design

Public design for extending UniGrok's onboarding plan and facts system. Two tracks.
The server writes nothing — the IDE executes plans with its own permissions under the
existing `grok_mcp_onboard_client` contract. This is design-only unless a behavior is
also documented in the technical reference and covered by runtime tests.

## Track A — dynamic IDE settings in the onboarding plan

Settings plans need template-driven updates without clobbering user edits.

**Portable algorithm (plan emits instructions, never file writes):**

```
INPUTS (server-side, read-only):
  T = template of MANAGED keys -> desired values (namespaced under "unigrok")
  D = deprecation map {oldKey -> newKey | null}
  targets = [{path, format}]  # global/workspace settings.json (JSONC), .gemini (JSON)

PLAN PER TARGET (instructions for the IDE):
  1. READ target JSONC-aware (strip //, /* */, BOM, trailing commas)
  2. CLASSIFY: managed = keys(existing) ∩ keys(T);
     user_only = keys(existing) − keys(T)  -> PRESERVE verbatim;
     new = keys(T) − keys(existing)
  3. DEPRECATION PASS: migrate old->new (or drop), recorded explicitly
  4. DEEP MERGE: recurse where both are dicts; leaf overwrite ONLY for keys
     in T∪D — a key outside T∪D is structurally untouchable
  5. LIST BLOCKS (tasks/registry): dedupe by stable id, append missing
  6. MANIFEST KEYS: rules.autoLoad (ordered), agents.systemPromptFile,
     skills/workflows registry — all namespaced, pointing at plan-created paths
  7. OUTPUT: merged preview + diff + conflict list + .bak instruction
```

**Non-negotiables:**
- Never overwrite a key outside `T∪D`; always emit a diff for keys inside it.
- Global user settings is a separate, explicitly-consented target.
- Do not synthesize settings values from extension names or disable safety
  confirmations. Discover keys from manifests only as
  *suggestions*, never auto-applied.
- Preserve JSONC tolerance, `.bak` backup instructions, classify-before-write
  accounting, dedupe-by-id, and a namespaced manifest of pointers. This matches the
  "return a plan, never write content" model.

## Track B — reflex/synapse memory onto the facts system

The future route may select a small set of short, relevant notes per turn and adjust
their ranking only from explicit outcomes.

**What UniGrok already covers (do NOT rebuild):**
- Auto-appearing short notes → `remember_fact` + per-turn `search_facts(prompt,
  limit=5)` injected as "Durable user-controlled knowledge". This is the DB
  analog of reflex files. The `limit=5` top-k avoids injecting every note on every turn.

**Small, high-value additions (server-side, schema tweaks):**
1. A measured relevance floor on fact injection so weak matches are not injected.
2. Fact `category` tag (`reflex` vs plain user fact) to distinguish procedural
   scars from preferences.
3. Outcome-weighted facts: reinforce or penalize only from explicit feedback, retain the
   evidence receipt, and prune at a reviewed floor.
4. Dedup on `remember_fact` (content-aware slug) to avoid near-duplicates.

**IDE-side only (can't live in the DB):** if file-based reflexes are wanted,
the onboarding plan emits the `.md` files plus the
`chat.instructionsFilesLocations` config; the IDE writes them. Bound the count
(don't inherit "inject all").

**Traps to avoid:** append-only logs with no dedupe (stale-weight
duplicates win argmax); filename-only novelty (reflex written once, never
updated/expired); toxic entries with empty golden path winning selection. Our
DB + top-k + relevance floor sidesteps most of these by construction.

## Sequencing

Both tracks are post-launch. Track B item 1–2 (floor + category) are the
cheapest and could ride an early point release. Everything else waits until the
public graft is stable and the soak data is in.
