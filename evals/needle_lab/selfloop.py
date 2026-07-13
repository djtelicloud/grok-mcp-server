"""Phase D probe: Needle-ReAct micro-loop — output fed back as input until done.

Uses the next_step fine-tune as a policy over observation digests, in a simulated
env. Tested on UNSEEN symbols/files (not in training data) so success requires
generalized value-copying, not memorization. Bounds: max 8 iterations,
same-call-twice stop. Also injects a test failure to probe recovery.
"""
import argparse, json, os

p = argparse.ArgumentParser()
p.add_argument("--checkpoint", required=True)
a = p.parse_args()

from needle.model.run import load_checkpoint, generate
from needle.model.architecture import SimpleAttentionNetwork
from needle.dataset.tokenizer import get_tokenizer

params, config = load_checkpoint(a.checkpoint)
model = SimpleAttentionNetwork(config)
tok = get_tokenizer()

def cj(o): return json.dumps(o, separators=(",", ":"))
TOOLS = json.dumps([
    {"name": "search_code", "description": "Search the codebase for a pattern.", "parameters": {"pattern": {"type": "string", "description": "Search pattern.", "required": True}}},
    {"name": "read_file", "description": "Read the contents of a file.", "parameters": {"path": {"type": "string", "description": "File path.", "required": True}}},
    {"name": "edit_file", "description": "Replace text in a file.", "parameters": {"path": {"type": "string", "description": "File path.", "required": True}, "find": {"type": "string", "description": "Text to find.", "required": True}, "replace": {"type": "string", "description": "Replacement text.", "required": True}}},
    {"name": "run_tests", "description": "Run the test suite.", "parameters": {"target": {"type": "string", "description": "Test file or 'all'.", "required": True}}},
    {"name": "git_commit", "description": "Commit staged changes.", "parameters": {"message": {"type": "string", "description": "Commit message.", "required": True}}},
    {"name": "done", "description": "Call when the task is fully complete.", "parameters": {"summary": {"type": "string", "description": "One-line completion summary.", "required": True}}},
], separators=(",", ":"))

class Env:
    """Simulated repo. Tracks state so 'correct process' is checkable."""
    def __init__(self, sym, path, fail_tests_once=False):
        self.sym, self.path = sym, path
        self.edited = self.tested = self.committed = False
        self.fail_tests_once = fail_tests_once
        self.log = []

    def observe(self, call):
        name, args = call.get("name"), call.get("arguments", {}) or {}
        self.log.append(call)
        if name == "search_code":
            hit = args.get("pattern", "") and args["pattern"] in self.sym
            return f"found {self.sym} in {self.path}:214" if hit else "0 matches found"
        if name == "read_file":
            return f"read {args.get('path','?')}: {self.sym} defined at line 214, called twice"
        if name == "edit_file":
            ok = args.get("path") == self.path and args.get("find", "") in (self.sym, self.sym)
            self.edited = self.edited or bool(ok)
            return f"edited {args.get('path','?')}: 3 occurrences replaced" if ok else "0 occurrences found, nothing edited"
        if name == "run_tests":
            if self.fail_tests_once:
                self.fail_tests_once = False
                return f"exit 1: 2 tests FAILED in {self.path}"
            self.tested = self.edited
            return "exit 0: 12 tests passed"
        if name == "git_commit":
            self.committed = self.tested
            return "commit 9c1e42 created"
        return None  # done

def run_loop(task, env, max_iters=8):
    history = []
    calls_seen = set()
    for it in range(max_iters):
        if history:
            lines = "\n".join(f"- {cj(c)} -> {o}" for c, o in history)
            q = f"task: {task}\nso far:\n{lines}"
        else:
            q = f"task: {task}\nso far: nothing yet"
        out = generate(model, params, tok, q, tools=TOOLS, stream=False, max_gen_len=96)
        try:
            call = json.loads(out.strip())
            call = call[0] if isinstance(call, list) and call else call
        except Exception:
            return {"ok": False, "why": f"parse fail at iter {it}: {out[:80]!r}", "iters": it}
        key = cj(call)
        if key in calls_seen:
            return {"ok": False, "why": f"repeated call at iter {it}: {key[:80]}", "iters": it,
                    "trace": [cj(c) for c, _ in history]}
        calls_seen.add(key)
        if call.get("name") == "done":
            done_ok = env.edited and env.tested and env.committed
            return {"ok": done_ok, "why": "done called" + ("" if done_ok else " PREMATURELY"),
                    "iters": it + 1, "trace": [cj(c) for c, _ in history] + [key]}
        obs = env.observe(call)
        history.append((call, obs))
    return {"ok": False, "why": "max iters", "iters": max_iters,
            "trace": [cj(c) for c, _ in history]}

# UNSEEN vocabulary — none of these appear in next_step.jsonl
SCENARIOS = [
    ("normalize_headers", "src/metrics.py", False),
    ("FlushQueue", "app/worker.ts", False),
    ("decode_frame", "lib/codec.go", False),
    ("SessionGuard", "pkg/session.java", False),
    ("rotate_logs", "cmd/logrotate.rs", False),
    ("merge_manifest", "src/manifest.py", True),   # test failure injected
    ("ParseRetry", "app/retry.ts", True),
]
results = []
for sym, path, fail in SCENARIOS:
    env = Env(sym, path, fail_tests_once=fail)
    task = f"rename {sym} to {sym}_v2 safely and commit"
    r = run_loop(task, env)
    r.update(sym=sym, fail_injected=fail)
    ok = "PASS" if r["ok"] else "FAIL"
    print(f"[{ok}] {sym:<18} fail_inj={fail} iters={r['iters']} why={r['why']}")
    if not r["ok"] and "trace" in r:
        for t in r["trace"]:
            print(f"      {t[:110]}")
    results.append(r)

n_ok = sum(r["ok"] for r in results)
print(f"\nVERDICT:{json.dumps({'pass': n_ok, 'total': len(results)})}")
