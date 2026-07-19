"""A0′ task-class assignment + literal acceptance extraction.

Strength chain (not a random partition):
  literal ⊂ echo_ok ⊂ receipt ⊂ substantial ⊂ adversarial

Runtime assigns a class at verify time from task/acceptance text.
Higher-class essay gates must not fire on literal/echo_ok missions.
Adversarial and dual-intent work cues outrank bare token extraction.
"""

from __future__ import annotations

import os
import re
from typing import Literal

TaskClass = Literal["literal", "echo_ok", "receipt", "substantial", "adversarial"]

TASK_CLASSES: tuple[TaskClass, ...] = (
    "literal",
    "echo_ok",
    "receipt",
    "substantial",
    "adversarial",
)

# Acceptance-framed exact asks only — never bare API-docs "return X".
# Allow "exactly:" / "exactly :" before the token (common instruction punctuation).
_EXACTLY_GAP = r"(?:exactly\s*:?\s+)"
_REPLY_WITH = re.compile(
    r"(?is)(?:reply|respond|answer)\s+with\s+(?:" + _EXACTLY_GAP + r")?"
    r"(?:the\s+(?:string|token|text|word)\s+)?"
    r"[\"'`]?(?P<tok>[A-Za-z0-9][A-Za-z0-9_./+-]{0,127})[\"'`]?"
)
_RETURN_EXACTLY = re.compile(
    r"(?is)(?:return|output|print|say)\s+" + _EXACTLY_GAP +
    r"(?:the\s+(?:string|token|text|word)\s+)?"
    r"[\"'`]?(?P<tok>[A-Za-z0-9][A-Za-z0-9_./+-]{0,127})[\"'`]?"
)
_EXACTLY_TOKEN = re.compile(
    r"(?is)\b" + _EXACTLY_GAP +
    r"[\"'`]?(?P<tok>[A-Za-z0-9][A-Za-z0-9_./+-]{0,127})[\"'`]?"
)
_EXACT_PHRASE = _REPLY_WITH  # alias for dual-intent strip / echo_ok detect
_STRUCTURE = re.compile(
    r"(?is)\b(checklist|steps|bullet|evidence|artifact|diff|patch)\b"
)
_ADVERSARIAL = re.compile(
    r"(?is)\b(adversarial|no-?ship|security\s+review|threat\s+model|"
    r"self[- ]verif|red[- ]team)\b"
)
_RECEIPT = re.compile(
    r"(?is)\b(receipt|job_id|continue_token|digest|hash|telemetry_id)\b"
)
# Dual-intent: real work plus a closing token must stay substantial.
_WORK_CUE = re.compile(
    r"(?is)\b(then|after|before|also|apply|implement|fix|build|write|"
    r"create|deploy|patch|migrate|refactor|investigate)\b"
)


def task_class_enabled() -> bool:
    return os.environ.get("UNIGROK_TASK_CLASS", "true").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def literal_verify_enabled() -> bool:
    return os.environ.get("UNIGROK_VERIFY_LITERAL", "true").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def demands_structure(text: str) -> bool:
    return bool(_STRUCTURE.search(text or ""))


def _explicit_exact_phrase(text: str) -> re.Match[str] | None:
    return (
        _REPLY_WITH.search(text or "")
        or _RETURN_EXACTLY.search(text or "")
        or _EXACTLY_TOKEN.search(text or "")
    )


def extract_literal_acceptance(*texts: str) -> str | None:
    """Best-effort expected token for literal/echo_ok asks. None if unclear."""
    for text in texts:
        cleaned = (text or "").strip()
        if not cleaned or demands_structure(cleaned):
            continue
        # Prefer explicit acceptance framing — not incidental quotes or "return X".
        for pattern in (_REPLY_WITH, _RETURN_EXACTLY, _EXACTLY_TOKEN):
            match = pattern.search(cleaned)
            if not match:
                continue
            token = _normalize_extracted_token(match.group("tok"))
            if _usable_literal_token(token, cleaned):
                return token
        # Whole acceptance is a single closed-form probe token.
        words = cleaned.split()
        if len(words) == 1:
            token = _normalize_extracted_token(words[0])
            if _usable_literal_token(token, cleaned):
                return token
    return None


