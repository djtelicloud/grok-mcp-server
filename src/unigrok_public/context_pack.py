"""Per-session context pack: inventory → persona votes → lead merge → PFC.

Context is a list. After each completed turn (when enabled), UniGrok rebuilds a
bounded pack of keeps + don'ts, then runs a prefrontal condenser over Grok's
final selection so the next turn always holds one aggregated working-buffer
sentence at the bottom of the context list.

Modes (UNIGROK_CONTEXT_PACK):
  off  — disabled (process default)
  cpu  — five heuristic persona votes + lead merge + PFC hive loops (Docker live)
  hive — reserved; falls back to cpu until a spend-capped hive voter lands

PFC hive policy (max 2 loops per turn):
  loop1 — personas vote + may emit a provisional prefrontal sentence
  loop2 — only when knowledge extraction is incomplete / low confidence

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
_MAX_PFC_CHARS = 360
_MAX_PFC_LOOPS = 2
_PFC_POINT_LIMIT = 6
_PFC_LOOP2_MIN_POINTS = 4  # need another extraction pass when denser

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
_SENTENCE_END = re.compile(r"[.!?]\s*$")


@dataclass
class ContextItem:
    item_id: str
    kind: str
    text: str
    score: float = 0.0
    votes: dict[str, Vote] = field(default_factory=dict)
    decision: Vote = "drop"


@dataclass
class KnowledgePoint:
    """One extractable fact/constraint from Grok's final selection."""

    text: str
    kind: Literal["dont", "keep", "goal"]
    weight: float
    votes: dict[str, Literal["keep", "skip"]] = field(default_factory=dict)


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
    prefrontal: str = ""
    pfc_loops: int = 0
    pfc_points: int = 0
    pfc_confidence: float = 0.0

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
            prefrontal=str(data.get("prefrontal") or ""),
            pfc_loops=int(data.get("pfc_loops") or 0),
            pfc_points=int(data.get("pfc_points") or 0),
            pfc_confidence=float(data.get("pfc_confidence") or 0.0),
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


def _point_seed(
    text: str,
    *,
    kind: Literal["dont", "keep", "goal"],
    weight: float,
) -> KnowledgePoint:
    # Compress a keep/dont into a short extractable clause for the PFC sentence.
    clipped = _clip(re.sub(r"\s+", " ", text), limit=96)
    return KnowledgePoint(text=clipped, kind=kind, weight=weight)


def _extract_knowledge_points(
    pack: ContextPack,
    *,
    next_task: str,
    prior: list[KnowledgePoint] | None = None,
) -> list[KnowledgePoint]:
    """Knowledge extraction from Grok's final selection (keeps/donts)."""
    seen = {p.text.lower() for p in (prior or [])}
    points: list[KnowledgePoint] = list(prior or [])
    task_terms = _terms(next_task)

    for dont in pack.donts:
        seed = _point_seed(dont, kind="dont", weight=1.0)
        if seed.text.lower() in seen:
            continue
        seen.add(seed.text.lower())
        points.append(seed)

    for keep in pack.keeps:
        overlap = len(_terms(keep) & task_terms)
        weight = 0.55 + 0.35 * min(1.0, overlap / 3)
        # Don't promote constraint-shaped text into "goal".
        if _DONT.search(keep):
            kind: Literal["dont", "keep", "goal"] = "dont"
            weight = max(weight, 0.9)
        elif _GOALISH.search(keep):
            weight = max(weight, 0.85)
            kind = "goal"
        else:
            kind = "keep"
        seed = _point_seed(keep, kind=kind, weight=weight)
        if seed.text.lower() in seen:
            continue
        seen.add(seed.text.lower())
        points.append(seed)

    # Task itself can contribute one goal point when pack is thin.
    if next_task.strip() and len(points) < 2:
        seed = _point_seed(next_task, kind="goal", weight=0.7)
        if seed.text.lower() not in seen:
            points.append(seed)

    points.sort(key=lambda p: (-p.weight, p.kind, p.text))
    return points[: max(_PFC_POINT_LIMIT * 2, 8)]


