# evals/graders.py
# Declarative grader evaluation for golden eval tasks.
#
# Grader specs (evals/tasks/*.json "graders" entries):
#   {"type": "contains",     "value": str, "case_sensitive": bool=false}
#   {"type": "not_contains", "value": str, "case_sensitive": bool=false}
#   {"type": "regex",        "pattern": str, "ignore_case": bool=false}
#   {"type": "structural",   "field": str, "equals": any | "gte": num | "lte": num}
#
# structural graders probe the runner's result record (route, model, plane,
# escalated, finish_reason, cost_usd, citations_count, tool_calls_count,
# appends_before_first_sample, ...). The special values "$planning" and
# "$coding" in `equals` resolve to the result's planning_model/coding_model
# fields so task JSON never hardcodes model slugs.

import re
from typing import Any, Dict, List, Tuple

_ALIAS_FIELDS = {"$planning": "planning_model", "$coding": "coding_model"}


def _resolve_alias(value: Any, result: Dict[str, Any]) -> Any:
    if isinstance(value, str) and value in _ALIAS_FIELDS:
        return result.get(_ALIAS_FIELDS[value])
    return value


def _normalize(value: Any) -> Any:
    """Comparison normalization: booleans compare as booleans (accepting
    "true"/"false" strings from JSON-authored specs), everything else as str."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def _grade_one(grader: Dict[str, Any], answer: str, result: Dict[str, Any]) -> Tuple[bool, str]:
    gtype = str(grader.get("type") or "").strip().lower()

    if gtype in ("contains", "not_contains"):
        needle = str(grader.get("value") or "")
        if not needle:
            return False, f"{gtype}: empty 'value'"
        haystack = answer if grader.get("case_sensitive") else answer.lower()
        target = needle if grader.get("case_sensitive") else needle.lower()
        found = target in haystack
        if gtype == "contains":
            return found, f"contains {needle!r}: {'found' if found else 'MISSING'}"
        return (not found), f"not_contains {needle!r}: {'absent' if not found else 'PRESENT'}"

    if gtype == "regex":
        pattern = str(grader.get("pattern") or "")
        if not pattern:
            return False, "regex: empty 'pattern'"
        flags = re.IGNORECASE if grader.get("ignore_case") else 0
        try:
            matched = re.search(pattern, answer, flags) is not None
        except re.error as exc:
            return False, f"regex {pattern!r}: invalid pattern ({exc})"
        return matched, f"regex {pattern!r}: {'matched' if matched else 'NO MATCH'}"

    if gtype == "structural":
        field = str(grader.get("field") or "")
        if not field:
            return False, "structural: empty 'field'"
        actual = result.get(field)
        if "equals" in grader:
            expected = _resolve_alias(grader["equals"], result)
            passed = _normalize(actual) == _normalize(expected)
            return passed, f"structural {field}={actual!r} (expected {expected!r})"
        for op, cmp_ok in (("gte", lambda a, e: a >= e), ("lte", lambda a, e: a <= e)):
            if op in grader:
                try:
                    passed = cmp_ok(float(actual), float(grader[op]))
                except (TypeError, ValueError):
                    return False, f"structural {field}={actual!r} not comparable ({op} {grader[op]})"
                return passed, f"structural {field}={actual!r} ({op} {grader[op]})"
        return False, f"structural {field}: no 'equals'/'gte'/'lte' clause"

    return False, f"unknown grader type {gtype!r}"


def run_graders(graders: List[Dict[str, Any]], answer: str,
                result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate every grader against the answer text + result record.
    Returns one {"type", "passed", "detail"} entry per grader; grader crashes
    are captured as failures, never propagated."""
    outcomes = []
    for grader in graders or []:
        try:
            passed, detail = _grade_one(grader, answer or "", result or {})
        except Exception as exc:  # noqa: BLE001 — a broken grader is a failed grader
            passed, detail = False, f"grader error: {type(exc).__name__}: {exc}"
        outcomes.append({
            "type": str(grader.get("type") or "unknown"),
            "passed": bool(passed),
            "detail": detail,
        })
    return outcomes
