# First-file package — `fast_non_dominated_sort` (proper rerun)

**Status:** staged · **HOLD** until #476 Live + Forge relaunch + CONTINUE / sponsor go  
**Branch tip:** `cursor/python-superiority-plans-consolidate` @ `002fff8` (#475 draft)  
**Process:** ORIGINAL → team rewrite → TEAM_FINAL → swarm → SWARM_FINAL  
**Miss owned:** plans ≠ team rewrites ≠ measured wins

## Target

| Field | Value |
| --- | --- |
| File | `src/swarm/pareto.py` |
| Symbol | `fast_non_dominated_sort` |
| Span | ~L32–L61 |
| Oracle | existing `tests/test_swarm_pareto.py` |
| Import after #476 | `src.swarm.pareto` (workspace root on PYTHONPATH) |

## Why this file first

Codex pareto-first standing order. Small, pure, deterministic — proves the **pipeline** before touching large production modules (`utils`, `broker`, `http_server`, …).

## Steps when unlocked (do not start early)

1. **ORIGINAL** — run `scripts/superiority_baseline_pareto.py` → record LOC / parse / import / microbench / peak memory in tracker Rank 0.
2. **Team** — Forge hive/group-diff on the function → **rewrite Python in worktree** → re-run baseline → **TEAM_FINAL**.
3. **Swarm** — only after TEAM_FINAL; start_code_swarm / apply only if Codex exits dry_run; re-run baseline → **SWARM_FINAL**.
4. **Stop** — hand Codex the one measured candidate before expanding Rerun Queue v1 or re-triaging 133 skips.

## Smoke readiness (prep only)

```bash
# After #476 Live + Forge relaunch — prefer this import path:
cd .worktrees/cursor/python-superiority-loop
python -c "from src.swarm.pareto import fast_non_dominated_sort; print(fast_non_dominated_sort.__name__)"

# Baseline stub (ORIGINAL capture; safe anytime, does not rewrite):
python scripts/superiority_baseline_pareto.py
```

Full pytest suite not required for unlock. Targeted: `uv run pytest tests/test_swarm_pareto.py -q` after team rewrite.

## Out of scope while HOLD

- Mass file rewrites
- Swarm apply
- Treating 77 old plans as wins
- Publishing #408
- Un-skipping the 133 without Codex review of first e2e result