def _pfc_persona_vote(
    point: KnowledgePoint,
    *,
    next_task: str,
    loop: int,
) -> dict[str, Literal["keep", "skip"]]:
    """Hive-style votes on whether a knowledge point belongs in the PFC sentence."""
    overlap = len(_terms(point.text) & _terms(next_task))
    critic: Literal["keep", "skip"] = (
        "keep" if point.kind == "dont" or point.weight >= 0.6 or overlap else "skip"
    )
    bounty: Literal["keep", "skip"] = "keep" if point.kind == "dont" else critic
    spec: Literal["keep", "skip"] = (
        "keep" if point.kind in {"dont", "goal"} or overlap else "skip"
    )
    failures: Literal["keep", "skip"] = (
        "keep" if point.kind == "dont" or "fail" in point.text.lower() else critic
    )
    # Complexity trims fluff on loop1; on loop2 it keeps anything that still
    # covers a gap (dont/goal) even if long.
    if loop >= 2 and point.kind in {"dont", "goal"}:
        complexity: Literal["keep", "skip"] = "keep"
    elif len(point.text) > 80 and overlap == 0 and point.kind == "keep":
        complexity = "skip"
    else:
        complexity = critic
    return {
        "critic": critic,
        "bounty": bounty,
        "spec": spec,
        "failures": failures,
        "complexity": complexity,
    }


def _selected_points(points: list[KnowledgePoint]) -> list[KnowledgePoint]:
    kept: list[KnowledgePoint] = []
    for point in points:
        keeps = sum(1 for v in point.votes.values() if v == "keep")
        if keeps >= 3:  # majority of five
            kept.append(point)
    kept.sort(key=lambda p: (-p.weight, 0 if p.kind == "dont" else 1, p.text))
    return kept[:_PFC_POINT_LIMIT]


def _clause(text: str) -> str:
    return re.sub(r"[.!?]+$", "", (text or "").strip())


def _compose_prefrontal(points: list[KnowledgePoint], *, next_task: str) -> str:
    """Aggregate selected points into one working-buffer sentence."""
    if not points:
        task = _clip(next_task, limit=120)
        return f"Hold focus: {_clause(task)}." if task else ""

    donts = [_clause(p.text) for p in points if p.kind == "dont" and _clause(p.text)]
    goals = [_clause(p.text) for p in points if p.kind == "goal" and _clause(p.text)]
    keeps = [_clause(p.text) for p in points if p.kind == "keep" and _clause(p.text)]
    # Drop keep/goal clauses already covered by a don't.
    dont_blob = " ".join(donts).lower()
    goals = [g for g in goals if g.lower() not in dont_blob]
    keeps = [k for k in keeps if k.lower() not in dont_blob]

    parts: list[str] = []
    if goals:
        parts.append("Aim: " + "; ".join(goals[:2]))
    elif keeps:
        parts.append("Hold: " + "; ".join(keeps[:2]))
    if donts:
        parts.append("Avoid: " + "; ".join(donts[:3]))
    if keeps and goals:
        extras = [k for k in keeps[:2] if k not in goals]
        if extras:
            parts.append("Also: " + "; ".join(extras))

    sentence = ". ".join(parts).strip()
    if not sentence:
        return ""
    if not _SENTENCE_END.search(sentence):
        sentence += "."
    return _clip(sentence, limit=_MAX_PFC_CHARS)


def _pfc_confidence(points: list[KnowledgePoint], pack: ContextPack) -> float:
    if not points and not pack.keeps and not pack.donts:
        return 0.0
    dont_cover = 1.0 if (not pack.donts or any(p.kind == "dont" for p in points)) else 0.0
    density = min(1.0, len(points) / max(1, min(_PFC_POINT_LIMIT, 3)))
    vote_strength = 0.0
    if points:
        vote_strength = sum(
            sum(1 for v in p.votes.values() if v == "keep") / 5.0 for p in points
        ) / len(points)
    return round(0.4 * dont_cover + 0.3 * density + 0.3 * vote_strength, 3)


