"""Per-session context pack: inventory → persona votes → lead merge.

Context is a list. After each completed turn (when enabled), UniGrok rebuilds a
bounded pack of keeps + don'ts for the next turn instead of feeding the raw
append-only transcript dump.

Modes (UNIGROK_CONTEXT_PACK):
  off  — disabled (process default)
  cpu  — five heuristic persona votes + deterministic lead merge (Docker live)
  hive — reserved; falls back to cpu until a spend-capped hive voter lands

Never reads host IDE files. Only session messages + caller-supplied facts.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from unigrok_public.state import redact_secrets

PackMode = Literal["off", "cpu", "hive"]
Vote = Literal["retain", "drop", "pin_dont", "pin_keep"]

_MAX_KEEP = 12
_MAX_DONTS = 8
_MAX_ITEM_CHARS = 1_200
_MAX_PACK_CHARS = 24_000

_DONT = re.compile(
    r"(?is)\b(?:do\s+not|don't|never|avoid|must\s+not|cannot|can't|forbid|"
    r"without\s+(?:asking|changing)|no\s+force[- ]push)\b[^.!?\n]{0,200}"
)
_GOALISH = re.compile(
    r"(?is)\b(?:goal|must|need(?:s)?\s+to|should|require|acceptance|commit|"
    r"ship|fix|implement|literal|mission)\b"
)
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "into",
        "have",
        "been",
        "will",
        "when",
        "what",
        "which",
    }
)
_TERM = re.compile(r"[A-Za-z0-9_]{3,}")


@dataclass
class ContextItem:
    item_id: str
    kind: str
    text: str
    score: float = 0.0
    votes: dict[str, Vote] = field(default_factory=dict)
    decision: Vote = "drop"


@dataclass
class ContextPack:
    session: str
    version: int
    mode: str
    keeps: list[str]
    donts: list[str]
    dropped: int
    item_count: int
    lead_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ContextPack | None:
        if not isinstance(data, dict):
            return None
        keeps = [str(x) for x in (data.get("keeps") or []) if str(x).strip()]
        donts = [str(x) for x in (data.get("donts") or []) if str(x).strip()]
        return cls(
            session=str(data.get("session") or ""),
            version=int(data.get("version") or 1),
            mode=str(data.get("mode") or "cpu"),
            keeps=keeps,
            donts=donts,
            dropped=int(data.get("dropped") or 0),
            item_count=int(data.get("item_count") or 0),
            lead_notes=str(data.get("lead_notes") or ""),
        )


def context_pack_mode() -> PackMode:
    raw = os.environ.get("UNIGROK_CONTEXT_PACK", "off").strip().lower()
    if raw in {"0", "false", "off", "no", ""}:
        return "off"
    # hive reserved → cpu until spend-capped voter lands
    if raw in {"cpu", "hive", "on", "true", "1"}:
        return "cpu"
    return "off"


def _terms(text: str, *, limit: int = 32) -> set[str]:
    out: set[str] = set()
    for match in _TERM.findall((text or "").lower()):
        if match in _STOP:
            continue
        out.add(match)
        if len(out) >= limit:
            break
    return out


def _clip(text: str, *, limit: int = _MAX_ITEM_CHARS) -> str:
    clean = redact_secrets(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def _item_id(kind: str, text: str) -> str:
    digest = hashlib.sha256(f"{kind}\n{text}".encode()).hexdigest()[:16]
    return f"{kind}:{digest}"


def inventory(
    history: list[dict[str, Any]],
    *,
    facts: list[dict[str, Any]] | None = None,
    next_task: str = "",
) -> list[ContextItem]:
    """Turn session history + facts into a scored list (the 'diff')."""
    task_terms = _terms(next_task)
    items: list[ContextItem] = []
    for index, message in enumerate(history or []):
        role = str(message.get("role") or "user")
        text = _clip(str(message.get("content") or ""))
        if not text:
            continue
        kind = "turn_user" if role == "user" else "turn_assistant"
        overlap = len(_terms(text) & task_terms)
        recency = (index + 1) / max(1, len(history))
        score = 0.35 * recency + 0.45 * min(1.0, overlap / 4) + (
            0.2 if _GOALISH.search(text) else 0.0
        )
        items.append(
            ContextItem(item_id=_item_id(kind, text), kind=kind, text=text, score=score)
        )
        for match in _DONT.finditer(text):
            dont = _clip(match.group(0), limit=280)
            if len(dont) < 12:
                continue
            items.append(
                ContextItem(
                    item_id=_item_id("dont", dont),
                    kind="dont_candidate",
                    text=dont,
                    score=0.9 + 0.05 * min(1.0, overlap / 3),
                )
            )
    for fact in facts or []:
        body = _clip(str(fact.get("fact") or fact.get("content") or ""))
        if not body:
            continue
        overlap = len(_terms(body) & task_terms)
        items.append(
            ContextItem(
                item_id=_item_id("fact", body),
                kind="fact",
                text=body,
                score=0.5 + 0.4 * min(1.0, overlap / 3),
            )
        )
    return items


def _persona_votes(item: ContextItem, *, next_task: str) -> dict[str, Vote]:
    """Five cheap persona skins — same roles as hive, no metered spend."""
    text_l = item.text.lower()
    task_l = (next_task or "").lower()
    overlap = len(_terms(item.text) & _terms(next_task))
    critic: Vote = "retain" if item.score >= 0.45 or overlap else "drop"
    bounty: Vote = (
        "pin_dont"
        if item.kind == "dont_candidate"
        else ("pin_keep" if overlap >= 3 else critic)
    )
    spec: Vote = "retain" if _GOALISH.search(item.text) or overlap else "drop"
    failures: Vote = (
        "pin_dont"
        if item.kind == "dont_candidate" or "fail" in text_l or "never" in text_l
        else critic
    )
    complexity: Vote = "drop" if len(item.text) > 800 and overlap == 0 else critic
    # Prefer pinning constraints that still touch the next task.
    if item.kind == "dont_candidate" and (
        overlap or any(tok in text_l for tok in task_l.split()[:8] if len(tok) > 3)
    ):
        bounty = "pin_dont"
        failures = "pin_dont"
    return {
        "critic": critic,
        "bounty": bounty,
        "spec": spec,
        "failures": failures,
        "complexity": complexity,
    }


def _majority(votes: dict[str, Vote]) -> Vote:
    weight = {"pin_dont": 0, "pin_keep": 0, "retain": 0, "drop": 0}
    for vote in votes.values():
        weight[vote] = weight.get(vote, 0) + 1
    # Constraints win ties against drop.
    order: list[Vote] = ["pin_dont", "pin_keep", "retain", "drop"]
    return max(order, key=lambda key: (weight.get(key, 0), -order.index(key)))


def lead_merge(
    items: list[ContextItem],
    *,
    next_task: str,
    max_keep: int = _MAX_KEEP,
    max_donts: int = _MAX_DONTS,
) -> ContextPack:
    """Grok-lead stand-in: deterministic final call from persona votes + scores."""
    for item in items:
        item.votes = _persona_votes(item, next_task=next_task)
        item.decision = _majority(item.votes)

    donts: list[str] = []
    keeps: list[str] = []
    # Don'ts first — negative constraints are high-value and small.
    for item in sorted(items, key=lambda x: (-x.score, x.item_id)):
        if item.decision == "pin_dont" or (
            item.kind == "dont_candidate" and item.decision != "drop"
        ):
            if item.text not in donts and len(donts) < max_donts:
                donts.append(item.text)
    for item in sorted(items, key=lambda x: (-x.score, x.item_id)):
        if item.decision in {"pin_keep", "retain"} and item.kind != "dont_candidate":
            if item.text not in keeps and item.text not in donts and len(keeps) < max_keep:
                keeps.append(item.text)

    dropped = max(0, len(items) - len(keeps) - len(donts))
    notes = (
        f"lead_merge keeps={len(keeps)} donts={len(donts)} dropped={dropped} "
        f"task_terms={len(_terms(next_task))}"
    )
    return ContextPack(
        session="",
        version=1,
        mode="cpu",
        keeps=keeps,
        donts=donts,
        dropped=dropped,
        item_count=len(items),
        lead_notes=notes,
    )


def build_context_pack(
    *,
    session: str,
    history: list[dict[str, Any]],
    next_task: str,
    facts: list[dict[str, Any]] | None = None,
    version: int = 1,
) -> ContextPack | None:
    mode = context_pack_mode()
    if mode == "off":
        return None
    items = inventory(history, facts=facts, next_task=next_task)
    if not items:
        return None
    pack = lead_merge(items, next_task=next_task)
    pack.session = session
    pack.version = max(1, int(version))
    pack.mode = mode
    return pack


def format_context_pack(pack: ContextPack, *, max_chars: int = _MAX_PACK_CHARS) -> str:
    """Render pack for prompt injection (untrusted evidence framing)."""
    lines = [
        "# Session context pack (server-pruned, untrusted evidence)",
        "Retain constraints (don'ts) and high-utility keeps. Raw history may be truncated.",
        f"pack_version={pack.version} mode={pack.mode} {pack.lead_notes}",
    ]
    if pack.donts:
        lines.append("## Don'ts (negative constraints)")
        for index, item in enumerate(pack.donts, start=1):
            lines.append(f"{index}. {item}")
    if pack.keeps:
        lines.append("## Keeps (positive context)")
        for index, item in enumerate(pack.keeps, start=1):
            lines.append(f"{index}. {item}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def format_session_with_pack(
    history: list[dict[str, Any]],
    current_task: str,
    pack: ContextPack | None,
    *,
    max_chars: int = 60_000,
    raw_tail: int = 6,
) -> str:
    """Prefer pack + short raw tail over full transcript dump."""
    from unigrok_public.harness import format_session_prompt

    if pack is None or (not pack.keeps and not pack.donts):
        return format_session_prompt(
            history, current_task, max_chars=max_chars
        )
    pack_block = format_context_pack(pack)
    tail = list(history[-raw_tail:]) if history else []
    budget = max(1_000, int(max_chars) - len(pack_block) - 400)
    parts = [pack_block]
    if tail:
        rendered: list[str] = []
        remaining = budget
        for message in reversed(tail):
            role = str(message.get("role") or "user").upper()
            content = redact_secrets(str(message.get("content") or "")).strip()
            if not content:
                continue
            entry = f"## {role}\n{content}"
            if len(entry) > remaining:
                entry = entry[-remaining:]
            rendered.append(entry)
            remaining -= len(entry)
            if remaining <= 0:
                break
        rendered.reverse()
        if rendered:
            parts.append(
                "# Recent raw turns (tail only)\n" + "\n\n".join(rendered)
            )
    parts.append("# Current user request\n" + current_task)
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text
