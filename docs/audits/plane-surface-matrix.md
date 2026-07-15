# Plane & Surface Matrix — dual-plane routing & CLI surfaces

**Point-in-time audit snapshot. Not a Live product claim.**

- Audit HEAD: `e2c06347eceda1c739863344863e8da39f875cbb` (main)
- Date: 2026-07-15
- Method: read-only fan-out (5 parallel Explore passes) over `src/`, docs, and
  skills. Every verdict is cited to `file:line`. The four load-bearing
  root-cause claims were re-verified by direct read before publication.
- Author lane: Claude Code (map / contract / doc-truth). Runtime fixes flagged
  below belong to the Grok plane-truth lane; landing belongs to Codex.

Verdict legend: **Live** (wired + exercised) · **Partial** (wired but
incomplete/guarded) · **Stub** (present, not functional) · **Doc-only**
(referenced in docs/comments, no implementation) · **Absent** (named surface
does not exist).

---

## 1. Plane routing & fallback

| Capability | Verdict | Evidence |
|---|---|---|
| `_call_plane` API/CLI switch (`is_cli = plane in {"composer","cli-fallback"}`) | Live | `src/utils.py:10587` |
| Caller-side plane decision (`direct_cli`) | Live | `src/utils.py:11605` |
| `cli_first` default policy (local) / `api_first` (cloudrun) | Live | `src/credentials.py:64` |
| `cli_first` applied at model selection | Live | `src/utils.py:11177` |
| `prefer_cli_for_route` auto gating | Live | `src/utils.py:1001` |
| Selection-time `cross_plane` fallback | Live | `src/utils.py:11525` |
| Anti-ping-pong (downgrade to `same_plane` after selection fallback) | Live | `src/utils.py:11775` |
| CLI→API execution fallback gate | Live | `src/utils.py:12101` |
| API→CLI execution fallback + `same_plane` block | Live | `src/utils.py:12418` |
| Cross-plane capability incompatibility guard | **Partial — fails open on non-answer** | `src/utils.py:12469` |
| Routing receipt / `attempts` assembly | Live | `src/utils.py:11613`, `12035` |
| API cost/token accounting (provider-exact, degrades honestly) | Live | `src/utils.py:8356` |
| CLI cost/token accounting (hard-zero, unmetered by design) | Live-by-design | `src/utils.py:10835` |

Note: CLI `tokens=0`/`cost=0` is **intended** (subscription plane is unmetered)
— it is not, by itself, a failure signal anywhere in the code.

## 2. CLI surfaces (invoked vs documented)

Only three real `grok` exec sites exist, all in `src/utils.py`: chat
(`:10666`), readiness probe (`:388`), model discovery (`:1773`).

| Surface / flag | Verdict | Evidence |
|---|---|---|
| `grok` headless chat (`_call_plane`) | Live | exec `src/utils.py:10666` |
| `--output-format json` / `streaming-json` | Live | `src/utils.py:10637` |
| Native session ids (`--session-id` / `--resume` / `--fork-session`) | Live | `src/utils.py:929-937` |
| `--json-schema` (conditional on schema) | Live | `src/utils.py:951` |
| `--effort` (conditional on valid effort) | Live | `src/utils.py:943` |
| `--max-turns` (conditional on positive int) | Live | `src/utils.py:947` |
| `grok --check` for readiness | **Doc-only** — probe runs `grok models`, not `--check` | probe `src/utils.py:388` |
| `--best-of-n` | Doc-only | no code |
| `grok agent stdio` | Doc-only | no code |
| `grok agent serve` | Doc-only | no code |
| `grok agent leader` | Absent (name only in unrelated swarm bandit) | `src/swarm/router.py:64` |
| streaming-json completeness guard ("saw an `end` event?") | **Missing** | parser `src/utils.py:10495-10531` |

## 3. Modes (`auto` / `fast` / `reasoning` / `thinking` / `research`)

Dispatch: `src/tools/chats.py:169-187` collapses 5 modes into two orchestrate
flags (`thinking_mode`, `enable_agentic`) plus `mode` for reasoning/research.

