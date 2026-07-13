"""Phase D probe: revive the dead contrastive retrieval head.

The released checkpoint's contrastive projection outputs all-zeros (verified in
baseline.py). This trains ONLY the contrastive pathway with needle's own CLIP
loss on (query -> gold doc) pairs from our datasets, then measures retrieval
top-1 over two corpora: the 6-tool dev catalog and the 10 memory cards.
If this works, Needle can be the learned retrieval engine over capsule files
(the no-sqlite claim).
"""
import json, os, time
import numpy as np
import jax, jax.numpy as jnp
import optax

LAB = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(LAB, "checkpoints", "needle.pkl")

from needle.model.run import load_checkpoint
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer
from needle.training.train import _clip_contrastive_loss
from needle.training.finetune import _per_tool_split

params, config = load_checkpoint(CKPT)
model = SimpleAttentionNetwork(config)
tok = get_tokenizer()
pad = tok.pad_token_id

# ─── diagnose + re-init the dead head ────────────────────────────────────────
# Released weights have the contrastive head at EXACT zeros -> ReLU saddle ->
# zero gradients forever. Re-initialize the two head layers; train head-only.
for name in ("contrastive_hidden", "contrastive_proj"):
    leaves = jax.tree.leaves(params[name])
    print(f"[head] {name} max|w| before = {max(float(jnp.abs(x).max()) for x in leaves):.2e}")
key = jax.random.PRNGKey(0)
k1, k2, k3 = jax.random.split(key, 3)
params["contrastive_hidden"]["kernel"] = (
    jax.random.normal(k1, params["contrastive_hidden"]["kernel"].shape, jnp.bfloat16) * 0.02)
if "bias" in params["contrastive_hidden"]:
    params["contrastive_hidden"]["bias"] = (
        jax.random.normal(k2, params["contrastive_hidden"]["bias"].shape, jnp.bfloat16) * 0.01)
params["contrastive_proj"]["kernel"] = (
    jax.random.normal(k3, params["contrastive_proj"]["kernel"].shape, jnp.bfloat16) * 0.02)

HEAD = ("contrastive_hidden", "contrastive_proj", "log_temp")

# ─── pairs: (query, doc) — doc = tool one-liner or memory card ───────────────
TOOL_DOCS = {
    "read_file": "read_file: read the contents of a file at a path",
    "search_code": "search_code: search the codebase for a pattern or symbol",
    "edit_file": "edit_file: replace text in a file",
    "run_tests": "run_tests: execute the test suite and report results",
    "git_commit": "git_commit: commit staged changes with a message",
    "list_dir": "list_dir: list files in a directory",
}
pairs, test_pairs = [], []
examples = [json.loads(l) for l in open(os.path.join(LAB, "data", "tool_selection.jsonl"))]
train_ex, _, test_ex = _per_tool_split(examples)
for src, dst in ((train_ex, pairs), (test_ex, test_pairs)):
    for e in src:
        name = json.loads(e["answers"])[0]["name"]
        dst.append((e["query"], TOOL_DOCS[name]))

CARDS = {
    "auth": "fixed OAuth refresh loop in src/credentials.py by pinning token expiry",
    "routing": "planning tasks route to grok-4.5; coding stays on composer unless escalated",
    "docker": "CLI plane runs in docker; auth persists in unigrok-cli-auth volume",
    "evals": "golden tasks live in evals/tasks; baseline gate is --check-baseline",
    "budget": "agent loop hard-stops at $0.50; caller budgets via UNIGROK_CALLER_BUDGETS",
    "sessions": "native CLI sessions via -s id are the continuity mechanism",
    "ui": "test bench UI at /ui/ shows live route receipts",
    "git": "git writes need UNIGROK_RUNTIME=local and ENABLE_GIT_WRITE=1",
    "needle": "needle projections are built by build_needle_tools_context, 1024 tokens max",
    "plane": "same_plane policy forbids crossing billing boundary on fallback",
}
# Grok-generated paraphrase pairs (120 train / 50 test, messy realistic phrasing)
_mp = json.load(open(os.path.join(LAB, "data", "memory_pairs.json")))
MEM_TRAIN = [(x["query"], x["card"]) for x in _mp["train"]]
MEM_TEST = [(x["query"], x["card"]) for x in _mp["test"]]
for q, cid in MEM_TRAIN:
    pairs.append((q, f"{cid}: {CARDS[cid]}"))

