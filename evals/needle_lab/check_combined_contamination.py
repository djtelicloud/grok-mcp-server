"""Verify the combined-run split contamination (audit check).

The combined interference run concatenated the 7 family JSONLs and let the
trainer re-split the combined file, selecting different test rows than the
per-family splits. This script reproduces the contamination counts in the
report's "Interference test — RESULT INVALID" section: for each family, how
many of ITS held-out test rows sit inside the combined run's TRAINING data.

`_per_tool_split` is vendored verbatim from needle/training/finetune.py:162
at upstream commit ffb1c5144c5a16cb8ec650dbc8a6f6fd3854f8f2 (seed 42, the
exact split every lab run used). Deterministic; committed data only.

Run from evals/needle_lab/:  python check_combined_contamination.py
"""

import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FAMILIES = [
    "route_selection",
    "observation_typing",
    "recovery_selection",
    "memory_rerank",
    "tool_selection",
    "extraction",
    "next_step",
]


def _per_tool_split(examples, val_per_tool=10, test_per_tool=10, seed=42):
    # vendored verbatim from needle/training/finetune.py:162 @ ffb1c5144c5a
    rng = random.Random(seed)
    tool_buckets = {}
    for i, ex in enumerate(examples):
        try:
            calls = json.loads(ex.get("answers", "[]"))
        except (ValueError, TypeError):
            calls = []
        names = [c["name"] for c in calls if isinstance(c, dict) and "name" in c]
        primary = names[0] if names else "__no_tool__"
        tool_buckets.setdefault(primary, []).append(i)
    train_idx, val_idx, test_idx = [], [], []
    for tool, indices in tool_buckets.items():
        rng.shuffle(indices)
        n = len(indices)
        needed = val_per_tool + test_per_tool
        if n < needed:
            if n == 1:
                n_test, n_val, n_train = 1, 0, 0
            elif n == 2:
                n_test, n_val, n_train = 1, 1, 0
            else:
                n_test = max(1, n // 3)
                n_val = max(1, (n - n_test) // 3)
                n_train = n - n_val - n_test
        else:
            n_test = test_per_tool
            n_val = val_per_tool
            n_train = n - n_val - n_test
        test_idx.extend(indices[:n_test])
        val_idx.extend(indices[n_test : n_test + n_val])
        train_idx.extend(indices[n_test + n_val :])
    train = [examples[i] for i in train_idx]
    val = [examples[i] for i in val_idx]
    test = [examples[i] for i in test_idx]
    return train, val, test


def rows(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def key(ex):
    return (ex.get("query"), ex.get("answers"))


def main():
    combined = rows(os.path.join(DATA, "combined.jsonl"))
    combined_train, _, _ = _per_tool_split(combined)
    train_keys = {key(e) for e in combined_train}
    print(f"combined.jsonl: {len(combined)} rows; "
          f"combined-run TRAIN split: {len(combined_train)} rows")
    for family in FAMILIES:
        examples = rows(os.path.join(DATA, f"{family}.jsonl"))
        _, _, family_test = _per_tool_split(examples)
        hits = sum(1 for e in family_test if key(e) in train_keys)
        status = "CLEAN" if hits == 0 else "CONTAMINATED"
        print(f"{family:20s}: {hits}/{len(family_test)} family-test rows "
              f"inside combined TRAIN  [{status}]")


if __name__ == "__main__":
    main()
