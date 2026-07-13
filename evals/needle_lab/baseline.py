"""Phase A: baseline — documented tool-calling + undocumented contrastive retrieval."""
import json, os, time, resource, sys

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
LAB = os.path.dirname(os.path.abspath(__file__))
CKPT_DIR = os.path.join(LAB, "checkpoints")
os.makedirs(CKPT_DIR, exist_ok=True)

from huggingface_hub import hf_hub_download

t0 = time.time()
ckpt = hf_hub_download(repo_id="Cactus-Compute/needle", filename="needle.pkl",
                       repo_type="model", local_dir=CKPT_DIR)
print(f"[weights] {ckpt} ({os.path.getsize(ckpt)/1e6:.1f} MB, {time.time()-t0:.1f}s)")

from needle.model.run import load_checkpoint, generate, generate_batch, retrieve_tools, encode_for_retrieval
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer

t0 = time.time()
params, config = load_checkpoint(ckpt)
model = SimpleAttentionNetwork(config)
tokenizer = get_tokenizer()
import jax
n_params = sum(x.size for x in jax.tree.leaves(params))
print(f"[load] {n_params:,} params, config={config.__dict__}, {time.time()-t0:.1f}s")

CASES = [
    ("What's the weather in San Francisco?",
     '[{"name":"get_weather","description":"Get current weather for a city.","parameters":{"location":{"type":"string","description":"City name.","required":true}}}]',
     {"name": "get_weather", "arguments": {"location": "San Francisco"}}),
    ("Send an email to john@example.com saying the deploy finished",
     '[{"name":"send_email","description":"Send an email to a recipient.","parameters":{"to":{"type":"string","description":"Recipient email.","required":true},"body":{"type":"string","description":"Email body.","required":true}}},{"name":"get_weather","description":"Get current weather for a city.","parameters":{"location":{"type":"string","description":"City name.","required":true}}}]',
     {"name": "send_email"}),
    ("Turn off the kitchen lights",
     '[{"name":"toggle_lights","description":"Toggle smart lights on or off.","parameters":{"room":{"type":"string","description":"Room name.","required":true},"state":{"type":"string","description":"on or off.","required":true}}}]',
     {"name": "toggle_lights"}),
]

print("\n=== Documented behavior: single-shot tool calling ===")
results = []
for i, (q, tools, expect) in enumerate(CASES):
    t0 = time.time()
    out = generate(model, params, tokenizer, q, tools=tools, stream=False)
    dt = time.time() - t0
    try:
        calls = json.loads(out)
        ok = isinstance(calls, list) and calls and calls[0].get("name") == expect["name"]
        if "arguments" in expect:
            ok = ok and calls[0].get("arguments") == expect["arguments"]
    except Exception:
        calls, ok = None, False
    results.append(ok)
    print(f"  [{i}] {dt*1000:.0f}ms ok={ok} query={q!r}\n      out={out!r}")

print("\n=== Steady-state latency (case 0, 5 runs after JIT warm) ===")
q, tools, _ = CASES[0]
lat = []
for _ in range(5):
    t0 = time.time()
    generate(model, params, tokenizer, q, tools=tools, stream=False)
    lat.append(time.time() - t0)
print(f"  per-call: {[f'{x*1000:.0f}ms' for x in lat]}")

print("\n=== UNDOCUMENTED: contrastive retrieval (retrieve_tools) ===")
docs = [
    "get_weather: Get current weather conditions for a city",
    "send_email: Send an email message to a recipient address",
    "toggle_lights: Turn smart home lights on or off in a room",
    "run_tests: Execute the project test suite and report failures",
    "git_commit: Commit staged changes with a message",
    "search_code: Search the codebase for a symbol or pattern",
    "read_file: Read the contents of a file at a path",
    "book_flight: Book an airline flight between two cities",
]
queries = [
    "is it raining in Tokyo right now?",
    "let bob know the meeting moved to 3pm",
    "make the living room dark",
    "did my latest change break anything?",
    "save my work to version control",
    "where is the function that parses JSONL?",
]
t0 = time.time()
for q in queries:
    hits = retrieve_tools(model, params, tokenizer, q, docs, top_k=3)
    top = [(docs[i].split(":")[0], round(s, 3)) for i, s in hits]
    print(f"  {q!r} -> {top}")
print(f"  retrieval total: {time.time()-t0:.1f}s")

print("\n=== UNDOCUMENTED: raw text embeddings (arbitrary text, not tools) ===")
texts = [
    "fix the failing pytest in the auth module",
    "the test suite is broken after my change",
    "book me a table for dinner",
    "what model should handle a deep reasoning task?",
    "route this to the planning model please",
]
embs = encode_for_retrieval(model, params, tokenizer, texts)
import numpy as np
sims = embs @ embs.T
print("  cosine sim matrix (should cluster 0-1 and 3-4):")
for i, row in enumerate(sims):
    print(f"    {i}: " + " ".join(f"{v:+.2f}" for v in row))

mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
print(f"\n[mem] peak RSS ≈ {mem:.0f} MB")
print(f"[verdict] documented cases pass: {sum(results)}/{len(results)}")
