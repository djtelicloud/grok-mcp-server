"""Family 8: next_step — trajectory-conditioned policy examples for the self-loop probe.

Each example: query = task + 'so far:' history of (call -> observation digest),
answer = the next tool call. Trains Needle to act as a bounded ReAct policy.
Includes a 'done' pseudo-tool so the loop can terminate itself.
"""
import itertools, json, os, random

LAB = os.path.dirname(os.path.abspath(__file__))
rng = random.Random(7)

def cj(o): return json.dumps(o, separators=(",", ":"))
def tool(name, desc, params):
    return {"name": name, "description": desc,
            "parameters": {k: {"type": t, "description": d, "required": True}
                           for k, (t, d) in params.items()}}

TOOLS = [
    tool("search_code", "Search the codebase for a pattern.", {"pattern": ("string", "Search pattern.")}),
    tool("read_file", "Read the contents of a file.", {"path": ("string", "File path.")}),
    tool("edit_file", "Replace text in a file.", {"path": ("string", "File path."), "find": ("string", "Text to find."), "replace": ("string", "Replacement text.")}),
    tool("run_tests", "Run the test suite.", {"target": ("string", "Test file or 'all'.")}),
    tool("git_commit", "Commit staged changes.", {"message": ("string", "Commit message.")}),
    tool("done", "Call when the task is fully complete.", {"summary": ("string", "One-line completion summary.")}),
]

SYMS = ["parse_config", "AuthMiddleware", "retry_loop", "save_telemetry", "RouteTable",
        "loadSession", "chunk_writer", "validate_body", "sync_state", "renderNav"]
FILES = ["src/utils.py", "src/http_server.py", "app/router.ts", "lib/parse.go",
         "src/storage.py", "tests/test_jobs.py", "cmd/main.rs", "pkg/auth.java"]
TASKS = ["fix the bug in {s} and commit", "rename {s} to {s}_v2 safely and commit",
         "patch {s} then verify tests pass", "update {s} and land the change"]

def workflow(s, f, msg):
    """Canonical 5-step trajectory with observation digests."""
    return [
        ({"name": "search_code", "arguments": {"pattern": s}},
         f"found {s} in {f}:214"),
        ({"name": "read_file", "arguments": {"path": f}},
         f"read {f}: {s} defined at line 214, called twice"),
        ({"name": "edit_file", "arguments": {"path": f, "find": s, "replace": s + "_v2"}},
         f"edited {f}: 3 occurrences replaced"),
        ({"name": "run_tests", "arguments": {"target": f}},
         "exit 0: 12 tests passed"),
        ({"name": "git_commit", "arguments": {"message": msg}},
         "commit 9c1e42 created"),
        ({"name": "done", "arguments": {"summary": f"updated {s} in {f}, tests green, committed"}},
         None),
    ]

FAIL_BRANCHES = [
    # (step index where failure injected, failing observation, corrective next call)
    (3, "exit 1: 2 tests FAILED in {f}",
     lambda s, f, m: {"name": "read_file", "arguments": {"path": f}}),
    (0, "0 matches found",
     lambda s, f, m: {"name": "search_code", "arguments": {"pattern": s.split("_")[0]}}),
]

def render(task, history):
    if not history:
        return f"task: {task}\nso far: nothing yet"
    lines = "\n".join(f"- {cj(c)} -> {o}" for c, o in history)
    return f"task: {task}\nso far:\n{lines}"

out = []
for s, f in itertools.product(SYMS, FILES):
    msg = f"update {s}"
    task = rng.choice(TASKS).format(s=s)
    steps = workflow(s, f, msg)
    hist = []
    for call, obs in steps:
        out.append({"query": render(task, hist), "tools": cj(TOOLS), "answers": cj([call])})
        if obs is not None:
            hist.append((call, obs))
    # one failure branch per (s,f) pair
    idx, failobs, correct = FAIL_BRANCHES[hash((s, f)) % len(FAIL_BRANCHES)]
    hist = [(c, o) for c, o in [x for x in steps[:idx] if x[1] is not None]]
    failed_call = steps[idx][0]
    hist.append((failed_call, failobs.format(f=f)))
    out.append({"query": render(task, hist), "tools": cj(TOOLS),
                "answers": cj([correct(s, f, msg)])})

rng.shuffle(out)
path = os.path.join(LAB, "data", "next_step.jsonl")
with open(path, "w") as fh:
    for e in out:
        fh.write(json.dumps(e) + "\n")
import collections
c = collections.Counter(json.loads(e["answers"])[0]["name"] for e in out)
print(f"next_step n={len(out)} per-tool={dict(c)}")