def _needs_second_loop(
    points: list[KnowledgePoint],
    *,
    pack: ContextPack,
    confidence: float,
    provisional: str,
) -> bool:
    """Second hive loop only when knowledge extraction is incomplete."""
    if not provisional.strip():
        return bool(pack.keeps or pack.donts)
    if confidence < 0.55:
        return True
    if pack.donts and not any(p.kind == "dont" for p in points):
        return True
    # Dense lead selections need a second extraction pass to compress fairly.
    raw_slots = len(pack.donts) + len(pack.keeps)
    if raw_slots >= _PFC_LOOP2_MIN_POINTS and len(points) < min(3, raw_slots):
        return True
    return False


def prefrontal_condense(
    pack: ContextPack,
    *,
    next_task: str,
    max_loops: int = _MAX_PFC_LOOPS,
) -> ContextPack:
    """Hive takes Grok's final selection → one prefrontal summary sentence.

    Loop 1 always votes and may emit a provisional sentence in the same pass.
    Loop 2 runs only when extraction is incomplete / low confidence.
    """
    if not pack.keeps and not pack.donts and not (next_task or "").strip():
        pack.prefrontal = ""
        pack.pfc_loops = 0
        pack.pfc_points = 0
        pack.pfc_confidence = 0.0
        return pack

    loops = 0
    prior: list[KnowledgePoint] = []
    selected: list[KnowledgePoint] = []
    sentence = ""
    confidence = 0.0

    for loop_idx in range(1, max(1, min(int(max_loops), _MAX_PFC_LOOPS)) + 1):
        loops = loop_idx
        # Knowledge extraction (+ votes) in the same hive turn.
        extracted = _extract_knowledge_points(pack, next_task=next_task, prior=prior)
        for point in extracted:
            point.votes = _pfc_persona_vote(point, next_task=next_task, loop=loop_idx)
        selected = _selected_points(extracted)
        sentence = _compose_prefrontal(selected, next_task=next_task)
        confidence = _pfc_confidence(selected, pack)
        prior = extracted
        if loop_idx == 1 and not _needs_second_loop(
            selected, pack=pack, confidence=confidence, provisional=sentence
        ):
            break
        if loop_idx >= 2:
            break

    pack.prefrontal = sentence
    pack.pfc_loops = loops
    pack.pfc_points = len(selected)
    pack.pfc_confidence = confidence
    pack.lead_notes = (
        f"{pack.lead_notes}; pfc_loops={loops} pfc_points={len(selected)} "
        f"pfc_conf={confidence}"
    ).strip("; ")
    return pack


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
    pack = prefrontal_condense(pack, next_task=next_task)
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


def format_prefrontal(pack: ContextPack) -> str:
    """Bottom-of-list working buffer — last cue before the current request."""
    sentence = (pack.prefrontal or "").strip()
    if not sentence:
        return ""
    return (
        "# Prefrontal (working buffer — hold this)\n"
        f"{sentence}\n"
        f"(pfc_loops={pack.pfc_loops} points={pack.pfc_points} "
        f"conf={pack.pfc_confidence})"
    )


def format_session_with_pack(
    history: list[dict[str, Any]],
    current_task: str,
    pack: ContextPack | None,
    *,
    max_chars: int = 60_000,
    raw_tail: int = 6,
) -> str:
    """Prefer pack + short raw tail + prefrontal over full transcript dump.

    Order (context list bottom → action):
      pack (donts/keeps) → recent raw turns → prefrontal sentence → current request
    """
    from unigrok_public.harness import format_session_prompt

    if pack is None or (not pack.keeps and not pack.donts and not pack.prefrontal):
        return format_session_prompt(
            history, current_task, max_chars=max_chars
        )
    pack_block = format_context_pack(pack)
    pfc_block = format_prefrontal(pack)
    tail = list(history[-raw_tail:]) if history else []
    overhead = len(pack_block) + len(pfc_block) + 400
    budget = max(1_000, int(max_chars) - overhead)
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
    # Prefrontal sits at the bottom of the context list — last hold before acting.
    if pfc_block:
        parts.append(pfc_block)
    parts.append("# Current user request\n" + current_task)
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text
