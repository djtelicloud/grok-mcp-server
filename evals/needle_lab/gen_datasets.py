"""Phase B: dataset factory — 7 UniGrok gateway families as JSONL capsule files.

No sqlite anywhere: datasets are plain JSONL, manifest is JSON with catalog
hashes (the dynamic-TTL invalidation key for Phase D).
"""
import hashlib, itertools, json, os, random

LAB = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(LAB, "data")
os.makedirs(OUT, exist_ok=True)
rng = random.Random(42)

def cj(o):  # compact json string
    return json.dumps(o, separators=(",", ":"))

def tool(name, desc, params):
    return {"name": name, "description": desc,
            "parameters": {k: {"type": t, "description": d, "required": True}
                           for k, (t, d) in params.items()}}

def ex(query, tools, calls):
    return {"query": query, "tools": cj(tools), "answers": cj(calls)}

def catalog_hash(tools):
    key = sorted((t["name"], tuple(sorted(t["parameters"]))) for t in tools)
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]

def write(name, examples, tools):
    rng.shuffle(examples)
    path = os.path.join(OUT, f"{name}.jsonl")
    with open(path, "w") as f:
        for e in examples:
            f.write(json.dumps(e) + "\n")
    return {"family": name, "path": path, "n": len(examples),
            "catalog_hash": catalog_hash(tools)}

manifest = []

# ─── 1. route_selection ──────────────────────────────────────────────────────
ROUTE_TOOL = tool("route", "Select the execution mode and route class for a user task.",
                  {"mode": ("string", "One of: fast, reasoning, thinking, research."),
                   "route_class": ("string", "One of: coding, planning, research, vision.")})

CODING = ["fix the {} in {}", "add a unit test for {} in {}", "refactor {} inside {}",
          "why does {} throw in {}?", "implement {} for the {} module",
          "rename {} across {}", "the linter flags {} in {} — clean it up",
          "write a docstring for {} in {}", "optimize the hot loop {} in {}"]
BUGS = ["null deref", "race condition", "off-by-one", "memory leak", "type error",
        "flaky assertion", "regex bug", "encoding bug", "deadlock", "cache miss"]
FILES = ["src/utils.py", "src/http_server.py", "app/router.ts", "lib/parse.go",
         "src/storage.py", "tests/test_jobs.py", "cmd/main.rs", "pkg/auth.java"]
PLANNING = ["design a migration plan for {}", "compare {} vs {} and recommend one",
            "what architecture should we use for {}?", "draft an ADR about {}",
            "break down the work to ship {}", "estimate the risk of {}",
            "how should we roll out {} safely?"]
TOPICS = ["multi-region failover", "the billing rewrite", "event sourcing",
          "a plugin system", "zero-downtime deploys", "sharding the user table",
          "the v2 API", "offline mode"]
RESEARCH = ["research the latest on {}", "find recent papers about {}",
            "what are people saying about {} this month?", "survey the ecosystem around {}",
            "compile sources on {} with citations", "deep dive: {} — cite everything"]
RTOPICS = ["small language models", "JAX on Apple silicon", "MCP adoption",
           "constrained decoding", "agent memory systems", "edge inference",
           "tool-calling benchmarks", "grok model releases"]
VISION = ["what's in this screenshot{}", "describe the attached diagram{}",
          "read the text from this image{}", "is this chart{} showing a regression?",
          "compare these two UI mockups{}"]

def route_examples():
    out = []
    for t_, b, f in itertools.product(CODING, BUGS, FILES):
        out.append(ex(t_.format(b, f), [ROUTE_TOOL],
                      [{"name": "route", "arguments": {"mode": "fast", "route_class": "coding"}}]))
    for t_ in PLANNING:
        for tp in TOPICS:
            q = t_.format(tp, rng.choice(TOPICS)) if t_.count("{}") == 2 else t_.format(tp)
            out.append(ex(q, [ROUTE_TOOL],
                          [{"name": "route", "arguments": {"mode": "reasoning", "route_class": "planning"}}]))
    for t_ in RESEARCH:
        for tp in RTOPICS:
            out.append(ex(t_.format(tp), [ROUTE_TOOL],
                          [{"name": "route", "arguments": {"mode": "research", "route_class": "research"}}]))
    for t_ in VISION:
        for suf in ["", "?", " please", " for me"]:
            out.append(ex(t_.format(suf), [ROUTE_TOOL],
                          [{"name": "route", "arguments": {"mode": "fast", "route_class": "vision"}}]))
    rng.shuffle(out)
    # balance: cap coding at 220 so classes are comparable
    coding = [e for e in out if '"coding"' in e["answers"]][:220]
    rest = [e for e in out if '"coding"' not in e["answers"]]
    return coding + rest

