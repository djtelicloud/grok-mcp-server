"""$0 static fast-gate between compile() and the sandbox stages.

compile() accepts code with undefined names (NameError is a runtime error),
so an LLM hallucination like a misspelled variable survives compilation and
burns seconds of sandbox time before the tests kill it. ruff's
pyflakes-derived F821/F823 checks catch exactly that class statically in
tens of milliseconds via stdin, for $0 — a strict cheapest-filter-first win.

Deliberately NOT style linting: style rules would cull mutant diversity for
no objective gain, so the rule selection is correctness-only. The gate is
BASELINE-RELATIVE — a target file that already trips F-rules (wildcard
imports, dynamic names) must not have every mutant killed — and it degrades
to a no-op when ruff is unavailable or errors: the tests stage still catches
everything this gate would have, so it only ever saves time, never decides
alone.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

# Correctness-only pyflakes rules: undefined name / local used before binding.
_RUFF_RULES = "F821,F823"


def ruff_bin() -> Optional[str]:
    """The venv's ruff first (a pinned project dependency), PATH second."""
    sibling = Path(sys.executable).parent / "ruff"
    if sibling.exists():
        return str(sibling)
    return shutil.which("ruff")


ViolationCounts = Counter[tuple[str, str]]


async def violation_counts(
    source: bytes, timeout: float = 10.0
) -> Optional[ViolationCounts]:
    """F821/F823 diagnostics keyed by rule and message, or None when the gate cannot
    run (ruff missing, timeout, or internal error) — callers must treat None
    as gate-disabled, never as clean."""
    binary = ruff_bin()
    if not binary:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "check",
            "--select", _RUFF_RULES,
            "--isolated",
            "--ignore-noqa",
            "--output-format", "json",
            "--stdin-filename", "candidate.py",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return None
    try:
        out, _err = await asyncio.wait_for(proc.communicate(source), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    # ruff: 0 = clean, 1 = violations found, anything else = tool error.
    if proc.returncode not in (0, 1):
        return None
    try:
        findings = json.loads(out.decode("utf-8", errors="replace") or "[]")
    except ValueError:
        return None
    if not isinstance(findings, list):
        return None
    counts: ViolationCounts = Counter()
    for finding in findings:
        if not isinstance(finding, dict):
            return None
        code = finding.get("code")
        message = finding.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            return None
        counts[(code, message)] += 1
    return counts


async def count_violations(source: bytes, timeout: float = 10.0) -> Optional[int]:
    """Compatibility helper returning the total F821/F823 diagnostic count."""
    counts = await violation_counts(source, timeout=timeout)
    return sum(counts.values()) if counts is not None else None
