# Needle Lab Evidence Bundle (research_dev — QUARANTINED)

Sanitized artifacts backing `docs/reports/needle-discovery.md` (PR #64).

**Quarantine status:** everything here is `research_dev`. These datasets and
any checkpoints derived from them may inspire scenarios but MUST NEVER become
verified training truth, validation data, or sealed evaluation content.
Checkpoints (pickle) are deliberately NOT committed (ACE risk, footgun F6);
they remain in the ephemeral session lab only.

**Pins:** upstream needle commit `ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2`;
base weights `needle.pkl` sha256 prefix `40a32e91d1d4197b` (HF
Cactus-Compute/needle); python 3.12.13, jax 0.10.2 (CPU), flax 0.12.7;
Apple M3 Max, macOS Darwin 25.5.0. Dataset hashes in `data/manifest.json`
and `data/arm_candidates.json`; sealed→adaptive-dev set sha `52276cf8e483738b`.

**Contents:** generator/driver/probe scripts (`*.py`, `*.sh`), all JSONL
datasets + manifests, trimmed run logs. Known defects are documented in the
report (split leakage in abstention/next_step; combined-split contamination;
the 40-item OOD set became adaptive development data after iteration 1).
