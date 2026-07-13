"""Dry-run verdict: evaluate all 4 arms with Pareto objectives + forgetting suite.

Usage: python eval_arms.py  (expects checkpoints for arms + sealed set present)
Objectives per arm: sealed OOD exact, dev OOD exact, in-template exact,
worst-class recall (sealed), forgetting (base tool-calling retained), train wall.
"""
import glob, json, os

LAB = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(LAB, "data")

from needle.model.run import load_checkpoint, generate_batch, generate
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer
from needle.training.finetune import _per_tool_split

tok = get_tokenizer()

def load(path):
    p, c = load_checkpoint(path)
    return SimpleAttentionNetwork(c), p

def score_set(model, params, examples):
    preds = []
    for i in range(0, len(examples), 24):
        chunk = examples[i:i + 24]
        preds.extend(generate_batch(model, params, tok, [e["query"] for e in chunk],
                                    [e["tools"] for e in chunk], max_gen_len=96, max_enc_len=1024))
    n = exact = 0
    per_class = {}
    for e, pr in zip(examples, preds):
        g = json.loads(e["answers"])[0]
        cls = g["arguments"]["route_class"]
        pc = per_class.setdefault(cls, [0, 0])
        pc[1] += 1
        n += 1
        try:
            c = json.loads(pr.strip())
            c = c[0] if isinstance(c, list) and c else c
            if c.get("name") == g["name"] and c.get("arguments") == g["arguments"]:
                exact += 1
                pc[0] += 1
        except Exception:
            pass
    recalls = {k: round(v[0] / v[1], 3) for k, v in per_class.items()}
    return round(exact / max(n, 1), 4), recalls

FORGET_CASES = [
    ("What's the weather in San Francisco?",
     '[{"name":"get_weather","description":"Get current weather for a city.","parameters":{"location":{"type":"string","description":"City name.","required":true}}}]',
     {"name": "get_weather", "arguments": {"location": "San Francisco"}}),
    ("Send an email to sam@x.dev saying build is green",
     '[{"name":"send_email","description":"Send an email to a recipient.","parameters":{"to":{"type":"string","description":"Recipient email.","required":true},"body":{"type":"string","description":"Email body.","required":true}}}]',
     {"name": "send_email", "arguments": {"to": "sam@x.dev", "body": "build is green"}}),
    ("Turn off the kitchen lights",
     '[{"name":"toggle_lights","description":"Toggle smart lights on or off.","parameters":{"room":{"type":"string","description":"Room name.","required":true},"state":{"type":"string","description":"on or off.","required":true}}}]',
     {"name": "toggle_lights", "arguments": {"room": "kitchen", "state": "off"}}),
]

def forgetting(model, params):
    ok = 0
    for q, tools, want in FORGET_CASES:
        try:
            out = json.loads(generate(model, params, tok, q, tools=tools, stream=False))
            c = out[0] if isinstance(out, list) else out
            if c.get("name") == want["name"] and c.get("arguments") == want["arguments"]:
                ok += 1
        except Exception:
            pass
    return f"{ok}/{len(FORGET_CASES)}"

sealed = [json.loads(l) for l in open(os.path.join(D, "route_sealed.jsonl"))]
dev_ood = [json.loads(l) for l in open(os.path.join(D, "route_selection_ood.jsonl"))]
_, _, intmpl = _per_tool_split([json.loads(l) for l in open(os.path.join(D, "route_selection.jsonl"))])

def ck(pattern):
    hits = sorted(glob.glob(os.path.join(LAB, "checkpoints", pattern, "*_best.pkl")))
    return hits[-1] if hits else None

ARMS = {"A_control": ck("route_selection"), "B_hardneg": ck("route_arm_B"),
        "C_metamorphic": ck("route_arm_C"), "D_balanced": ck("route_arm_D"),
        "E_worstcell": ck("route_arm_E")}

print(f"{'arm':<14} {'sealed':>7} {'devOOD':>7} {'intmpl':>7} {'worst-class':>16} {'forget':>7}")
rows = []
for name, path in ARMS.items():
    if not path:
        print(f"{name:<14} (no checkpoint)")
        continue
    model, params = load(path)
    s, s_rec = score_set(model, params, sealed)
    d, _ = score_set(model, params, dev_ood)
    t, _ = score_set(model, params, intmpl)
    worst = min(s_rec.items(), key=lambda kv: kv[1])
    fg = forgetting(model, params)
    rows.append({"arm": name, "sealed": s, "dev_ood": d, "in_template": t,
                 "sealed_recalls": s_rec, "worst_class": worst, "forgetting": fg,
                 "checkpoint": os.path.basename(path)})
    print(f"{name:<14} {s:>7.1%} {d:>7.1%} {t:>7.1%} {str(worst):>16} {fg:>7}")
json.dump(rows, open(os.path.join(D, "arm_results.json"), "w"), indent=1)
print("\nVERDICT:" + json.dumps([{k: r[k] for k in ('arm', 'sealed', 'dev_ood', 'forgetting')} for r in rows]))
