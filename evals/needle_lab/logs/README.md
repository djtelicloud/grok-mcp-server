# Trimmed evidence logs (research_dev — QUARANTINED)

Recovered, trimmed run logs backing `docs/reports/needle-discovery.md`.
The original lab wrote 23 logs (2.8 MB); these copies are the sanitized,
trimmed evidence set. The first amend pass intended to commit them but the
repo-wide `.gitignore` rules (`*.log`, `logs/`) silently excluded the
directory — fixed with explicit negation rules in the root `.gitignore`.

**Trim rule for `ft-*.log`:** progress-bar lines removed; config header kept;
everything from `Training complete.` to EOF kept (`FINETUNED_EVAL` + wall
time always present). Small logs are verbatim. Every file begins with a
`# LIFTED`/`# TRIMMED` provenance line. Nothing was silently redacted; a
strict secret scan (key shapes, bearer tokens, api_key values) found nothing.
Absolute paths from the ephemeral session lab remain in the text — they are
historical and resolve nowhere else; treat them as opaque run identifiers.

## Report citation → log file

| Report citation | Log file | Status |
|---|---|---|
| F1 "revive log v1 (loss frozen at ln 32)" | `revive-v1-frozen-saddle.log` | recovered (loss constant 3.4688 ≈ ln 32 = 3.4657, 300 steps) |
| F2 "revive v2 log (model lobotomized)" | `revive-v2-gradient-leak-lobotomy.log` | recovered (gibberish generation verbatim) |
| F2 "v3 (clean)" / headline revival | `revive-v3-clean-revival.log` | recovered (tool 100%, mem 20%, 399 steps / 182 s) |
| headline memory 60% (50 queries, 12 pairs/card) | `revive-v4-memory-12pairs-60pct.log` | recovered (mem top-1 60%, tool 100%) |
| F3 "matrix run 1 failure" (mp.Pool `__main__` guard) | — | **NOT RECOVERABLE** — run 2's `> logs/…` redirects overwrote it; no saved log contains the RuntimeError |
| matrix wave schedule (run 2) | `matrix-run2-success-schedule.log` | recovered (shows extraction + abstention started concurrently 10:37:53) |
| F7 "observation_typing run 1: 782 s, exact_match 0.0" | — | **NOT RECOVERABLE** — overwritten by run 2's redirect; figures are from the session record |
| observation_typing run 2 (matrix row) | `ft-observation-typing-RUN2-batch8-1338s.log` | recovered (batch clamped 32→8) |
| Apple-silicon verdict (forward 7.5 s OK, backward `null operand`) | `metal-probe-FAILED-moduleerror.log` | **CLAIM NOT LOG-BACKED** — the only saved metal log is an earlier probe that failed on `ModuleNotFoundError: datasets`; the successful-forward/failed-backward run was never logged to a file |
| fine-tune matrix rows | `ft-route-selection-66steps-1000s.log`, `ft-recovery-selection-50steps-960s.log`, `ft-memory-rerank-240steps-2823s.log`, `ft-tool-selection-481steps-5762s.log`, `ft-extraction-480steps-6983s.log`, `ft-abstention-KILLED-epoch17.log`, `ft-next-step-30epochs-7480s.log`, `ft-combined-6epochs-2134s.log` | recovered; all steps/wall figures match the table |
| adaptive-recipe arm runs | `ft-arm-B-hardneg-80epochs-2632s.log`, `ft-arm-C-metamorphic-12epochs-545s.log`, `ft-arm-D-balanced-12epochs-534s.log`, `ft-arm-E-worstcell-12epochs-493s.log` | recovered; note arm E's own 10-row trainer split scored 8/10 (the arm table's in-template 100% comes from the eval_arms set, `data/arm_results.json`) |
| arm eval scores (adaptive dev / secondary OOD) | — | no eval_arms log was written; numbers live in `data/arm_results.json` and match the report table exactly |
| ICL probe table | `icl-probe-verdict.log` | recovered (VERDICT JSON; all 12 conditions match the table) |
| drift probe table | `drift-probe-verdict.log` | recovered (10/10 renamed key, 6/6 new tool, hash-guard ABSTAIN) |
| OOD 45.8% exact / 0.48 s/call | `eval-route-ood-45p8-exact.log` | recovered (exact 0.4583; sampled failures all value-tier) |
| self-loop 5/7 | — | **NOT RECOVERABLE** — `selfloop.py` printed its VERDICT to stdout only; no log file was ever written |
| TF-IDF baseline vs revived head | `tfidf-baseline.log` | regenerated deterministically from committed data (`../tfidf_baseline.py`) |
| combined-split contamination counts | `combined-contamination-check.log` | regenerated deterministically from committed data (`../check_combined_contamination.py`) |

## The extraction row's "~116 min" wall-clock

`ft-extraction-480steps-6983s.log` is a single uninterrupted 80-epoch run,
Total steps 480, wall 6,983 s = 116.4 min. It started in matrix wave 3
concurrently with the abstention lane (killed mid-epoch 17 when leakage was
found). Compare tool_selection: 481 steps in 5,762 s (~96 min) — nearly the
same step count, +20 min wall. The report's footnote marks this
concurrent-lane wall-clock inflation.