manifest.append(write("route_selection", route_examples(), [ROUTE_TOOL]))

# ─── 2. observation_typing ───────────────────────────────────────────────────
OBS_TOOL = tool("type_observation", "Classify a tool observation digest.",
                {"label": ("string", "One of: success, tool_error_retryable, tool_error_fatal, resource_unavailable, empty_result.")})
OBS = {
    "success": ['exit 0: 42 tests passed in 3.1s', 'HTTP 200 {"ok":true,"rows":18}',
                "wrote 214 bytes to out.json", "commit 3f2a1c created on branch fix/auth",
                "found 7 matches in 3 files", '{"status":"healthy"}', "file saved, lint clean",
                "deploy finished: revision 41 live", "3 rows updated", "cache warmed: 128 keys"],
    "tool_error_retryable": ["HTTP 429 Too Many Requests, retry-after: 12",
                             "connection reset by peer", "timeout after 30s waiting for lock",
                             "HTTP 503 service unavailable", "temporary DNS failure for api.x.ai",
                             "rate limit exceeded, backoff advised", "socket hang up mid-response",
                             "database is locked (sqlite busy)", "TLS handshake timeout"],
    "tool_error_fatal": ["SyntaxError: unexpected token at line 14", "HTTP 401 invalid api key",
                         "permission denied: /etc/shadow", "no such file: src/mian.py",
                         "TypeError: cannot read property 'id' of undefined",
                         "HTTP 404 model not found: grok-9", "invalid JSON schema: missing type",
                         "assertion failed: expected 3 got 7", "segmentation fault (core dumped)"],
    "resource_unavailable": ["grok CLI not authenticated: run grok auth login",
                             "docker daemon is not running", "XAI_API_KEY unset",
                             "no network route to host", "GPU not found, CUDA unavailable",
                             "MCP server on :4765 not reachable", "OAuth token expired and refresh failed",
                             "circuit breaker open for plane CLI"],
    "empty_result": ["0 matches found", "query returned no rows", "[]", "{}",
                     "no files changed", "search produced nothing", "empty response body",
                     "0 tests collected", "no sessions found"],
}
def obs_examples():
    out = []
    prefixes = ["tool output: ", "observation: ", "", "last result: ", "stderr: ", "stdout: "]
    for label, digests in OBS.items():
        for d in digests:
            for p in prefixes[:4 if label == "success" else 3]:
                out.append(ex(p + d, [OBS_TOOL],
                              [{"name": "type_observation", "arguments": {"label": label}}]))
    return out
manifest.append(write("observation_typing", obs_examples(), [OBS_TOOL]))

# ─── 3. recovery_selection ───────────────────────────────────────────────────
REC_TOOL = tool("recover", "Choose the recovery action for a failed agent step.",
                {"action": ("string", "One of: retry_same, retry_alt_model, degrade_fast, enter_wait, fail_typed.")})
