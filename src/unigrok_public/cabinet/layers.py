"""L0/L1 information bottleneck + freshness gate. Zero-LLM always defined."""

from __future__ import annotations

import math
import re

TOKEN_L0 = 100
TOKEN_L1 = 2000
FRESH_THETA = 3
FRESH_DELTA_S = 300
_TERM = re.compile(r"[A-Za-z0-9_]{2,}")
_SENTENCE = re.compile(r"[.!?]")


def estimate_tokens(text: str) -> int:
    words = [w for w in str(text or "").split() if w]
    if not words:
        return 0
    return max(1, math.ceil(len(words) * 1.3))


def clip_tokens(text: str, limit: int) -> str:
    words = str(text or "").split()
    if not words:
        return ""
    cap = max(1, int(limit))
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word])
        if kept and estimate_tokens(candidate) > cap:
            break
        kept.append(word)
        if estimate_tokens(" ".join(kept)) >= cap:
            break
    return " ".join(kept)


def _first_sentence(text: str) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    match = _SENTENCE.search(raw)
    if match is None:
        return raw
    return raw[: match.end()].strip()


def _terms(text: str, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in _TERM.findall(str(text or "").lower()):
        if match in seen:
            continue
        seen.add(match)
        out.append(match)
        if len(out) >= limit:
            break
    return out


def zero_llm_l0(text: str, *, limit: int = TOKEN_L0) -> str:
    sentence = _first_sentence(text)
    terms = _terms(text)
    if terms:
        extra = " ".join(terms)
        if extra.lower() not in sentence.lower():
            sentence = f"{sentence} [{extra}]" if sentence else extra
    return clip_tokens(sentence, limit)


def zero_llm_l1(
    title: str,
    child_abstracts: list[str],
    *,
    limit: int = TOKEN_L1,
) -> str:
    lines = [f"# {title.strip() or 'directory'}"]
    if child_abstracts:
        lines.append("## Children")
        for abstract in child_abstracts:
            clipped = clip_tokens(abstract, 40)
            if clipped:
                lines.append(f"- {clipped}")
    return clip_tokens("\n".join(lines), limit)


def should_refresh(
    pending_child_changes: int,
    age_seconds: float,
    *,
    theta: int = FRESH_THETA,
    delta_s: float = FRESH_DELTA_S,
) -> bool:
    """Bubble to grandparents only when pending or stale. Direct parent always refreshes."""
    if int(pending_child_changes) >= int(theta):
        return True
    return float(age_seconds) >= float(delta_s)
