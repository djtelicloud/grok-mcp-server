"""Phase D probe: catalog drift + the no-sqlite dynamic-TTL flywheel check.

1. Rename a param key in the served catalog (find -> needle_find) — does the
   constrained decoder force structurally-valid-but-stale calls? (silent drift)
2. Add a new tool (format_code) — does the tuned model ever select it? (shadowing)
3. Hash guard: recompute catalog hash and show mismatch vs the manifest hash
   the checkpoint was trained against (loud fallback instead of silent wrong).
"""
import argparse, hashlib, json, os

p = argparse.ArgumentParser()
p.add_argument("--checkpoint", required=True)
a = p.parse_args()

LAB = os.path.dirname(os.path.abspath(__file__))
from needle.model.run import load_checkpoint, generate_batch
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer
from needle.training.finetune import _per_tool_split

params, config = load_checkpoint(a.checkpoint)
model = SimpleAttentionNetwork(config)
tok = get_tokenizer()

examples = [json.loads(l) for l in open(os.path.join(LAB, "data", "tool_selection.jsonl"))]
_, _, test = _per_tool_split(examples)
edit_cases = [e for e in test if json.loads(e["answers"])[0]["name"] == "edit_file"][:12]
print(f"[drift] {len(edit_cases)} held-out edit_file cases")

def catalog_hash(tools):
    key = sorted((t["name"], tuple(sorted(t["parameters"]))) for t in tools)
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]

manifest = {m["family"]: m for m in json.load(open(os.path.join(LAB, "data", "manifest.json")))}
trained_hash = manifest["tool_selection"]["catalog_hash"]

# ── 1. renamed param key ─────────────────────────────────────────────────────
def rename_key(tools_json):
    tools = json.loads(tools_json)
    for t in tools:
        if t["name"] == "edit_file" and "find" in t["parameters"]:
            t["parameters"]["needle_find"] = t["parameters"].pop("find")
    return json.dumps(tools, separators=(",", ":"))

drifted = [rename_key(e["tools"]) for e in edit_cases]
preds = generate_batch(model, params, tok, [e["query"] for e in edit_cases], drifted,
                       max_gen_len=128, max_enc_len=512)
forced_stale = valid_new_key = parse_fail = 0
for e, pr in zip(edit_cases, preds):
    try:
        c = json.loads(pr.strip())[0]
        args = c.get("arguments", {})
        if "needle_find" in args:
            valid_new_key += 1
            gold = json.loads(e["answers"])[0]["arguments"]
            if args.get("needle_find") != gold.get("find"):
                forced_stale += 1
    except Exception:
        parse_fail += 1
print(f"[drift/renamed-key] emitted new key: {valid_new_key}/{len(edit_cases)}, "
      f"wrong value under new key: {forced_stale}, parse fails: {parse_fail}")
print(f"  sample: {preds[0][:140]!r}")

# ── 2. new tool shadowing ────────────────────────────────────────────────────
new_tool = {"name": "format_code", "description": "Auto-format a source file with the project formatter.",
            "parameters": {"path": {"type": "string", "description": "File path.", "required": True}}}
base_tools = json.loads(edit_cases[0]["tools"])
aug = json.dumps(base_tools + [new_tool], separators=(",", ":"))
fmt_queries = ["format src/utils.py with the project formatter",
               "auto-format app/router.ts", "run the formatter on lib/parse.go",
               "prettify cmd/main.rs using our standard style",
               "apply code formatting to src/storage.py", "format tests/test_jobs.py"]
preds = generate_batch(model, params, tok, fmt_queries, [aug] * len(fmt_queries),
                       max_gen_len=96, max_enc_len=512)
picked_new = 0
for q, pr in zip(fmt_queries, preds):
    try:
        name = json.loads(pr.strip())[0].get("name")
    except Exception:
        name = "<parse fail>"
    picked_new += name == "format_code"
    print(f"  {q!r} -> {name}")
print(f"[drift/new-tool] picked format_code: {picked_new}/{len(fmt_queries)}")

# ── 3. hash guard (the no-sqlite invalidation key) ───────────────────────────
served = json.loads(rename_key(edit_cases[0]["tools"]))
h_served = catalog_hash(served)
print(f"[flywheel] trained_hash={trained_hash} served_hash={h_served} "
      f"match={h_served == trained_hash} -> {'SERVE' if h_served == trained_hash else 'ABSTAIN + queue retrain (file-only check, no db)'}")
print("VERDICT:" + json.dumps({"stale_forced": forced_stale, "new_key_emitted": valid_new_key,
                               "n_edit": len(edit_cases), "new_tool_picked": picked_new,
                               "n_fmt": len(fmt_queries), "hash_guard_fires": h_served != trained_hash}))
