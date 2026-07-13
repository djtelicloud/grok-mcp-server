"""Phase D probe: does few-shot ICL work at inference? (kill-criterion experiment)

Conditions: k ∈ {0, 4, 8} verified examples injected, two channels:
  tools  — examples smuggled into the tools JSON (UniGrok build_needle_tools_context style)
  query  — examples prepended to the query text
Run on BOTH the base checkpoint (task unseen) and the route fine-tune (task known),
against the Grok-generated OOD set. Flat curves = no ICL.
"""
import argparse, json, os
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--base", default=None, help="base checkpoint")
p.add_argument("--tuned", default=None, help="fine-tuned route checkpoint")
a = p.parse_args()

LAB = os.path.dirname(os.path.abspath(__file__))
BASE = a.base or os.path.join(LAB, "checkpoints", "needle.pkl")

from needle.model.run import load_checkpoint, generate_batch
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer
from needle.training.finetune import _per_tool_split

tok = get_tokenizer()
ood = [json.loads(l) for l in open(os.path.join(LAB, "data", "route_selection_ood.jsonl"))]
train_pool, _, _ = _per_tool_split(
    [json.loads(l) for l in open(os.path.join(LAB, "data", "route_selection.jsonl"))])

rng = np.random.default_rng(3)

def pick_examples(k):
    """k diverse verified examples as {prompt, chosen} records."""
    by_class = {}
    for e in train_pool:
        c = json.loads(e["answers"])[0]["arguments"]["route_class"]
        by_class.setdefault(c, []).append(e)
    out = []
    classes = list(by_class)
    i = 0
    while len(out) < k:
        cls = classes[i % len(classes)]
        e = by_class[cls][int(rng.integers(len(by_class[cls])))]
        out.append({"prompt": e["query"], "chosen": e["answers"]})
        i += 1
    return out

def build_inputs(examples, k, channel):
    exs = pick_examples(k)
    queries, tools_list = [], []
    for e in examples:
        if k == 0:
            queries.append(e["query"])
            tools_list.append(e["tools"])
        elif channel == "tools":
            tools = json.loads(e["tools"])
            tools.append({"name": "examples", "description": json.dumps(exs, separators=(",", ":")),
                          "parameters": {}})
            queries.append(e["query"])
            tools_list.append(json.dumps(tools, separators=(",", ":")))
        else:  # query channel
            shots = "\n".join(f"Q: {x['prompt']}\nA: {x['chosen']}" for x in exs)
            queries.append(f"{shots}\nQ: {e['query']}\nA:")
            tools_list.append(e["tools"])
    return queries, tools_list

def score(model, params, queries, tools_list, golds):
    preds = []
    B = 24
    for i in range(0, len(queries), B):
        preds.extend(generate_batch(model, params, tok, queries[i:i+B], tools_list[i:i+B],
                                    max_gen_len=96, max_enc_len=1024))
    name = args_ok = 0
    for pred, gold in zip(preds, golds):
        g = json.loads(gold)[0]
        try:
            c = json.loads(pred.strip())
            c = c[0] if isinstance(c, list) and c else c
        except Exception:
            continue
        if isinstance(c, dict) and c.get("name") == g["name"]:
            name += 1
            if c.get("arguments") == g["arguments"]:
                args_ok += 1
    n = len(golds)
    return round(name / n, 3), round(args_ok / n, 3)

golds = [e["answers"] for e in ood]
results = {}
ckpts = {"base": BASE}
if a.tuned:
    ckpts["tuned"] = a.tuned
for label, path in ckpts.items():
    params, config = load_checkpoint(path)
    model = SimpleAttentionNetwork(config)
    for channel in ("tools", "query"):
        for k in (0, 4, 8):
            if k == 0 and channel == "query" and (label, "tools", 0) in results:
                results[(label, channel, k)] = results[(label, "tools", 0)]
                continue
            q, t = build_inputs(ood, k, channel)
            nm, ar = score(model, params, q, t, golds)
            results[(label, channel, k)] = (nm, ar)
            print(f"  {label:<6} channel={channel:<6} k={k}: name={nm:.1%} args={ar:.1%}")
print("VERDICT:" + json.dumps({f"{l}/{c}/k{k}": v for (l, c, k), v in results.items()}))
