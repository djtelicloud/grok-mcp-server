"""Rebuild data/manifest.json with full lineage for every committed data file.

Canonical manifest generator for the evidence bundle: repo-relative paths,
sha256 of committed bytes, row/item counts, the original tool-catalog hashes
for the 7 generated families (reproducible from gen_datasets.py), and the
generator/provenance of each file. Supersedes the 7-family manifest that
gen_datasets.py writes during generation (which recorded absolute session
paths — the defect this script fixes). Deterministic: re-running it on the
committed data must be a no-op.

Run from evals/needle_lab/:  python rebuild_manifest.py
"""

import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Tool-catalog hashes recorded by gen_datasets.py at generation time
# (sha256(repr(sorted (tool-name, sorted-param-names)))[:16] — a schema
# hash, NOT a content hash; verified reproducible from the committed
# generator).
CATALOG_HASH = {
    "route_selection": "a8b0a066176fb67c",
    "observation_typing": "20e1dc4a5ad88278",
    "recovery_selection": "b36d2f8ac7261066",
    "memory_rerank": "5e9d51925807abc6",
    "tool_selection": "73b8f33f05c642af",
    "extraction": "6c7c6cdd536e0dcf",
    "abstention": "f30f67f98c45bce0",
}

GENERATOR = {
    "route_selection.jsonl": "gen_datasets.py (seed 42)",
    "observation_typing.jsonl": "gen_datasets.py (seed 42)",
    "recovery_selection.jsonl": "gen_datasets.py (seed 42)",
    "memory_rerank.jsonl": "gen_datasets.py (seed 42)",
    "tool_selection.jsonl": "gen_datasets.py (seed 42)",
    "extraction.jsonl": "gen_datasets.py (seed 42)",
    "abstention.jsonl": "gen_datasets.py (seed 42); family VOIDED (20/40 test-train leakage)",
    "next_step.jsonl": (
        "gen_nextstep.py (seed 7) — HISTORICAL BYTES CANONICAL: the original "
        "run used Python's per-process-randomized hash() for failure-branch "
        "selection (fixed post-audit to a sha256 digest), so the committed "
        "file is pinned by sha256, not regenerable byte-exactly"
    ),
    "combined.jsonl": "run_matrix.sh: concat of the 7 non-abstention families, in FAMILIES order",
    "route_selection_ood.jsonl": "grok_route_test.py transform of a one-shot grok-4.5 generation (raw input not committed)",
    "sealed_raw.json": "one-shot grok-4.5 generation via UniGrok MCP (external; unreproducible)",
    "route_sealed.jsonl": "grok_route_test.py-style transform of sealed_raw.json (verified byte-exact)",
    "hard_negatives.json": "one-shot grok-4.5 generation via UniGrok MCP (external; unreproducible)",
    "metamorphic.json": "one-shot grok-4.5 generation via UniGrok MCP (external; unreproducible)",
    "vision_research_delta.json": "one-shot grok-4.5 generation via UniGrok MCP (external; unreproducible)",
    "memory_pairs.json": "one-shot grok-4.5 generation via UniGrok MCP (external; unreproducible)",
    "route_arm_B.jsonl": "build_arms.py (route_selection train + hard_negatives)",
    "route_arm_C.jsonl": "build_arms.py (route_selection train + metamorphic)",
    "route_arm_D.jsonl": "build_arms.py (balanced resample of base+hard+meta)",
    "route_arm_E.jsonl": "route_arm_B.jsonl + grok_route_test-style transform of vision_research_delta.json (verified byte-exact)",
    "arm_candidates.json": "build_arms.py candidate records (arm E record reconstructed post-audit from committed bytes)",
    "arm_results.json": "eval_arms.py output (legacy keys: 'sealed'/'sealed_recalls' = the adaptive OOD dev set)",
    "manifest.json": "rebuild_manifest.py (this script)",
}

DERIVED_FROM = {
    "combined.jsonl": [
        "route_selection.jsonl", "observation_typing.jsonl",
        "recovery_selection.jsonl", "memory_rerank.jsonl",
        "tool_selection.jsonl", "extraction.jsonl", "next_step.jsonl",
    ],
    "route_sealed.jsonl": ["sealed_raw.json"],
    "route_selection_ood.jsonl": [],
    "route_arm_B.jsonl": ["route_selection.jsonl", "hard_negatives.json"],
    "route_arm_C.jsonl": ["route_selection.jsonl", "metamorphic.json"],
    "route_arm_D.jsonl": ["route_selection.jsonl", "hard_negatives.json", "metamorphic.json"],
    "route_arm_E.jsonl": ["route_arm_B.jsonl", "vision_research_delta.json"],
}


def count_rows(path):
    if path.endswith(".jsonl"):
        with open(path) as fh:
            return sum(1 for _ in fh)
    payload = json.load(open(path))
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return sum(len(v) if isinstance(v, list) else 1 for v in payload.values())
    return 1


def main():
    entries = []
    for name in sorted(os.listdir(DATA)):
        if name == "manifest.json" or name.startswith("."):
            continue
        path = os.path.join(DATA, name)
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        entry = {
            "path": f"evals/needle_lab/data/{name}",
            "sha256": digest,
            "n": count_rows(path),
            "generator": GENERATOR.get(name, "UNKNOWN — add lineage before committing"),
        }
        family = name.removesuffix(".jsonl")
        if family in CATALOG_HASH:
            entry["catalog_hash"] = CATALOG_HASH[family]
        if name in DERIVED_FROM:
            entry["derived_from"] = [
                f"evals/needle_lab/data/{d}" for d in DERIVED_FROM[name]
            ]
        entries.append(entry)

    out = os.path.join(DATA, "manifest.json")
    with open(out, "w") as fh:
        json.dump(entries, fh, indent=1)
        fh.write("\n")
    print(f"wrote {out}: {len(entries)} entries")
    for e in entries:
        print(f"  {e['path']}  sha256[:16]={e['sha256'][:16]}  n={e['n']}")


if __name__ == "__main__":
    main()
