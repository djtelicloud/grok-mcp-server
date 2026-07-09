# evals/cassettes.py
# Cassette loading + session export for the offline eval harness.
#
# A cassette file (evals/cassettes/*.json) is a JSON object mapping
# task_id -> script, where a script is:
#   {"responses": [{"content", "tool_calls", "usage", "cost_usd",
#                   "citations", "id"}, ...],
#    "verdicts":  [{"status": "pass"|"fail", "issues": [...],
#                   "next_action": str, "cost_usd": float}, ...]}
#
# Responses are consumed IN ORDER across every chat the run creates (agent
# depths, self-escalation rebuilds, thinking-mode reviewer chats), which is
# what makes replay deterministic. `verdicts` script chat.parse() for the
# thinking route's reflection reviewer.

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CASSETTES_DIR = Path(__file__).resolve().parent / "cassettes"


def load_cassettes(cassettes_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Merge every *.json cassette file in the directory into one
    task_id -> script mapping. Later files (sorted by name) win on id clashes."""
    directory = Path(cassettes_dir or DEFAULT_CASSETTES_DIR)
    merged: Dict[str, Dict[str, Any]] = {}
    if not directory.is_dir():
        return merged
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cassette file {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"cassette file {path} must be a task_id -> script object")
        for task_id, script in data.items():
            if isinstance(script, dict):
                merged[str(task_id)] = script
    return merged


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "session"


def stable_substrings(text: str, max_n: int = 2, min_len: int = 15,
                      max_len: int = 60) -> List[str]:
    """Pick up to max_n stable 'expected-contains' substrings from an answer.

    Candidates are sentence/line fragments; the longest ones win (long
    fragments are the least likely to appear by accident). Each pick is
    truncated to max_len characters — a prefix of a candidate is still a
    contiguous substring of the original answer, so `contains` stays valid.
    """
    candidates = []
    for fragment in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        fragment = fragment.strip()
        if len(fragment) < min_len or fragment.startswith("```"):
            continue
        if fragment not in candidates:
            candidates.append(fragment)
    candidates.sort(key=len, reverse=True)
    picks = []
    for fragment in candidates[:max_n]:
        pick = fragment[:max_len].rstrip()
        if pick and pick not in picks:
            picks.append(pick)
    return picks


async def export_session(
    store: Any,
    session_name: str,
    tasks_dir: Path,
    cassettes_dir: Path,
    category: str = "memory",
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Turn a real stored session into a replayable golden task + cassette.

    Reads the session's messages from a GrokSessionStore, then emits:
      - <tasks_dir>/session_<name>.json — task skeleton whose prompt is the
        session's FIRST user turn and whose graders expect the corresponding
        assistant answer's stable substrings;
      - <cassettes_dir>/session_<name>.json — a cassette scripting every
        stored assistant turn (usage/cost recovered from message metadata
        when present).

    Returns {"task_id", "task_path", "cassette_path"}; raises ValueError when
    the session has no usable user/assistant exchange.
    """
    messages = await store.load_messages(session_name)
    if not messages:
        raise ValueError(f"session '{session_name}' has no stored messages")

    first_user = next((m for m in messages if m.get("role") == "user"), None)
    if first_user is None:
        raise ValueError(f"session '{session_name}' has no user turn to replay")
    first_user_idx = messages.index(first_user)
    first_assistant = next(
        (m for m in messages[first_user_idx + 1:] if m.get("role") == "assistant"),
        None,
    )
    if first_assistant is None:
        raise ValueError(f"session '{session_name}' has no assistant answer to grade")

    resolved_id = task_id or f"session_{_safe_name(session_name)}"
    graders = [
        {"type": "contains", "value": substring}
        for substring in stable_substrings(first_assistant.get("content") or "")
    ]
    if not graders:
        raise ValueError(
            f"session '{session_name}': assistant answer too short to derive stable substrings"
        )
    graders.append({"type": "structural", "field": "finish_reason", "equals": "final_answer"})

    task = {
        "id": resolved_id,
        "category": category,
        "prompt": str(first_user.get("content") or ""),
        "graders": graders,
        "tags": ["exported-session", session_name],
    }

    responses = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        metadata = message.get("metadata") or {}
        responses.append({
            "content": str(message.get("content") or ""),
            "usage": {
                "prompt_tokens": max(0, int(metadata.get("tokens") or 0) // 2),
                "completion_tokens": max(0, int(metadata.get("tokens") or 0) // 2),
            },
            "cost_usd": float(metadata.get("cost") or 0.0),
        })
    cassette = {resolved_id: {"responses": responses}}

    tasks_dir = Path(tasks_dir)
    cassettes_dir = Path(cassettes_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    cassettes_dir.mkdir(parents=True, exist_ok=True)
    task_path = tasks_dir / f"{resolved_id}.json"
    cassette_path = cassettes_dir / f"{resolved_id}.json"
    task_path.write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cassette_path.write_text(json.dumps(cassette, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"task_id": resolved_id, "task_path": str(task_path), "cassette_path": str(cassette_path)}
