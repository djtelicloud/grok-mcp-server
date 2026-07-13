# Needle Lab Evidence Bundle (research_dev — QUARANTINED)

Sanitized artifacts backing `docs/reports/needle-discovery.md` (PR #64).

**Quarantine status:** everything here is `research_dev`. These datasets and
any checkpoints derived from them may inspire scenarios but MUST NEVER become
verified training truth, validation data, or sealed evaluation content.
Checkpoints (pickle) are deliberately NOT committed (ACE risk, footgun F6);
they remain in the ephemeral session lab only. The bundle is also excluded
from the published sdist (`pyproject.toml` hatch exclude).

**Pins:** upstream needle commit `ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2`;
base weights `needle.pkl` sha256 prefix `40a32e91d1d4197b` (HF
Cactus-Compute/needle); python 3.12.13, jax 0.10.2 (CPU), flax 0.12.7;
Apple M3 Max, macOS Darwin 25.5.0.

**Hashes & lineage:** `data/manifest.json` (written by `rebuild_manifest.py`)
covers EVERY committed data file: repo-relative path, sha256 of committed
bytes, row count, and generator provenance. The 7 generated families also
carry `catalog_hash` — a hash of the tool schema (per `gen_datasets.py`),
NOT a content hash. Retrain-arm content hashes (arms B–E) are in
`data/arm_candidates.json`; arm E's record was reconstructed post-audit
after byte-exact verification (`route_arm_E.jsonl` = `route_arm_B.jsonl` +
the `grok_route_test.py` transform of `vision_research_delta.json`).
Adaptive-dev set pin: sha256[:16] `52276cf8e483738b` (`route_sealed.jsonl`).

**Contents:** generator/driver/probe scripts (`*.py`, `*.sh`), all JSONL/JSON
datasets + manifests, trimmed run logs (`logs/`, with a citation map in
`logs/README.md` — committed via explicit `.gitignore` negation of the
global `logs/` rule), and two deterministic audit-verification scripts
(`tfidf_baseline.py`, `check_combined_contamination.py`) that reproduce the
report's TF-IDF-baseline and combined-split-contamination numbers from
committed data alone. Known defects are documented in the report (split
leakage in abstention/next_step; combined-split contamination; the 40-item
OOD set became adaptive development data after iteration 1). Two run-1 logs
(F3 matrix crash, F7 observation_typing NaN run) were overwritten in-session
and are not recoverable; the self-loop and decisive metal-probe runs were
never logged to a file (see `logs/README.md`).

## Legacy naming (sealed → adaptive-dev)

The 40-query set was frozen with a zero-overlap guard before iteration 1 but
became an **adaptive OOD development set** from iteration 2 onward (its
iteration-1 results were used to design arm E). File names
(`route_sealed.jsonl`, `sealed_raw.json`) and the `sealed`/`sealed_recalls`
keys inside `arm_results.json` are the historical artifact names, kept
unmodified to preserve the pinned hashes; read them as "adaptive OOD dev"
everywhere. Renaming committed artifacts post-hoc would falsify the record.

## Reproducing / re-running

- **Environment:** clone needle at the pinned commit; `uv venv --python 3.12
  .venv && uv pip install -e <needle checkout>` inside `evals/needle_lab/`.
  `run_matrix.sh` expects `.venv/bin/activate` here.
- **Run order:** `baseline.py` first — it downloads `needle.pkl` from HF
  (Cactus-Compute/needle) into `checkpoints/` (uncommitted); `ft.py` and
  `metal_probe.sh` load that file. `metal_probe.sh` additionally expects the
  needle source checkout.
- **Deterministic from committed data alone** (no model, no network):
  `python tfidf_baseline.py`, `python check_combined_contamination.py`,
  `python rebuild_manifest.py` (must be a no-op on the committed tree).
- **Generators:** `gen_datasets.py` reproduces the 7 family JSONLs
  byte-exactly (seed 42). `gen_nextstep.py` originally used Python's
  randomized `hash()` for failure-branch choice — fixed post-audit; the
  committed `next_step.jsonl` bytes (sha256[:16] `8bb294f2c14bb195`) are
  canonical. `grok_route_test.py <raw.json> [out.jsonl]` re-derives
  `route_sealed.jsonl` from `sealed_raw.json` byte-exactly; the raw input
  behind `route_selection_ood.jsonl` was a one-shot grok-4.5 generation and
  is not committed. `hard_negatives.json`, `metamorphic.json`,
  `vision_research_delta.json`, `memory_pairs.json`, `sealed_raw.json` are
  one-shot grok-4.5 generations (externally unreproducible; pinned by
  sha256 in the manifest).