REC = {
    "retry_same": ["HTTP 429 rate limited, plane=api, budget left $0.40, attempt 1 of 3",
                   "timeout after 30s, plane=api, attempt 1, network fine",
                   "connection reset mid-stream, attempt 1, same plane allowed",
                   "503 from upstream, retry-after 5s, attempt 1"],
    "retry_alt_model": ["model grok-composer overloaded, catalog has grok-4.5 same plane, attempt 2",
                        "context length exceeded on small model, larger model available same plane",
                        "model returned garbage twice, sibling model available, attempt 3",
                        "model deprecated error, replacement model listed in catalog"],
    "degrade_fast": ["agent loop failed twice with tool errors, budget nearly gone ($0.05 left)",
                     "reasoning route crashed, user asked a one-line question, fast path available",
                     "tool registry corrupted, plain completion still possible",
                     "depth exhausted at 8 with no answer, simple summary still possible"],
    "enter_wait": ["plane CLI not authenticated and plane API has no key, fallback_policy=same_plane",
                   "docker daemon down, task requires CLI container, policy forbids cross-plane",
                   "circuit breaker open for 10 more minutes on the only legal plane",
                   "network fully offline, task queued as durable job"],
    "fail_typed": ["request asks for vision on CLI plane which never supports vision",
                   "caller budget is $0.00 before the first call",
                   "fallback_policy=same_plane and requested plane is permanently misconfigured",
                   "request requires git write but ENABLE_GIT_WRITE=0 in cloud runtime"],
}
def rec_examples():
    out = []
    prefixes = ["failure: ", "context: ", "", "recover from: "]
    for label, ctxs in REC.items():
        for c in ctxs:
            for p in prefixes:
                for suffix in ["", " — pick a recovery", " what now?"]:
                    out.append(ex(p + c + suffix, [REC_TOOL],
                                  [{"name": "recover", "arguments": {"action": label}}]))
    return out
manifest.append(write("recovery_selection", rec_examples(), [REC_TOOL]))

# ─── 4. memory_rerank ────────────────────────────────────────────────────────
MEM_TOOL = tool("select_context", "Pick the ids of the memory cards most relevant to the task.",
                {"ids": ("array", "ids of the 1-2 most relevant candidate cards.")})
CARDS = [
    ("auth", "fixed OAuth refresh loop in src/credentials.py by pinning token expiry"),
    ("routing", "planning tasks route to grok-4.5; coding stays on composer unless escalated"),
    ("docker", "CLI plane runs in docker; auth persists in unigrok-cli-auth volume"),
    ("evals", "golden tasks live in evals/tasks; baseline gate is --check-baseline"),
    ("budget", "agent loop hard-stops at $0.50; caller budgets via UNIGROK_CALLER_BUDGETS"),
    ("sessions", "native CLI sessions via -s id are the continuity mechanism"),
    ("ui", "test bench UI at /ui/ shows live route receipts"),
    ("git", "git writes need UNIGROK_RUNTIME=local and ENABLE_GIT_WRITE=1"),
    ("needle", "needle projections are built by build_needle_tools_context, 1024 tokens max"),
    ("plane", "same_plane policy forbids crossing billing boundary on fallback"),
]
MEM_TASKS = [
    ("why is the oauth token expiring mid-session?", ["auth"]),
    ("which model should a design review go to?", ["routing"]),
    ("the container lost its login after rebuild", ["docker", "sessions"]),
    ("how do I add a new golden eval task?", ["evals"]),
    ("agent stopped early complaining about cost", ["budget"]),
    ("continue yesterday's CLI conversation", ["sessions"]),
    ("where can I watch routing decisions live?", ["ui"]),
    ("commit is failing with a permission error from the server", ["git"]),
    ("how big can the needle context get?", ["needle"]),
    ("api key died — can we hop to the subscription?", ["plane"]),
    ("docker auth volume — what was it called again?", ["docker"]),
    ("what gates a route flip between models?", ["routing"]),
    ("test bench url?", ["ui"]),
    ("token refresh bug fix — which file was that?", ["auth"]),
    ("spending cap per caller — how is it set?", ["budget"]),
]
def mem_examples():
    out = []
    for task, gold in MEM_TASKS:
        for _ in range(11):
            distractors = rng.sample([c for c in CARDS if c[0] not in gold], 4)
            chosen = [(g, dict(CARDS)[g]) for g in gold]
            cards = distractors + chosen
            rng.shuffle(cards)
            lines = "\n".join(f"{cid}: {summary}" for cid, summary in cards)
            q = f"task: {task}\ncards:\n{lines}"
            out.append(ex(q, [MEM_TOOL],
                          [{"name": "select_context", "arguments": {"ids": gold}}]))
    return out
