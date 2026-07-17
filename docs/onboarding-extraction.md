# Onboarding extraction design (post-launch)

Harvest of internal donor-research ideas into UniGrok's onboarding plan and facts
system. Two tracks. Server writes nothing — the IDE executes plans with its
own permissions (existing `grok_mcp_onboard_client` contract).

## Track A — dynamic IDE settings in the onboarding plan

Donor scripts (`ide_hijack.py`, `settings_sync.py`) generate settings from
templates but clobber user edits. We keep the good mechanics, drop the harm.

**Portable algorithm (plan emits instructions, never file writes):**

```
INPUTS (server-side, read-only):
  T = template of MANAGED keys -> desired values (namespaced under "unigrok")
  D = deprecation map {oldKey -> newKey | null}     # the piece donors lacked
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

**Non-negotiables (fix the donor's trust traps):**
- Never overwrite a key outside `T∪D`; always emit a diff for keys inside it.
- Global user settings is a separate, explicitly-consented target.
- Do NOT port `hunt_extension_friction`'s guess-by-name value synthesis (it
  disabled safety confirmations). Discover keys from manifests only as
  *suggestions*, never auto-applied.
- Keep from donor: JSONC tolerance, `.bak` backup, classify-before-write
  accounting, dedupe-by-id, and the `settings.local.json` manifest-of-pointers
  shape (`rules.autoLoad` + `agents.systemPromptFile`) — this is exactly our
  "return a plan of pointers, don't write content" model.

## Track B — reflex/synapse memory onto the facts system

Donor `omni_router.py` does per-turn TF-IDF selection of short notes;
`synapse_recorder`/`decay` weight them by outcome.

**What UniGrok already covers (do NOT rebuild):**
- Auto-appearing short notes → `remember_fact` + per-turn `search_facts(prompt,
  limit=5)` injected as "Durable user-controlled knowledge". This is the DB
  analog of reflex files, and the `limit=5` top-k is *better* than the donor's
  "inject every reflex every turn" default (their main context-bloat trap).

**Small, high-value additions (server-side, schema tweaks):**
1. Relevance floor on fact injection (mirror donor `T_REFLEX≈0.40`) so weak
   matches aren't injected.
2. Fact `category` tag (`reflex` vs plain user fact) to distinguish procedural
   scars from preferences.
3. Outcome-weighted facts: `weight` column, +reinforce/−penalize on outcome,
   prune at ≤0 (donor's UCB loop; hook Forge's `record_landed_outcome`).
4. Dedup on `remember_fact` (content-aware slug) to avoid near-duplicates.

**IDE-side only (can't live in the DB):** if file-based reflexes are wanted,
the onboarding plan emits the `.md` files plus the
`chat.instructionsFilesLocations` config; the IDE writes them. Bound the count
(don't inherit "inject all").

**Traps to avoid from the donor:** append-only log with no dedupe (stale-weight
duplicates win argmax); filename-only novelty (reflex written once, never
updated/expired); toxic entries with empty golden path winning selection. Our
DB + top-k + relevance floor sidesteps most of these by construction.

## Sequencing

Both tracks are post-launch. Track B item 1–2 (floor + category) are the
cheapest and could ride an early point release. Everything else waits until the
public graft is stable and the soak data is in.
