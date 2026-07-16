# Code Optimization Swarm Workflow

This pack instructs IDE agents (Codex, Cursor, Claude Code) on how to safely and effectively use UniGrok's internal swarm optimization principles to dramatically accelerate $O(N^2)$ loops, math functions, or memory-heavy python code.

## The Auto-Optimization Boundary

**Never automatically optimize generated code before returning it to the user.**
If the user asks a question via the public MCP, return the text/code immediately. Optimization swarms require executing untrusted code on the host to benchmark it, pull heavy dependencies (like `numpy`/`numba`), and add severe latency. 

**Code Optimization is an opt-in, agent-driven workflow.** Only execute the following steps when the user explicitly requests an optimization or speedup pass.

## The Agentic Workflow

When tasked with optimizing a function:

1. **Isolate the Hotspot:** Do not rewrite the function in-place in the project. Extract the slow function to a temporary scratchpad (e.g. `scratch/bench_target.py`).
2. **Generate Baselines:** Write a deterministic benchmark that measures the exact `latency_ms` and `peak_mem_bytes` using realistic sample data. Ensure tests prove feasibility.
3. **Agentic Refactoring:** Generate 3-5 distinct algorithmic or JIT-based variations. Common Python strategies include:
   - Algorithmic improvement (e.g., $O(N^2)$ to $O(N \log N)$ or unique pair mapping).
   - Numba `@njit` compilation (LLVM).
   - NumPy Vectorization.
   - Built-in `collections` or `itertools` C-bindings.
4. **Isolated Benchmark Execution:** Run the benchmark script inside an isolated environment (e.g., `uv run --with numpy --with numba python3 bench_target.py`) to prevent polluting the user's project dependencies.
5. **Evaluate the Pareto Front:**
   - **Constraint:** Drop any variation that fails correctness tests (`feasible = False`).
   - **Metrics:** Compare `latency_ms`, `peak_mem_bytes`, and `diff_bytes` (code complexity).
   - Present the surviving Pareto-optimal candidates to the user.
6. **Apply:** Once the user reviews the metrics and selects the winner, safely apply the changes back to the actual project file.

## Why capture multiple metrics?

If an agent is told simply to "make it faster", it will blindly trade memory and maintainability for speed. The optimization swarm must record:
- **Speedup:** The primary goal.
- **Memory Cost:** Did we allocate a 10GB array to save 20ms?
- **Complexity Cost:** Did we replace 5 lines of readable Python with 500 lines of unsafe C-extensions?
