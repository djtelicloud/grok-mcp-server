"""A/B benchmark runner for the j-space deep harness on the public UniGrok gateway.

Runs a deterministic task set through the `agent` tool at depth "auto" and "deep",
polls `agent_result` until each job completes, machine-checks every answer, and
records the verified outcome with `record_benchmark_result`. Prints a comparison
table of tokens, latency, cost, plane, and pass/fail per run.

Usage:
    python scripts/benchmark_deep.py                       # both depths, all tasks
    python scripts/benchmark_deep.py --depth deep          # one depth
    python scripts/benchmark_deep.py --task grid_60        # one task
    python scripts/benchmark_deep.py --endpoint http://localhost:4775/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

POLL_SECONDS = 16
MAX_POLLS = 30


def _check_grid_60(text: str) -> tuple[bool, str]:
    """The 4x4 constrained max-sum grid; unique optimum is 60 via 5,8,12,1,15,8,11."""
    if not re.search(r"\b60\b", text):
        return False, "did not report max sum 60"
    if re.search(r"maximum sum[^0-9]{0,20}\b(?!60\b)(\d{2})\b", text, re.I):
        claimed = re.search(r"maximum sum[^0-9]{0,20}(\d{2,3})", text, re.I)
        if claimed and claimed.group(1) != "60":
            return False, f"claimed max sum {claimed.group(1)}, expected 60"
    needed = ["5", "8", "12", "1", "15", "8", "11"]
    values_line = re.search(r"5\D+8\D+12\D+1\D+15\D+8\D+11", text)
    if not values_line:
        return False, f"optimal value sequence {needed} not found"
    return True, "sum 60 with correct value sequence"


def _check_digit_sum(text: str) -> tuple[bool, str]:
    """Sum of digits of 2^64 = 18446744073709551616 -> 88."""
    if re.search(r"\b88\b", text):
        return True, "digit sum 88 reported"
    return False, "expected digit sum 88"


def _check_lcm(text: str) -> tuple[bool, str]:
    """lcm(1..20) = 232792560."""
    if "232792560" in text.replace(",", "").replace(" ", ""):
        return True, "lcm 232792560 reported"
    return False, "expected lcm(1..20) = 232792560"


def _check_lis_code(text: str) -> tuple[bool, str]:
    """Extract the generated function and actually run it against test cases."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = match.group(1) if match else text
    if "def lis(" not in code:
        return False, "no `def lis(` function found in reply"
    tests = (
        "\nassert lis([]) == 0"
        "\nassert lis([7]) == 1"
        "\nassert lis([10, 9, 2, 5, 3, 7, 101, 18]) == 4"
        "\nassert lis([0, 1, 0, 3, 2, 3]) == 4"
        "\nassert lis([7, 7, 7, 7]) == 1"
        "\nassert lis(list(range(2000))) == 2000"
        "\nprint('LIS_TESTS_PASS')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(code + tests)
        script = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return False, "generated code timed out (not O(n log n)?)"
    if "LIS_TESTS_PASS" in proc.stdout:
        return True, "generated lis() passed all executed tests"
    return False, f"tests failed: {(proc.stderr or proc.stdout)[-120:]}"


def _check_median_code(text: str) -> tuple[bool, str]:
    """Median of two sorted arrays in O(log(m+n)) — an edge-case minefield."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = match.group(1) if match else text
    if "def median_two_sorted(" not in code:
        return False, "no `def median_two_sorted(` found"
    tests = (
        "\nassert median_two_sorted([1,3],[2]) == 2"
        "\nassert median_two_sorted([1,2],[3,4]) == 2.5"
        "\nassert median_two_sorted([],[1]) == 1"
        "\nassert median_two_sorted([2],[]) == 2"
        "\nassert median_two_sorted([1,1],[1,1]) == 1"
        "\nassert median_two_sorted([1,3,5,7,9],[2,4,6,8,10,12]) == 6"
        "\nassert median_two_sorted([0,0],[0,0]) == 0"
        "\nassert median_two_sorted([1,2,3,4,5,6,7,8],[9,10]) == 5.5"
        "\nprint('MEDIAN_TESTS_PASS')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(code + tests)
        script = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return False, "generated code timed out"
    if "MEDIAN_TESTS_PASS" in proc.stdout:
        return True, "generated median_two_sorted() passed all executed tests"
    return False, f"tests failed: {(proc.stderr or proc.stdout)[-120:]}"


def _check_automorphic(text: str) -> tuple[bool, str]:
    """Hive-authored, brute-force-verified ground truth: 14286."""
    if re.search(r"\b14286\b", text.replace(",", "")):
        return True, "correct count 14286"
    return False, "expected 14286"


def _check_digit_sum_mod4(text: str) -> tuple[bool, str]:
    """Hive-authored, brute-force-verified ground truth: 1871."""
    if re.search(r"\b1871\b", text.replace(",", "")):
        return True, "correct count 1871"
    return False, "expected 1871"


def _check_palindrome_code(text: str) -> tuple[bool, str]:
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = match.group(1) if match else text
    if "def next_palindrome(" not in code:
        return False, "no `def next_palindrome(` found"
    tests = (
        "\nassert next_palindrome(0) == 1"
        "\nassert next_palindrome(9) == 11"
        "\nassert next_palindrome(10) == 11"
        "\nassert next_palindrome(11) == 22"
        "\nassert next_palindrome(99) == 101"
        "\nassert next_palindrome(100) == 101"
        "\nassert next_palindrome(123) == 131"
        "\nassert next_palindrome(999) == 1001"
        "\nassert next_palindrome(1299) == 1331"
        "\nassert next_palindrome(1999) == 2002"
        "\nassert next_palindrome(9999) == 10001"
        "\nassert next_palindrome(12321) == 12421"
        "\nassert next_palindrome(123321) == 124421"
        "\nassert next_palindrome(100001) == 101101"
        "\nprint('PAL_TESTS_PASS')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(code + tests)
        script = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return False, "generated code timed out"
    if "PAL_TESTS_PASS" in proc.stdout:
        return True, "next_palindrome passed all executed tests"
    return False, f"tests failed: {(proc.stderr or proc.stdout)[-120:]}"


@dataclass(frozen=True)
class Task:
    key: str
    prompt: str
    check: Callable[[str], tuple[bool, str]]


TASKS: list[Task] = [
    Task(
        key="grid_60",
        prompt=(
            "Solve this logic and math grid puzzle:\n"
            "You have a 4x4 grid of numbers:\n"
            "[[5, 8, 2, 9],\n [3, 12, 4, 7],\n [6, 1, 15, 2],\n [9, 5, 8, 11]]\n\n"
            "Find the path from the top-left (row 0, col 0 = 5) to the bottom-right "
            "(row 3, col 3 = 11) that maximizes the sum of the visited cells.\n"
            "Movement rules: You can only move Right, Down, or diagonally Down-Right.\n"
            "Constraints:\n"
            "- You CANNOT visit two prime numbers (2, 3, 5, 7, 11) in a row.\n"
            "- You CANNOT visit three even numbers (2, 4, 6, 8, 12) in a row.\n\n"
            "Provide the optimal path coordinates, the values, the maximum sum, and "
            "verify every step against the constraints."
        ),
        check=_check_grid_60,
    ),
    Task(
        key="digit_sum_2_64",
        prompt=(
            "Compute 2 to the power of 64 exactly, then compute the sum of the decimal "
            "digits of that number. Reply with the power and the digit sum. Do not use "
            "any tools; reason it out."
        ),
        check=_check_digit_sum,
    ),
    Task(
        key="lcm_1_20",
        prompt=(
            "Compute the least common multiple of the integers 1 through 20 exactly. "
            "Show the prime factorization you used and the final value. Do not use any "
            "tools; reason it out."
        ),
        check=_check_lcm,
    ),
    Task(
        key="lis_code",
        prompt=(
            "Write a single Python function `lis(nums: list[int]) -> int` returning "
            "the length of the longest strictly increasing subsequence in O(n log n). "
            "Handle empty lists and duplicates correctly. Output only one python code "
            "block with the function, no usage examples."
        ),
        check=_check_lis_code,
    ),
    Task(
        key="median_code",
        prompt=(
            "Write a single Python function "
            "`median_two_sorted(a: list[int], b: list[int]) -> float` returning the "
            "median of the two sorted input lists combined, running in "
            "O(log(min(len(a), len(b)))). Handle empty lists, odd/even totals, and "
            "duplicates. Output only one python code block with the function, no "
            "usage examples."
        ),
        check=_check_median_code,
    ),
    Task(
        key="automorphic_count",
        prompt=(
            "Count the integers k with 1 <= k <= 1000000 that satisfy ALL of the "
            "following simultaneously:\n"
            "(1) k is not divisible by 2 and not divisible by 5;\n"
            "(2) the last digit of k^2 equals the last digit of k;\n"
            "(3) (k // 100) is congruent to k (mod 7), where // is integer floor "
            "division.\n"
            "Return a single integer. Do not use any tools; reason it out."
        ),
        check=_check_automorphic,
    ),
    Task(
        key="digit_sum_mod4",
        prompt=(
            "How many integers N with 0 <= N <= 10000 have the sum of decimal digits "
            "of N divisible by 4, while N itself is NOT divisible by 4? The digit sum "
            "of 0 is 0. Return a single integer. Do not use any tools; reason it out."
        ),
        check=_check_digit_sum_mod4,
    ),
    Task(
        key="palindrome_code",
        prompt=(
            "Write a Python function next_palindrome(n) that returns the smallest "
            "integer strictly greater than n whose base-10 representation is a "
            "palindrome. n is an integer with n >= 0. Do not import libraries. "
            "Output only one python code block with the function, no usage examples."
        ),
        check=_check_palindrome_code,
    ),
]


def _payload(result: Any) -> dict[str, Any]:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def _total_tokens(payload: dict[str, Any]) -> int | None:
    usage = payload.get("usage") or {}
    for key in ("totalTokens", "total_tokens"):
        if isinstance(usage.get(key), (int, float)):
            return int(usage[key])
    return None


async def _run_one(
    session: ClientSession,
    task: Task,
    knob: str,
    *,
    use_level: bool = False,
    voters: int | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    if voters is not None:
        # Hive-variation sweep: fixed hive shape, vary only the voter count.
        args: dict[str, Any] = {"task": task.prompt, "depth": "hive", "voters": voters}
        knob = f"hive-v{voters}"
    else:
        args = {"task": task.prompt, ("level" if use_level else "depth"): knob}
    result = _payload(await session.call_tool("agent", args))
    while result.get("status") == "pending":
        job_id = result.get("job_id")
        if not job_id or (time.monotonic() - started) > POLL_SECONDS * MAX_POLLS:
            raise RuntimeError(f"{task.key}/{knob}: job polling exhausted")
        result = _payload(
            await session.call_tool(
                "agent_result", {"job_id": job_id, "wait_seconds": POLL_SECONDS}
            )
        )
    text = str(result.get("text") or "")
    passed, reason = task.check(text)
    telemetry_id = result.get("telemetry_id")
    label = ("level" if use_level else "depth") + "=" + knob
    if isinstance(telemetry_id, int):
        await session.call_tool(
            "record_benchmark_result",
            {
                "telemetry_id": telemetry_id,
                "success": passed,
                "note": f"benchmark_deep {task.key} {label}: {reason}",
            },
        )
    return {
        "task": task.key,
        "depth": knob,
        "passed": passed,
        "reason": reason,
        "telemetry_id": telemetry_id,
        "plane": result.get("resolved_plane"),
        "fallback": result.get("fallback_reason"),
        "tokens": _total_tokens(result),
        "latency_ms": result.get("elapsed_ms"),
        "cost_usd": result.get("cost_usd"),
        "requested_mode": result.get("requested_mode"),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:4775/mcp")
    parser.add_argument(
        "--depth", choices=["auto", "deep", "hive", "both", "all"], default="both"
    )
    parser.add_argument(
        "--levels",
        default=None,
        help="Comma list of ladder rungs to sweep instead of depths, "
        "e.g. none,low,high,max,ultra",
    )
    parser.add_argument(
        "--voters",
        default=None,
        help="Comma list of hive voter counts to sweep (depth=hive), e.g. 1,2,3,5",
    )
    parser.add_argument("--task", choices=[t.key for t in TASKS], default=None)
    args = parser.parse_args()

    voter_sweep = (
        [int(v.strip()) for v in args.voters.split(",") if v.strip()]
        if args.voters is not None
        else None
    )
    use_level = args.levels is not None
    if voter_sweep is not None:
        knobs = [f"hive-v{v}" for v in voter_sweep]
    elif use_level:
        knobs = [k.strip() for k in args.levels.split(",") if k.strip()]
    elif args.depth == "both":
        knobs = ["auto", "deep"]
    elif args.depth == "all":
        knobs = ["auto", "deep", "hive"]
    else:
        knobs = [args.depth]
    tasks = [t for t in TASKS if args.task is None or t.key == args.task]
    rows: list[dict[str, Any]] = []
    knob_label = "voters" if voter_sweep is not None else "level" if use_level else "depth"
    async with streamablehttp_client(args.endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for task in tasks:
                for index, knob in enumerate(knobs):
                    print(f"running {task.key} {knob_label}={knob} ...", flush=True)
                    try:
                        if voter_sweep is not None:
                            row = await _run_one(
                                session, task, knob, voters=voter_sweep[index]
                            )
                        else:
                            row = await _run_one(
                                session, task, knob, use_level=use_level
                            )
                    except Exception as exc:  # keep the suite going; report the failure
                        row = {
                            "task": task.key,
                            "depth": knob,
                            "passed": False,
                            "reason": f"runner error: {exc}",
                        }
                    rows.append(row)
                    print(f"  -> {json.dumps(row, default=str)}", flush=True)

    header = (
        f"{'task':<16}{'depth':<7}{'pass':<6}{'plane':<6}{'tokens':<9}"
        f"{'latency_ms':<12}{'cost_usd':<10}{'fallback':<18}{'telemetry':<10}"
    )
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.get('task', ''):<16}{row.get('depth', ''):<7}"
            f"{str(row.get('passed', '')):<6}{str(row.get('plane', '') or '-'):<6}"
            f"{str(row.get('tokens', '') or '-'):<9}"
            f"{str(row.get('latency_ms', '') or '-'):<12}"
            f"{str(row.get('cost_usd', '') or '0'):<10}"
            f"{str(row.get('fallback', '') or '-'):<18}"
            f"{str(row.get('telemetry_id', '') or '-'):<10}"
        )
    failed = [row for row in rows if not row.get("passed")]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