| Mode | Verdict | Evidence |
|---|---|---|
| `auto` | Live | `src/routing.py:109-119`; AgentLoop `src/utils.py:11871` |
| `fast` | Live | `src/tools/chats.py:176`; fast path `src/utils.py:11954` |
| `reasoning` | Live (pins planning) | `src/utils.py:11317` |
| `thinking` | **Live, with fallback defect** | loop+reflection `src/utils.py:10131-10300`; defect `12469` |
| `research` | **Partial** — core Live, fan-out delegated to xAI (no local orchestration) | pin `src/routing.py:26`; `agent_count` param `src/utils.py:8602` |
| `thinking` reflection (`ReflectionVerdict`, schema-enforced) | Live | `src/utils.py:9888-9951` |
| Phoneword mode-dial ports | Live | `src/http_server.py:78-83` |
| `_is_nonanswer_completion` classifier | **Partial — misses arbitrary preambles** | `src/utils.py:7606-7643` |

## 4. Sessions & state

| Capability | Verdict | Evidence |
|---|---|---|
| `session` persist + reload/replay | Live | write `src/utils.py:12846`; replay `8614`, `10617` |
| MCP name ↔ native CLI id bridge | Live (stored UUID, **not** deterministic hash) | `src/utils.py:10755-10832` |
| Cross-plane continuity (shared SQLite transcript) | Live | `src/tools/chats.py:280-286` |
| Async job rows (status/result durable) | Live | `src/utils.py:2830` |
| Async job **execution** resume after restart | **Stub** — stale-only detection, never re-run | `src/jobs.py:281-292` |
| `serve` / `leader` session surfaces | Absent | `src/cli.py:133-175`; `src/server.py:153-166` |

---

## 5. Doc-vs-code delta (the headline output)

Ranked by impact. Items 1–2 are the live defect; 3–8 are doc drift.

| # | Claim / doc | Reality | Kind | Fix location |
|---|---|---|---|---|
| 1 | `thinking` "runs the agent loop plus a schema-enforced reflection review" (implies robust) | On a rejected non-answer under default `cross_plane`, the compatibility guard **fails open** and the run silently drops to a **non-reasoning CLI fallback** returning a zero-token stub as `response`. See §6. | **Code defect** | `src/utils.py:12469-12474` (guard) + `7606-7643` (classifier) + `src/tools/chats.py:190` (`response` never blanked on `error`) |
| 2 | streaming-json is parsed incrementally (implied complete) | Parser exits on EOF with **no `end`-event check** (`if not line: break`); a preamble line on a truncated-but-clean stream becomes the whole answer. | **Latent code gap** | `src/utils.py:10495-10531` |
| 3 | "`grok --check` for plane readiness" | Readiness actually runs `grok models` and greps `"logged in with grok.com"`; `--check` is never invoked (`docs/okf/faq.md:283` even says `--check` is *not* a health probe). | Doc contradicts code | `CLAUDE.md:38`, `.agents/AGENTS.md:45` |
| 4 | "deterministic `-s` native session ids" | Code emits `--session-id`/`--resume`/`--fork-session` and the id is a **stored random UUID**, not deterministic (determinism only in fork-on-collision). `AGENTS.md` is already correct; `CLAUDE.md` is not. | Self-inconsistent docs | `CLAUDE.md:37-38` |
| 5 | `research` "pins the planning route" | `classify_route` returns the **research** class and pins the multi-agent slug, not planning. | Doc inaccurate | `src/http_server.py:1970-1971` |
| 6 | `research` "multi-agent fan-out" (implies local orchestration) | `agent_count` is a single param handed to xAI's server-side model; there is **no local fan-out/merge/vote** code. | Doc overstates | `src/http_server.py:1970`; clarify wording |
| 7 | schema "`user_version` currently 14" vs "schema v17" | Two architecture.md passages disagree on the DDL version. | Internal doc inconsistency | `architecture.md:258` vs `299` (verify actual DDL) |
| 8 | "`agent` is the only public entry point" vs ~20-tool legacy directory | `architecture.md:322-339` lists ~20 tools; reconcile which are actually registered on the public MCP surface vs contributor/internal. | Needs reconciliation | `architecture.md:322-339` |

---

## 6. Live routing bug — full chain (evidenced by this audit's own consult)

**Symptom (observed live):** two `mcp__unigrok__agent` calls returned only a
status preamble. `thinking` mode: `response = "I'll ground this in…"`,
`finish_reason="fallback"`, `failure.error = "Thinking route returned a rejected
non-answer completion."`. Retried `reasoning` mode resolved to CLI fast and
returned `response = "Digging into the real gaps…"`, `finish_reason="final_answer"`,
`tokens=0`. Only pinning `plane=api, fallback_policy=same_plane` produced a real
answer.

**Two distinct failure modes, both real:**

