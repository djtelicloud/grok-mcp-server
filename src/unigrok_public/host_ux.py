"""Host-facing UX helpers for cream layout, poll contract, soft-continue, chat short-circuit.

Public product only. No private intelligence. Pure functions + light heuristics.
"""

from __future__ import annotations

import re
from typing import Any

_CODE_FENCE_RE = re.compile(r"```[\w+-]*\n[\s\S]*?```", re.MULTILINE)
_DIFF_RE = re.compile(
    r"(?m)^(diff --git |@@ |--- a/|\+\+\+ b/|\+[\w].*|Index: )"
)
_ARTIFACT_RE = re.compile(
    r"(?is)(?:^|\n)##\s*ARTIFACT\b[\s\S]*?(?=\n##\s+\w|\Z)"
)
_SOFT_STOP_MARKERS = (
    "maxturn",
    "max_turns",
    "maxturns",
    "length",
    "toolbudget",
    "tool_budget",
    "incomplete",
    "budgetslice",
    "budget_slice",
    "truncated",
)
_ENGINEERING_MARKERS = re.compile(
    r"\b("
    r"implement|refactor|write\s+(?:a\s+)?(?:file|module|function|class|test)|"
    r"create\s+(?:a\s+)?(?:pr|pull\s+request|branch)|edit\s+the|fix\s+the\s+bug|"
    r"add\s+tests?|migrate|deploy|debug\s+this|patch|commit|push|"
    r"search\s+the\s+(?:web|codebase)|browse|scrape|run\s+(?:the\s+)?(?:tests?|ci)|"
    r"use\s+tools?|tool\s+call"
    r")\b",
    re.I,
)
_QA_MARKERS = re.compile(
    r"(?i)^(?:"
    r"what|why|how|when|where|who|which|is|are|can|could|should|does|do|"
    r"explain|define|summarize|compare|list|describe|tell\s+me"
    r")\b|[?？]\s*$"
)


def cream_first_layout(body: str) -> str:
    """Reorder body so code/diff/ARTIFACT cream leads; supporting prose follows.

    Hosts that only show the top of ``text`` get shippable cream first (rank 4).
    Idempotent when cream is already leading.
    """
    text = str(body or "").strip()
    if not text:
        return text
    blocks: list[str] = []
    # Prefer explicit ARTIFACT sections first.
    for match in _ARTIFACT_RE.finditer(text):
        chunk = match.group(0).strip()
        if chunk and chunk not in blocks:
            blocks.append(chunk)
    # Then fenced code.
    for match in _CODE_FENCE_RE.finditer(text):
        chunk = match.group(0).strip()
        if chunk and chunk not in blocks:
            blocks.append(chunk)
    # Diff-ish lines as a block if present and no fences captured.
    if not blocks and _DIFF_RE.search(text):
        lines = text.splitlines()
        diff_lines = [ln for ln in lines if _DIFF_RE.match(ln) or ln.startswith(("+", "-", " "))]
        if len(diff_lines) >= 3:
            blocks.append("\n".join(diff_lines).strip())
    if not blocks:
        return text
    remainder = text
    for chunk in blocks:
        remainder = remainder.replace(chunk, "\n", 1)
    remainder = re.sub(r"\n{3,}", "\n\n", remainder).strip()
    cream = "\n\n".join(blocks).strip()
    if not remainder or remainder == cream:
        return cream
    # Avoid double layout if cream already prefix.
    if text.startswith(cream[: min(40, len(cream))]):
        return text
    return f"{cream}\n\n---\n{remainder}"


def poll_contract(
    job_id: str,
    *,
    kind: str = "agent",
    wait_seconds: int = 16,
    max_polls_hint: int = 30,
) -> dict[str, Any]:
    """Structured auto-poll contract for pending hosts (rank 6)."""
    jid = str(job_id or "").strip().lower()
    wait = max(1, min(int(wait_seconds), 60))
    return {
        "tool": "agent_result",
        "job_id": jid,
        "wait_seconds": wait,
        "max_polls_hint": max(1, min(int(max_polls_hint), 100)),
        "job_kind": kind,
        "host_instructions": (
            f"While status is pending, call agent_result(job_id={jid!r}, "
            f"wait_seconds={wait}) and repeat until status is complete, continue, "
            "error, or lost. Do not re-fire the original tool for the same work."
        ),
    }


def attach_poll_contract(
    envelope: dict[str, Any],
    job_id: str,
    *,
    kind: str = "agent",
    wait_seconds: int = 16,
) -> dict[str, Any]:
    """Ensure pending/continue envelopes expose a machine-readable poll contract."""
    if not isinstance(envelope, dict):
        return envelope
    contract = poll_contract(job_id, kind=kind, wait_seconds=wait_seconds)
    envelope["poll"] = {
        "tool": contract["tool"],
        "job_id": contract["job_id"],
        "wait_seconds": contract["wait_seconds"],
    }
    envelope["poll_contract"] = contract
    return envelope


def looks_like_pure_qa(task: str, *, max_chars: int = 480) -> bool:
    """True when the task is short Q&A with no engineering/tool verbs (rank 7)."""
    text = str(task or "").strip()
    if not text or len(text) > max_chars:
        return False
    if _ENGINEERING_MARKERS.search(text):
        return False
    # Multi-step numbered engineering plans → not pure QA.
    if re.search(r"(?m)^\s*(?:\d+[\).]|[-*]\s+).{20,}", text) and len(text) > 200:
        return False
    return bool(_QA_MARKERS.search(text))


def _normalize_stop(stop_reason: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(stop_reason or "").casefold())


def should_soft_continue(result: dict[str, Any] | None) -> bool:
    """Whether a partial result should soft-continue instead of hard-stopping (rank 5)."""
    if not isinstance(result, dict):
        return False
    cream = str(
        result.get("proposed_text")
        or result.get("text")
        or result.get("review")
        or ""
    ).strip()
    if not cream:
        return False
    if result.get("tool_budget_exhausted") or result.get("partial"):
        return True
    stop = _normalize_stop(result.get("stop_reason"))
    if any(marker in stop for marker in _SOFT_STOP_MARKERS):
        return True
    # Explicit incomplete flags from harness.
    if result.get("truncated") or result.get("incomplete"):
        return True
    return False


def soft_continue_status_note(result: dict[str, Any] | None = None) -> str:
    stop = ""
    if isinstance(result, dict):
        stop = str(result.get("stop_reason") or "").strip()
    reason = stop or "tool/turn budget"
    return (
        f"Soft-continue: partial cream sealed after {reason}. "
        "Re-invoke agent with continue_token to finish; do not treat as CommitDone."
    )


def apply_soft_continue_markers(result: dict[str, Any]) -> dict[str, Any]:
    """Mark a result for soft-continue sealing when partial cream exists."""
    if not isinstance(result, dict) or not should_soft_continue(result):
        return result
    out = dict(result)
    out["soft_continue"] = True
    out["partial"] = True
    out.setdefault("status_text", soft_continue_status_note(result))
    return out
