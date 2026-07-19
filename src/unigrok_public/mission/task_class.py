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
VerificationMode = Literal["structural", "independent_evidence"]

TASK_CLASSES: tuple[TaskClass, ...] = (
    "literal",
    "echo_ok",
    "receipt",
    "substantial",
    "adversarial",
)

VERIFICATION_STRUCTURAL: VerificationMode = "structural"
VERIFICATION_INDEPENDENT: VerificationMode = "independent_evidence"
VERIFICATION_MODES: tuple[VerificationMode, ...] = (
    VERIFICATION_STRUCTURAL,
    VERIFICATION_INDEPENDENT,
)

# Acceptance-framed exact asks only — never bare API-docs "return X" or
# incidental prose such as "this is exactly correct".
# Allow "exactly:" / "exactly :" before the token (common instruction punctuation).
_EXACTLY_GAP = r"exactly\s*:?\s+"
_REPLY_WITH = re.compile(
    r"(?is)\b(?:reply|respond|answer)\s+with\s+"
    r"(?P<exactly>" + _EXACTLY_GAP + r")?"
    r"(?:the\s+(?:string|token|text|word)\s+)?"
    r"[\"'`]?(?P<tok>[A-Za-z0-9][A-Za-z0-9_./+-]{0,127})"
    r"(?![A-Za-z0-9_./+-])[\"'`]?"
)
_RETURN_EXACTLY = re.compile(
    r"(?is)\b(?:return|output|print|say)\s+" + _EXACTLY_GAP +
    r"(?:the\s+(?:string|token|text|word)\s+)?"
    r"[\"'`]?(?P<tok>[A-Za-z0-9][A-Za-z0-9_./+-]{0,127})"
    r"(?![A-Za-z0-9_./+-])[\"'`]?"
)
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

# CommitDone should require an observation external to the candidate only when
# the caller asks for a real-world outcome. Mere subject-matter mentions (for
# example, "explain runtime architecture" or "write unit tests") remain
# structurally verifiable output requests.
_INFORMATIONAL_PROOF = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:explain|describe|summarize|outline|draft)\b"
    r".{0,100}\b(?:how|plan|strategy|steps|guide)\b.{0,100}"
    r"\b(?:prove|verify|validate|confirm|demonstrate|certify)\b"
)
_PROOF_ACTION = re.compile(
    r"(?is)\b(?:prove|verify|validate|confirm|demonstrate|certify)\b"
)
_TEST_OUTCOME = re.compile(
    r"(?is)(?:"
    r"\b(?:run|execute|rerun|perform)\b.{0,80}\b(?:tests?|test[- ]suite|benchmark)\b|"
    r"\b(?:tests?|test[- ]suite|benchmark)\b.{0,80}"
    r"\b(?:pass(?:ed|ing)?|succeed(?:ed|ing)?|green|result|evidence)\b"
    r")"
)
_RUNTIME_OUTCOME = re.compile(
    r"(?is)\b(?:runtime|live|production|deployed?)\b.{0,80}"
    r"\b(?:observation|evidence|result|check|verification|passed?|healthy|works?)\b"
)
_EXPLICIT_EVIDENCE = re.compile(
    r"(?is)\b(?:provide|include|attach|supply|cite|show)\b.{0,80}"
    r"\b(?:independent\s+)?(?:evidence|proof|test\s+results?|runtime\s+logs?)\b"
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
    for reply in _REPLY_WITH.finditer(text or ""):
        if reply.group("exactly"):
            return reply
    return _RETURN_EXACTLY.search(text or "")


def extract_literal_acceptance(*texts: str) -> str | None:
    """Best-effort expected token for literal/echo_ok asks. None if unclear."""
    for text in texts:
        cleaned = (text or "").strip()
        if not cleaned or demands_structure(cleaned):
            continue
        # Prefer explicit acceptance framing — not incidental quotes or "return X".
        for pattern in (_REPLY_WITH, _RETURN_EXACTLY):
            for match in pattern.finditer(cleaned):
                token = _normalize_extracted_token(match.group("tok"))
                if _usable_literal_token(token, command_framed=True):
                    return token
        # Whole acceptance is a single closed-form probe token.
        words = cleaned.split()
        if len(words) == 1:
            token = _normalize_extracted_token(words[0])
            if _usable_literal_token(token):
                return token
    return None


def _normalize_extracted_token(raw: str | None) -> str:
    token = (raw or "").strip().strip("\"'`")
    # Drop instruction punctuation accidentally glued on ("exactly:", "OK.")
    return token.strip(":.,;")


def _usable_literal_token(token: str, *, command_framed: bool = False) -> bool:
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
    }
    low = token.lower()
    if low in stop:
        return False
    if command_framed:
        return True
    # Prefer status-like tokens; still allow mixed identifiers when phrase-marked.
    if token.isupper() or "_" in token or any(ch.isdigit() for ch in token):
        return True
    return False


def matches_literal(candidate: str, expected: str) -> bool:
    left = (candidate or "").strip()
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
        if _explicit_exact_phrase(combined):
            return "literal"
        if _REPLY_WITH.search(combined) or len(acceptance_clean.split()) <= 3:
            return "echo_ok"
        return "literal"
    if _RECEIPT.search(combined):
        return "receipt"
    return "substantial"


def assign_verification_mode(
    task: str = "",
    acceptance: str = "",
    *,
    assigned_class: TaskClass | None = None,
    destructive: bool = False,
) -> VerificationMode:
    """Freeze whether CommitDone needs evidence external to the candidate.

    This is intentionally narrower than task classification. Ordinary prose and
    generated artifacts can be checked deterministically for shape and coverage.
    Adversarial work, destructive work, and explicit claims about tests, proof,
    or live/runtime outcomes require a caller, runtime, or human record.
    """
    if destructive:
        return VERIFICATION_INDEPENDENT

    combined = f"{task or ''}\n{acceptance or ''}".strip()
    if not combined:
        return VERIFICATION_STRUCTURAL
    if _EXPLICIT_EVIDENCE.search(combined):
        return VERIFICATION_INDEPENDENT
    if _TEST_OUTCOME.search(combined) or _RUNTIME_OUTCOME.search(combined):
        return VERIFICATION_INDEPENDENT
    # "Explain how to verify" is an informational deliverable, not a claim that
    # verification occurred. An imperative verification request is an outcome.
    if _PROOF_ACTION.search(combined) and not _INFORMATIONAL_PROOF.search(combined):
        return VERIFICATION_INDEPENDENT
    return VERIFICATION_STRUCTURAL


def literal_commit_ready(
    *,
    task: str = "",
    acceptance: str = "",
    candidate: str = "",
    assigned_class: TaskClass | None = None,
) -> tuple[bool, str | None, TaskClass]:
    """Return (matched, expected_token, class) for A0 short-circuit."""
    klass = (
        assigned_class
        if assigned_class in TASK_CLASSES
        else assign_task_class(task, acceptance)
    )
    if not literal_verify_enabled():
        return False, None, klass
    if klass not in {"literal", "echo_ok"}:
        return False, None, klass
    expected = extract_literal_acceptance(acceptance, task)
    if not expected:
        return False, None, klass
    return matches_literal(candidate, expected), expected, klass
