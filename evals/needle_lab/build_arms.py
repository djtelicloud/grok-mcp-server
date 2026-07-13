"""Build the 4-arm dry-run datasets for the route confusion cell.

Arm A: original corpus (control — its checkpoint already exists)
Arm B: original + 40 Grok hard negatives (style/class boundary contrast)
Arm C: original + 30 Grok metamorphic rephrasings (label-preserving)
Arm D: B + C with balanced resampling (downsample coding to ~parity)

Candidate records (base hash, dataset manifest, recipe) written per arm —
the Swarm `training_experiment` record shape, file-only.
"""
import hashlib, json, os, random

LAB = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(LAB, "data")
rng = random.Random(11)

ROUTE_TOOL_JSON = json.loads(next(open(os.path.join(D, "route_selection.jsonl"))))["tools"]

def to_example(item):
    return {"query": item["query"], "tools": ROUTE_TOOL_JSON,
            "answers": json.dumps([{"name": "route", "arguments":
                {"mode": item["mode"], "route_class": item["route_class"]}}],
                separators=(",", ":"))}

base = [json.loads(l) for l in open(os.path.join(D, "route_selection.jsonl"))]
hard = [to_example(x) for x in json.load(open(os.path.join(D, "hard_negatives.json")))]
meta = [to_example(x) for x in json.load(open(os.path.join(D, "metamorphic.json")))]

def classcount(ex):
    from collections import Counter
    return dict(Counter(json.loads(e["answers"])[0]["arguments"]["route_class"] for e in ex))

def balance(ex):
    """Downsample majority class to ~1.3x the median class size."""
    from collections import defaultdict
    by = defaultdict(list)
    for e in ex:
        by[json.loads(e["answers"])[0]["arguments"]["route_class"]].append(e)
    sizes = sorted(len(v) for v in by.values())
    cap = int(sizes[len(sizes) // 2] * 1.3)
    out = []
    for cls, items in by.items():
        rng.shuffle(items)
        out.extend(items[:max(cap, sizes[0])])
    rng.shuffle(out)
    return out

ARMS = {
    "arm_B": base + hard,
    "arm_C": base + meta,
    "arm_D": balance(base + hard + meta),
}

ckpt_hash = hashlib.sha256(open(os.path.join(LAB, "checkpoints", "needle.pkl"), "rb").read()).hexdigest()[:16]
records = []
for name, ex in ARMS.items():
    # dedup guard (lesson learned)
    seen, ded = set(), []
    for e in ex:
        k = (e["query"], e["answers"])
        if k not in seen:
            seen.add(k)
            ded.append(e)
    path = os.path.join(D, f"route_{name}.jsonl")
    with open(path, "w") as f:
        for e in ded:
            f.write(json.dumps(e) + "\n")
    dh = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    rec = {"arm": name, "base_checkpoint_hash": ckpt_hash, "dataset_hash": dh,
           "n": len(ded), "dropped_dups": len(ex) - len(ded),
           "class_counts": classcount(ded),
           "recipe": {"arm_B": "base+hard_negatives", "arm_C": "base+metamorphic",
                      "arm_D": "balanced(base+hard+meta)"}[name],
           "seed": 42}
    records.append(rec)
    print(json.dumps(rec))
json.dump(records, open(os.path.join(D, "arm_candidates.json"), "w"), indent=1)
print("arm A = existing route_selection checkpoint (control, no retrain)")