manifest.append(write("memory_rerank", mem_examples(), [MEM_TOOL]))

# ─── 5. tool_selection (CursorBench-style dev tools) ─────────────────────────
DEV_TOOLS = [
    tool("read_file", "Read the contents of a file.", {"path": ("string", "File path.")}),
    tool("search_code", "Search the codebase for a pattern.", {"pattern": ("string", "Search pattern.")}),
    tool("edit_file", "Replace text in a file.", {"path": ("string", "File path."), "find": ("string", "Text to find."), "replace": ("string", "Replacement text.")}),
    tool("run_tests", "Run the test suite.", {"target": ("string", "Test file or 'all'.")}),
    tool("git_commit", "Commit staged changes.", {"message": ("string", "Commit message.")}),
    tool("list_dir", "List files in a directory.", {"path": ("string", "Directory path.")}),
]
SYMS = ["parse_config", "AuthMiddleware", "retry_loop", "save_telemetry", "RouteTable",
        "loadSession", "chunk_writer", "validate_body", "sync_state", "renderNav"]
def toolsel_examples():
    out = []
    READ_PHR = ["show me {}", "open {} so I can see it", "read {}", "cat {}", "what's in {}?",
                "display {}", "print the contents of {}", "pull up {}", "let me look at {}",
                "dump {} to the screen", "view {}", "fetch the source of {}"]
    for f in FILES:
        for p in READ_PHR:
            out.append(ex(p.format(f), DEV_TOOLS,
                          [{"name": "read_file", "arguments": {"path": f}}]))
    SEARCH_PHR = ["where is {} defined?", "find every call to {}", "grep for {}", "search for {}",
                  "locate {} in the codebase", "which files mention {}?", "hunt down {}",
                  "look up usages of {}", "find references to {}", "track down {}"]
    for s in SYMS:
        for p in SEARCH_PHR:
            out.append(ex(p.format(s), DEV_TOOLS,
                          [{"name": "search_code", "arguments": {"pattern": s}}]))
    for s, f in itertools.product(SYMS, FILES):
        out.append(ex(f"in {f} rename {s} to {s}_v2", DEV_TOOLS,
                      [{"name": "edit_file", "arguments": {"path": f, "find": s, "replace": s + "_v2"}}]))
    TEST_PHR = ["run the tests for {}", "does {} pass?", "execute the tests in {}",
                "check whether {} is green", "pytest {} please", "verify {} still passes",
                "re-run {} after my change", "kick off the tests for {}"]
    for f in FILES:
        for p in TEST_PHR:
            out.append(ex(p.format(f), DEV_TOOLS,
                          [{"name": "run_tests", "arguments": {"target": f}}]))
    for p in ["run the whole suite", "run all tests", "full test run", "check everything passes",
              "execute the entire test suite", "run every test we have", "is the build green? run all",
              "smoke the full suite", "all tests, go", "run the complete suite now",
              "pytest everything", "give me a full test pass", "regression run over all tests",
              "run tests across the whole repo", "fire the entire suite", "full pytest sweep"]:
        out.append(ex(p, DEV_TOOLS, [{"name": "run_tests", "arguments": {"target": "all"}}]))
    DIRS = ["src", "tests", "app", "lib", "cmd", "pkg", "docs", "evals", "scripts", "config"]
    for d in DIRS:
        for p in ["what's inside the {} folder?", "list the files in {}", "show the contents of {}",
                  "ls {}", "what files live under {}?", "enumerate everything in {}",
                  "peek into the {} directory", "browse {}"]:
            out.append(ex(p.format(d), DEV_TOOLS,
                          [{"name": "list_dir", "arguments": {"path": d}}]))
    MSGS = ["fix auth retry", "add routing receipts", "bump deps", "docs: update readme",
            "refactor storage layer", "fix flaky test", "wire context forge", "cleanup imports",
            "add abstention tool", "pin needle tokenizer", "harden envelope checks",
            "speed up decode loop", "add drift guard", "record eval baseline",
            "fix session resume", "tighten budget gate"]
    for m in MSGS:
        for phr in ["commit this as '{}'", "save my work: {}", "commit with message {}",
                    "git commit -m {}", "check that in as '{}'", "make a commit: {}",
                    "commit: {}", "land locally with message '{}'"]:
            out.append(ex(phr.format(m), DEV_TOOLS,
                          [{"name": "git_commit", "arguments": {"message": m}}]))
    # dedup on (query, answers)
    seen, deduped = set(), []
    for e in out:
        k = (e["query"], e["answers"])
        if k not in seen:
            seen.add(k)
            deduped.append(e)
    return deduped
