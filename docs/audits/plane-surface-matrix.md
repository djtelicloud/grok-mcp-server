# Plane & Surface Matrix — dual-plane routing & CLI surfaces

**Point-in-time audit snapshot. Not a Live product claim.**

- Audit HEAD: `e2c06347eceda1c739863344863e8da39f875cbb` (main)
- Date: 2026-07-15
- Method: read-only fan-out (5 parallel Explore passes) over `src/`, docs, and
  skills, every verdict cited to `file:line`. §5–§6 were **re-verified against
  the test suite** after the first draft, which downgraded two items originally
  called "runtime defects" to intended/tested behavior (see §6). The actionable
  output is the verified **doc drift** in §5 items 3–8, fixed in this PR.
- Author lane: Claude Code (map / contract / doc-truth). This audit produced
  **no runtime fix** — re-verification found no clean defect to patch.

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
| Cross-plane recovery gate (thinking/research non-answer → CLI) | Live — **intended** cross-once recovery, tested | `src/utils.py:12469`; test `tests/test_utils.py:2695` |
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

Items 3–8 are **verified doc drift** and are fixed in this PR. Items 1–2 were
initially flagged as "live defects" but **re-verification against the test suite
downgraded them** — see §6 for the correction. Only 3–8 are actionable drift.

| # | Claim / doc | Reality (re-verified) | Kind | Location |
|---|---|---|---|---|
| 1 | `thinking` non-answer → CLI "stub" (originally called a defect) | **NOT a defect.** Under `cross_plane` a rejected thinking/agentic non-answer is *designed* to cross once to CLI and return `finish_reason="fallback"` — asserted by `tests/test_utils.py:2695`. The guard at `12469` *implements* that recovery. Residual: the CLI recovery output can be low-quality; that is a **product tradeoff** (return something vs. nothing), not a bug. See §6. | Not a defect — corrected | `src/utils.py:12469`; `tests/test_utils.py:2695` |
| 2 | `_is_nonanswer_completion` "has no substance floor" | Overstated. The classifier is **deliberately prompt-aware** with ~40 contract cases (`tests/test_utils.py:3332-3432`): promise+evidence accepted, unsolicited plans rejected, advice-prompt bullets accepted. The one narrow real gap is missing gerund openers (`exploring`/`digging into`) — but the observed string also trips a false evidence match (`"answer is grounded…"`), so it is not a clean fix. **Tuning tradeoff, not a bug.** | Overstated — corrected | `src/utils.py:7481-7643`; `tests/test_utils.py:3332` |
| 3 | "`grok --check` for plane readiness" | Readiness actually runs `grok models` and greps `"logged in with grok.com"`; `--check` is never invoked (`docs/okf/faq.md:283` even says `--check` is *not* a health probe). | Doc contradicts code | `CLAUDE.md:38`, `.agents/AGENTS.md:45` |
| 4 | "deterministic `-s` native session ids" | Code emits `--session-id`/`--resume`/`--fork-session` and the id is a **stored random UUID**, not deterministic (determinism only in fork-on-collision). `AGENTS.md` is already correct; `CLAUDE.md` is not. | Self-inconsistent docs | `CLAUDE.md:37-38` |
| 5 | `research` "pins the planning route" | `classify_route` returns the **research** class and pins the multi-agent slug, not planning. | Doc inaccurate | `src/http_server.py:1970-1971` |
| 6 | `research` "multi-agent fan-out" (implies local orchestration) | `agent_count` is a single param handed to xAI's server-side model; there is **no local fan-out/merge/vote** code. | Doc overstates | `src/http_server.py:1970`; clarify wording |
| 7 | schema "`user_version` currently 14" vs "schema v17" | Two architecture.md passages disagree on the DDL version. | Internal doc inconsistency | `architecture.md:258` vs `299` (verify actual DDL) |
| 8 | "`agent` is the only public entry point" vs ~20-tool legacy directory | `architecture.md:322-339` lists ~20 tools; reconcile which are actually registered on the public MCP surface vs contributor/internal. | Needs reconciliation | `architecture.md:322-339` |

---

## 6. The preamble observation — and why it is NOT a routing bug (corrected)

**This section corrects an over-claim in the first draft of this matrix.** The
first draft called the behavior below a "live routing defect" with "surgical
fixes for the Grok lane." Re-verification against the test suite shows the
behavior is **intended and tested**. The honest conclusion is recorded here.

**Symptom (observed live):** two `mcp__unigrok__agent` calls returned only a
status preamble. `thinking` mode: `finish_reason="fallback"`, and the reflection
gate raised `"Thinking route returned a rejected non-answer completion."`.
Retried on the CLI-recovering path it returned `"Digging into the real gaps…"`.
Pinning `plane=api, fallback_policy=same_plane` produced a full answer.

**Why this is intended, not a defect:**

1. `tests/test_utils.py:2695` (`test_thinking_nonanswer_crosses_once_without_api_fast_call`)
   **asserts** that under `cross_plane`, a rejected thinking non-answer crosses
   **once** to the CLI plane and returns `finish_reason="fallback"` with
   `attempts == ["API", "CLI"]`. The guard at `src/utils.py:12469` and the raise
   at `11827` are the *mechanism* for that recovery — working as designed.
2. Under `same_plane` the cross is correctly blocked (`src/utils.py:12418`);
   that is why the `api`+`same_plane` pin forced the API side to actually answer.
3. `_is_nonanswer_completion` (`src/utils.py:7606-7643`) is **deliberately
   prompt-aware** and has ~40 passing contract cases
   (`tests/test_utils.py:3332-3432`). It accepts "promise + delivered evidence",
   rejects *unsolicited* plans, and accepts bulleted answers when the prompt asks
   "what should I do". My earlier probe ran it with an empty prompt, which is not
   how production calls it.

**What is actually true (a product tradeoff, not a bug):** the default
`cross_plane` policy recovers a rejected thinking/agentic non-answer by handing
off to the CLI plane, and the CLI recovery can produce a lower-quality
completion (no reflection loop). The system chose "return a CLI recovery" over
"return nothing." Whether that default is right is a **product judgment for the
maintainers**, not a defect to patch. A caller who wants the API reflection loop
to deliver-or-fail should pin `fallback_policy=same_plane`.

**Narrow, real, low-value residue (not fixed here):** the gerund promise-openers
list at `src/utils.py:7489` misses `exploring`/`digging into`/`looking into`.
Adding them is low-risk but does not even fix the observed string (it trips a
false evidence match on `"…the answer is grounded…"`), so it is left as a note,
not a change. No runtime fix is warranted by this audit.

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

- **Done in this PR:** deltas #3–#8 (the verified doc drift) corrected in
  `CLAUDE.md` / `AGENTS.md` / `architecture.md` / the `agent` docstring.
- **No runtime change:** §5 items 1–2 were re-verified and found to be intended,
  tested behavior (§6) — this audit warrants no code fix.
- **Open, deferred:** delta #8 (the ~20-tool legacy directory vs "`agent` is the
  only public entry") needs a registration audit of the actual public MCP
  surface before the `architecture.md:322-339` text can be corrected honestly.
