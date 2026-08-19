"""Session compile: events, rule summary, EUA patch, origin guard."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .layers import zero_llm_l0
from .retrieve import ORIGIN_PREFIX
from .uri import CabinetUri, handoff_uri, peer_last_job_uri

HANDOFF_LIMIT = 240
EVENT_BODY_LIMIT = 16_384
EVENT_KINDS = frozenset(
    {
        "session-start",
        "user-prompt",
        "tool",
        "compact",
        "session-end",
        "remember",
        "peer-cream",
        "other",
    }
)


def origin_marker(qid: str) -> str:
    token = str(qid or "").strip()
    if not token:
        raise ValueError("qid is required")
    return f"{ORIGIN_PREFIX}{token}"


def contains_origin(text: str) -> bool:
    return ORIGIN_PREFIX in str(text or "")


def rule_summary(text: str, *, limit: int = HANDOFF_LIMIT) -> str:
    return zero_llm_l0(str(text or ""), limit=max(8, int(limit)))


def observation_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def eua_patch(body: str, search: str, replace: str) -> str:
    """Apply one SEARCH/REPLACE to an entity body. No LLM on the hot path."""
    hay = str(body or "")
    needle = str(search or "")
    if not needle or needle not in hay:
        return hay
    return hay.replace(needle, str(replace), 1)


def _event_kind(role: str) -> str:
    lowered = str(role or "").strip().lower()
    if lowered == "user":
        return "user-prompt"
    if lowered in EVENT_KINDS:
        return lowered
    return "other"


def compile_session(
    session: str,
    turns: list[dict[str, Any]],
    *,
    scope: str = "global",
    from_seat: str = "",
    to_seat: str = "",
) -> dict[str, Any]:
    """Zero-LLM compile: skip origin-marked bytes, emit events + one handoff."""
    name = str(session or "").strip()
    if not name:
        raise ValueError("session is required")
    events: list[dict[str, Any]] = []
    kept: list[dict[str, str]] = []
    for raw in turns:
        role = str(raw.get("role") or "other")
        content = str(raw.get("content") or "")
        if contains_origin(content):
            continue
        body = content.encode("utf-8")[:EVENT_BODY_LIMIT].decode("utf-8", errors="ignore")
        if not body.strip():
            continue
        kind = _event_kind(role)
        events.append({"kind": kind, "role": role, "body": body})
        kept.append({"role": role, "content": body})
    last_user = next(
        (item["content"] for item in reversed(kept) if item["role"] == "user"),
        "",
    )
    last_assistant = next(
        (item["content"] for item in reversed(kept) if item["role"] == "assistant"),
        "",
    )
    handoff = rule_summary(
        " ".join(part for part in (last_user, last_assistant) if part),
        limit=HANDOFF_LIMIT,
    )
    uri = handoff_uri(scope, name)
    return {
        "session": name,
        "scope": scope,
        "uri": str(uri),
        "from_seat": str(from_seat or ""),
        "to_seat": str(to_seat or ""),
        "events": events,
        "handoff": handoff,
        "observation": observation_hash(kept),
    }


def peer_last_job_markdown(seat: str, job: str, *, next_step: str = "") -> str:
    name = str(seat or "").strip() or "peer"
    lines = [f"# {name} last-job", "", str(job or "").strip() or "(empty)"]
    step = str(next_step or "").strip()
    if step:
        lines.extend(["", "## Next", step])
    return "\n".join(lines) + "\n"


def write_peer_target(scope: str, seat: str) -> CabinetUri:
    return peer_last_job_uri(scope, seat)