manifest.append(write("tool_selection", toolsel_examples(), DEV_TOOLS))

# ─── 6. extraction (free-value probe) ────────────────────────────────────────
EXT_TOOL = tool("extract_task_spec", "Extract the structured spec from a dev request.",
                {"path": ("string", "The file path mentioned."),
                 "symbol": ("string", "The function/class named."),
                 "action": ("string", "One of: fix, test, refactor, document, delete.")})
ACTIONS = {"fix": ["fix", "repair", "debug"], "test": ["write a test for", "add coverage for", "test"],
           "refactor": ["refactor", "restructure", "clean up"],
           "document": ["document", "write docs for", "add a docstring to"],
           "delete": ["remove", "delete", "drop"]}
def ext_examples():
    out = []
    for action, verbs in ACTIONS.items():
        for v, s, f in itertools.product(verbs, SYMS, FILES[:5]):
            out.append(ex(f"{v} {s} in {f}", [EXT_TOOL],
                          [{"name": "extract_task_spec",
                            "arguments": {"path": f, "symbol": s, "action": action}}]))
    rng.shuffle(out)
    return out[:600]
manifest.append(write("extraction", ext_examples(), [EXT_TOOL]))

# ─── 7. abstention ───────────────────────────────────────────────────────────
ABST_TOOLS = DEV_TOOLS[:3] + [tool("abstain", "Call when no available tool fits the request.",
                                   {"reason": ("string", "One of: out_of_domain, ambiguous.")})]
OOD = ["book me a flight to Lisbon", "what's a good pasta recipe?", "tell me a joke",
       "who won the game last night?", "translate this to French: hello",
       "what's the capital of Peru?", "play some jazz", "order more coffee pods",
       "how tall is Mount Fuji?", "set an alarm for 7am", "what should I name my cat?",
       "summarize the news today", "is it going to snow?", "call mom", "buy AAPL stock"]
AMB = ["do the thing", "handle it", "you know what to do", "make it better", "fix everything",
       "sort it out", "deal with this", "improve stuff", "the usual", "go"]
def abst_examples():
    out = []
    for q in OOD:
        for p in ["", "hey, ", "quick one: ", "please "]:
            for s in ["", " thanks", "!"]:
                out.append(ex(p + q + s, ABST_TOOLS,
                              [{"name": "abstain", "arguments": {"reason": "out_of_domain"}}]))
    for q in AMB:
        for p in ["", "ok ", "now ", "just "]:
            out.append(ex(p + q, ABST_TOOLS,
                          [{"name": "abstain", "arguments": {"reason": "ambiguous"}}]))
    # in-domain positives so it can't learn "always abstain"
    for s, f in itertools.product(SYMS[:8], FILES[:6]):
        out.append(ex(f"show me {f}", ABST_TOOLS, [{"name": "read_file", "arguments": {"path": f}}]))
        out.append(ex(f"where is {s} defined?", ABST_TOOLS,
                      [{"name": "search_code", "arguments": {"pattern": s}}]))
        out.append(ex(f"in {f} swap {s} for {s}X", ABST_TOOLS,
                      [{"name": "edit_file", "arguments": {"path": f, "find": s, "replace": s + "X"}}]))
    return out
manifest.append(write("abstention", abst_examples(), ABST_TOOLS))

with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
for m in manifest:
    print(f"{m['family']:<20} n={m['n']:>5}  catalog={m['catalog_hash']}")