def _normalize_extracted_token(raw: str | None) -> str:
    token = (raw or "").strip().strip("\"'`")
    # Drop instruction punctuation accidentally glued on ("exactly:", "OK.")
    return token.strip(":.,;")


def _usable_literal_token(token: str, context: str) -> bool:
    token = _normalize_extracted_token(token)
    if not token or len(token) > 128:
        return False
    # Avoid treating ordinary English instruction words as expected answers.
    stop = {
        "a",
        "an",
        "the",
        "with",
        "exactly",
        "reply",
        "respond",
        "answer",
        "return",
        "output",
        "print",
        "say",
        "string",
        "token",
        "text",
        "word",
        "yes",
        "no",
        "ok",
        "true",
        "false",
    }
    low = token.lower()
    if low in stop:
        return False
    # Prefer status-like tokens; still allow mixed identifiers when phrase-marked.
    if token.isupper() or "_" in token or any(ch.isdigit() for ch in token):
        return True
    if _explicit_exact_phrase(context):
        return len(token) >= 3
    return False


def matches_literal(candidate: str, expected: str) -> bool:
    left = _normalize_extracted_token(candidate)
    right = _normalize_extracted_token(expected)
    return bool(left) and bool(right) and left == right


def _dual_intent_work(text: str) -> bool:
    """True when acceptance asks for work beyond emitting a token."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    # Strip the exact-phrase span so "reply with exactly X" alone is not work.
    stripped = _REPLY_WITH.sub(" ", cleaned)
    stripped = _RETURN_EXACTLY.sub(" ", stripped)
    stripped = _EXACTLY_TOKEN.sub(" ", stripped)
    if _WORK_CUE.search(stripped):
        return True
    # Long multi-clause asks with a trailing token stay substantial.
    if len(stripped.split()) >= 8:
        return True
    return False


def assign_task_class(task: str = "", acceptance: str = "") -> TaskClass:
    """Deterministic class for verify generators. Legacy when TASK_CLASS=off."""
    if not task_class_enabled():
        return "substantial"
    acceptance_clean = (acceptance or "").strip()
    task_clean = (task or "").strip()
    combined = f"{task_clean}\n{acceptance_clean}".strip()
    if not combined:
        return "substantial"
    # Precedence: structure / adversarial outrank literal short-circuit.
    if demands_structure(acceptance_clean) or demands_structure(task_clean):
        if _ADVERSARIAL.search(combined):
            return "adversarial"
        return "substantial"
    if _ADVERSARIAL.search(combined):
        return "adversarial"
    token = extract_literal_acceptance(acceptance_clean, task_clean)
    if token:
        if _dual_intent_work(acceptance_clean) or _dual_intent_work(task_clean):
            return "substantial"
        # "reply with X" without "exactly" → echo_ok (same CommitDone path).
        if _EXACTLY_TOKEN.search(combined) or _RETURN_EXACTLY.search(combined) or re.search(
            r"(?is)\bexactly\b", combined
        ):
            return "literal"
        if _REPLY_WITH.search(combined) or len(acceptance_clean.split()) <= 3:
            return "echo_ok"
        return "literal"
    if _RECEIPT.search(combined):
        return "receipt"
    return "substantial"


def literal_commit_ready(
    *,
    task: str = "",
    acceptance: str = "",
    candidate: str = "",
) -> tuple[bool, str | None, TaskClass]:
    """Return (matched, expected_token, class) for A0 short-circuit."""
    klass = assign_task_class(task, acceptance)
    if not literal_verify_enabled():
        return False, None, klass
    if klass not in {"literal", "echo_ok"}:
        return False, None, klass
    expected = extract_literal_acceptance(acceptance, task)
    if not expected:
        return False, None, klass
    return matches_literal(candidate, expected), expected, klass