print(f"[pairs] train={len(pairs)} test(tool)={len(test_pairs)} test(mem)={len(MEM_TEST)}")

def batchify(texts, max_len=256):
    ids = [tok.encode(t)[:max_len] for t in texts]
    m = max(len(x) for x in ids)
    arr = np.full((len(ids), m), pad, dtype=np.int32)
    for i, x in enumerate(ids):
        arr[i, :len(x)] = x
    return jnp.array(arr)

def embed(p, texts):
    return np.asarray(model.apply({"params": p}, batchify(texts),
                                  deterministic=True, method="encode_contrastive"))

def retrieval_top1(p, queries, gold_idx, corpus):
    qe = embed(p, queries)
    ce = embed(p, corpus)
    pred = (qe @ ce.T).argmax(axis=1)
    return float((pred == np.array(gold_idx)).mean())

def eval_all(p, label):
    docs = list(TOOL_DOCS.values())
    tq = [q for q, d in test_pairs]
    tg = [docs.index(d) for q, d in test_pairs]
    tool_acc = retrieval_top1(p, tq, tg, docs)
    cards = [f"{cid}: {txt}" for cid, txt in CARDS.items()]
    mq = [q for q, c in MEM_TEST]
    mg = [list(CARDS).index(c) for q, c in MEM_TEST]
    mem_acc = retrieval_top1(p, mq, mg, cards)
    print(f"[{label}] tool-retrieval top1={tool_acc:.1%} ({len(tq)} queries/6 docs)  "
          f"memory-retrieval top1={mem_acc:.1%} ({len(mq)} queries/10 cards)")
    return tool_acc, mem_acc

eval_all(params, "before")

# ─── train: CLIP loss, HEAD-ONLY updates (protects the tool-calling pathway) ──
# NOTE optax.masked leaks raw grads for unmasked leaves (verified: it destroyed
# the model in run 2) — multi_transform + set_to_zero is the correct freeze.
STEPS, BATCH, LR = 400, 32, 1e-3
labels = jax.tree.map(lambda _: "freeze", params)
for h in HEAD:
    labels[h] = jax.tree.map(lambda _: "train", params[h])
opt = optax.multi_transform(
    {"train": optax.adam(LR), "freeze": optax.set_to_zero()}, labels)
opt_state = opt.init(params)
rng_np = np.random.default_rng(0)

@jax.jit
def step(p, o, q_tok, d_tok):
    def loss_fn(pp):
        qe, de, lt = model.apply({"params": pp}, q_tok, d_tok,
                                 deterministic=True, method="forward_contrastive")
        return _clip_contrastive_loss(qe, de, lt)
    loss, grads = jax.value_and_grad(loss_fn)(p)
    updates, o = opt.update(grads, o, p)
    return optax.apply_updates(p, updates), o, loss

t0 = time.time()
for i in range(STEPS):
    idx = rng_np.choice(len(pairs), BATCH, replace=False)
    qs = [pairs[j][0] for j in idx]
    ds = [pairs[j][1] for j in idx]
    params, opt_state, loss = step(params, opt_state, batchify(qs), batchify(ds))
    if i % 50 == 0 or i == STEPS - 1:
        print(f"  step {i:>3} loss={float(loss):.4f} ({time.time()-t0:.0f}s)")

tool_acc, mem_acc = eval_all(params, "after")

# does contrastive training destroy tool-calling? quick check
from needle.model.run import generate
out = generate(model, params, tok, "Turn off the kitchen lights",
               tools='[{"name":"toggle_lights","description":"Toggle smart lights on or off.","parameters":{"room":{"type":"string","description":"Room name.","required":true},"state":{"type":"string","description":"on or off.","required":true}}}]',
               stream=False)
print(f"[side-effect] tool-calling after contrastive training: {out!r}")

import pickle
outp = os.path.join(LAB, "checkpoints", "needle_retrieval.pkl")
with open(outp, "wb") as f:
    pickle.dump({"params": jax.tree.map(lambda x: np.asarray(x), params),
                 "config": config.__dict__}, f)
print(f"[saved] {outp}")
print("VERDICT:" + json.dumps({"tool_top1": tool_acc, "mem_top1": mem_acc}))
