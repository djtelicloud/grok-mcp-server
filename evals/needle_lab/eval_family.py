"""Score a checkpoint on a family JSONL: name / arg-key / arg-value tiers.

Usage: python eval_family.py --checkpoint c.pkl --data d.jsonl [--split test|all] [--tag label] [--no-constrained]
Prints one JSON line: METRICS:{...}
"""
import argparse, json, os, time

p = argparse.ArgumentParser()
p.add_argument("--checkpoint", required=True)
p.add_argument("--data", required=True)
p.add_argument("--split", default="test", choices=["test", "all"])
p.add_argument("--tag", default="")
p.add_argument("--no-constrained", action="store_true")
p.add_argument("--max-enc-len", type=int, default=512)
a = p.parse_args()

from needle.model.run import load_checkpoint, generate_batch
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer
from needle.training.finetune import _per_tool_split

examples = [json.loads(l) for l in open(a.data) if l.strip()]
if a.split == "test":
    _, _, examples = _per_tool_split(examples)  # same seed-42 split finetune used

params, config = load_checkpoint(a.checkpoint)
model = SimpleAttentionNetwork(config)
tok = get_tokenizer()

t0 = time.time()
preds = []
B = 32
for i in range(0, len(examples), B):
    chunk = examples[i:i + B]
    preds.extend(generate_batch(model, params, tok,
                                [e["query"] for e in chunk],
                                [e["tools"] for e in chunk],
                                max_gen_len=128, max_enc_len=a.max_enc_len,
                                constrained=not a.no_constrained))
wall = time.time() - t0

n = len(examples)
parse = name = keys = values = exact = 0
per_tool = {}
fails = []
for e, pred in zip(examples, preds):
    gold = json.loads(e["answers"])
    g = gold[0] if gold else {}
    gname, gargs = g.get("name"), g.get("arguments", {})
    pt = per_tool.setdefault(gname, dict(n=0, name=0, keys=0, values=0))
    pt["n"] += 1
    try:
        pc = json.loads(pred.strip())
        if isinstance(pc, dict):
            pc = [pc]
        parse += 1
    except Exception:
        fails.append({"q": e["query"][:90], "pred": pred[:120], "why": "parse"})
        continue
    c = pc[0] if pc else {}
    if c.get("name") == gname:
        name += 1
        pt["name"] += 1
    else:
        fails.append({"q": e["query"][:90], "pred": pred[:120], "why": f"name!={gname}"})
        continue
    cargs = c.get("arguments", {}) or {}
    if set(cargs) == set(gargs):
        keys += 1
        pt["keys"] += 1
    else:
        fails.append({"q": e["query"][:90], "pred": pred[:120], "why": "keys"})
        continue
    def norm(v):
        return sorted(v) if isinstance(v, list) else v
    if all(norm(cargs[k]) == norm(gargs[k]) for k in gargs):
        values += 1
        pt["values"] += 1
    else:
        fails.append({"q": e["query"][:90], "pred": pred[:120], "why": "values"})
    if json.dumps(pc, sort_keys=True) == json.dumps(gold, sort_keys=True):
        exact += 1

m = {
    "tag": a.tag or os.path.basename(a.data),
    "checkpoint": os.path.basename(a.checkpoint),
    "n": n, "parse": round(parse / max(n, 1), 4),
    "name_acc": round(name / max(n, 1), 4),
    "key_acc": round(keys / max(n, 1), 4),
    "value_acc": round(values / max(n, 1), 4),
    "exact": round(exact / max(n, 1), 4),
    "sec_per_call": round(wall / max(n, 1), 3),
    "per_tool": {k: v for k, v in sorted(per_tool.items())},
    "sample_fails": fails[:6],
}
print("METRICS:" + json.dumps(m))