**(A) Preamble accepted as a final answer** — `reasoning`→CLI path.
`_is_nonanswer_completion` (`src/utils.py:7606-7643`) rejects only empty
strings, recognized promise-verb prefixes without delivered evidence
(`_PROMISE_ONLY_PREFIX_RE`), and bulleted plans. A non-empty lead-in that isn't
matched as a promise and carries no completion evidence falls through to
`return False` at `:7643` → `_completion_finish_reason` leaves
`finish_reason="final_answer"` (`:7649-7653`). `"I'll ground this…"` matched the
promise regex and was correctly rejected; `"Digging into…"` did not match and
slipped through. **Root gap:** no minimum-substance floor, and the promise-verb
list is incomplete.

**(B) Thinking non-answer → non-reasoning CLI stub** — `thinking` path.
1. Thinking AgentLoop emits preamble-only content (`src/utils.py:8812` / `8888-8895`).
2. `finish_reason` set to `"error"` by the classifier (`:7646-7653`).
3. Raised at `src/utils.py:11827-11833` (verified verbatim) under `cross_plane`.
4. Caught at `:11861`, which **sets `cross_plane_intelligence_error`**.
5. The CLI-compatibility guard at `src/utils.py:12469-12474` (verified verbatim)
   is `not ((thinking_mode and cross_plane_intelligence_error is None) or …)`.
   Because step 4 set the error, `thinking_mode and (error is None)` is
   `True and False = False` → the guard **fails open** → `cli_execution_compatible = True`.
6. Execution drops into the CLI fallback (`:12559+`) run with `thinking_mode=False`
   — a non-reasoning CLI agent, no reflection — returning `tokens=0` and its
   stub `layer.generation` verbatim as `response` (`src/tools/chats.py:190`).

**Surgical fixes (Grok plane-truth lane to implement, Codex to land):**
- Guard `src/utils.py:12469`: a `thinking` non-answer should not become
  CLI-eligible merely because the error flag was set — the guard's intent is the
  opposite. Gate on `thinking_mode` directly, independent of
  `cross_plane_intelligence_error`.
- Classifier `src/utils.py:7606`: add a substance/length floor and treat a
  CLI reply whose only content is a lead-in line as a non-answer.
- Response contract `src/tools/chats.py:190`: do not surface `layer.generation`
  as `response` when `finish_reason=="error"`.
- Parser `src/utils.py:10495`: require a seen `end` event (or mark partial)
  before treating accumulated `parts` as complete.

---

## 7. Docs' honest self-declared gaps (already documented — NOT deltas)

These are correctly labeled in the docs and should not be re-flagged:

- `grok agent stdio|serve|leader` and `--best-of-n` unintegrated (`CLAUDE.md:41-42`, `AGENTS.md:48-49`).
- CLI adapter lacks the full API ReAct local-tool loop (`CLAUDE.md:34`, `AGENTS.md:40`).
- Multi-provider (OpenAI/Anthropic/Google) lanes landed but **not** runtime-wired; Grok is sole finalizer (`architecture.md:33-47`).
- No Postgres backend ships — only the protocol seam (`architecture.md:281`).
- AKE / provider-harvest Collections outbox is inert, no routing authority (`architecture.md:299`).
- Authority-inversion / Grok-first runtime is a phased **target**, not the current router (`architecture.md:18-22`).
- Live eval tier is never wired into CI (`architecture.md:410`).
- `/readyz` alone is **not** a usable-plane gate — use `grok_mcp_status`/`discover_self` (`AGENTS.md:149`).
- Forge/Swarm/land are contributor-only, never public product (`skills/using-unigrok/SKILL.md`, `architecture.md:264`).
- CLI provider cost/quota unavailable — report local estimated counts, not invented subscription cost (`architecture.md:289-295`).
- Stable service cannot see the IDE workspace — context only via `workspace_context` (`docs/ide-setup.md:60-62`).

---

## 8. Recommended next actions, by lane

- **Claude Code (now):** land deltas #3–#8 as a doc/skill drift-closure PR
  (CLAUDE.md/AGENTS/skill/architecture.md), and ship this matrix as the shared
  truth artifact. Design the runtime fix spec for #1–#2.
- **Grok (plane-truth lane):** implement the four surgical runtime fixes in §6.
- **Codex (supervisor):** review exact head and land.
- **NOT Claude's:** the runtime `_call_plane`/fallback *implementation*,
  productizing `/ui/`, or expanding private intelligence IP into the public
  adapter.
