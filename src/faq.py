"""Deterministic, local-only index for the canonical UniGrok OKF FAQ.

The FAQ is release-versioned documentation, not user/session knowledge. This
module intentionally never touches SQLite, invokes xAI, or returns local paths.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import PathResolver

FAQ_SOURCE_URI = "grok://faq"
FAQ_DOCS_PATH = "/docs/okf/faq.md"
FAQ_SCHEMA_VERSION = "1"
_MAX_EXCERPT_CHARS = 900
_MAX_RESOURCE_ITEMS = 50
_MIN_KEYWORD_MATCH_SCORE = 0.35

_ENTRY_RE = re.compile(
    r"^##\s+(?P<question>.+?)\s+\{#(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*)\}\s*$",
    re.MULTILINE,
)
_KEYWORDS_RE = re.compile(r"^\*\*Keywords:\*\*\s*(?P<keywords>.+?)\s*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FAQDocumentError(RuntimeError):
    """Raised when the canonical FAQ cannot be safely loaded or validated."""


@dataclass(frozen=True)
class FAQEntry:
    id: str
    question: str
    keywords: tuple[str, ...]
    answer: str

    @property
    def anchor(self) -> str:
        return f"{FAQ_DOCS_PATH}#{self.id}"


@dataclass(frozen=True)
class FAQIndex:
    schema_version: str
    source_version: str
    entries: tuple[FAQEntry, ...]

    def browse(self, limit: int = _MAX_RESOURCE_ITEMS) -> Dict[str, Any]:
        bounded_limit = max(1, min(int(limit), _MAX_RESOURCE_ITEMS))
        items = [
            {
                "id": entry.id,
                "question": entry.question,
                "keywords": list(entry.keywords),
                "source_uri": FAQ_SOURCE_URI,
                "source_anchor": entry.anchor,
            }
            for entry in self.entries[:bounded_limit]
        ]
        return {
            "schema_version": self.schema_version,
            "source_version": self.source_version,
            "source_uri": FAQ_SOURCE_URI,
            "count": len(self.entries),
            "items": items,
        }

    def get(self, entry_id: str) -> Optional[FAQEntry]:
        target = _normalize_phrase(entry_id)
        return next(
            (entry for entry in self.entries if _normalize_phrase(entry.id) == target),
            None,
        )

    def search(self, query: str, limit: int = 3) -> list[Dict[str, Any]]:
        normalized_query = _normalize_phrase(query)
        if not normalized_query:
            return [self._match_view(entry, "browse", 0.0) for entry in self.entries[:limit]]

        # One- and two-character words (for example "a" in an unrelated
        # sentence) are not meaningful retrieval evidence and must never
        # cause a support answer to surface out of context.
        query_tokens = {token for token in _tokens(normalized_query) if len(token) >= 3}
        if not query_tokens:
            return []
        matches: list[tuple[float, FAQEntry, str]] = []
        for entry in self.entries:
            entry_id = _normalize_phrase(entry.id)
            question = _normalize_phrase(entry.question)
            aliases = tuple(_normalize_phrase(keyword) for keyword in entry.keywords)
            if normalized_query in (entry_id, question):
                matches.append((1.0, entry, "exact"))
                continue
            if normalized_query in aliases:
                matches.append((0.9, entry, "alias"))
                continue

            candidate_tokens = set(
                _tokens(" ".join(("unigrok", entry.id, entry.question, *entry.keywords)))
            )
            overlap = len(query_tokens & candidate_tokens)
            if overlap:
                score = round(overlap / max(1, len(query_tokens)), 4)
                if score >= _MIN_KEYWORD_MATCH_SCORE:
                    matches.append((score, entry, "keyword"))

        matches.sort(key=lambda item: (-item[0], item[1].id))
        return [self._match_view(entry, match_type, score) for score, entry, match_type in matches[:limit]]

    def _match_view(self, entry: FAQEntry, match_type: str, score: float) -> Dict[str, Any]:
        return {
            "id": entry.id,
            "question": entry.question,
            "answer_excerpt": _excerpt(entry.answer),
            "match_type": match_type,
            "score": score,
            "source_uri": FAQ_SOURCE_URI,
            "source_anchor": entry.anchor,
        }


_cache_lock = threading.Lock()
_cached_signature: Optional[tuple[str, int, int]] = None
_cached_index: Optional[FAQIndex] = None


def _faq_path() -> Path:
    return PathResolver.get_service_root() / "docs" / "okf" / "faq.md"


def _normalize_phrase(value: str) -> str:
    return " ".join(_tokens(str(value or "").lower()))


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(str(value or "").lower())


def _excerpt(answer: str) -> str:
    compact = re.sub(r"\s+", " ", answer).strip()
    if len(compact) <= _MAX_EXCERPT_CHARS:
        return compact
    return compact[:_MAX_EXCERPT_CHARS].rstrip() + "…"


def _frontmatter_and_body(text: str) -> tuple[Dict[str, str], str]:
    if not text.startswith("---\n"):
        raise FAQDocumentError("FAQ document must begin with YAML-style frontmatter.")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FAQDocumentError("FAQ document frontmatter is not closed.")

    metadata: Dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, text[end + 5 :]


def _parse_keywords(block: str, entry_id: str) -> tuple[tuple[str, ...], str]:
    match = _KEYWORDS_RE.search(block)
    if not match:
        raise FAQDocumentError(f"FAQ entry '{entry_id}' must declare a Keywords line.")
    keywords = tuple(
        keyword.strip().strip("`")
        for keyword in match.group("keywords").split(",")
        if keyword.strip().strip("`")
    )
    if not keywords:
        raise FAQDocumentError(f"FAQ entry '{entry_id}' must declare at least one keyword.")
    answer = (block[: match.start()] + block[match.end() :]).strip()
    if not answer:
        raise FAQDocumentError(f"FAQ entry '{entry_id}' must have a non-empty answer.")
    return keywords, answer


def parse_faq_document(text: str) -> FAQIndex:
    """Parse and validate the strict canonical FAQ Markdown format."""
    metadata, body = _frontmatter_and_body(text)
    if metadata.get("okf_version") != "0.1":
        raise FAQDocumentError("FAQ document must declare okf_version: 0.1.")
    if metadata.get("faq_schema_version") != FAQ_SCHEMA_VERSION:
        raise FAQDocumentError(f"FAQ document must declare faq_schema_version: {FAQ_SCHEMA_VERSION}.")
    source_version = metadata.get("source_version")
    if not source_version:
        raise FAQDocumentError("FAQ document must declare source_version.")

    headings = list(_ENTRY_RE.finditer(body))
    if not headings:
        raise FAQDocumentError("FAQ document must contain at least one ## question {#stable-id} entry.")

    entries: list[FAQEntry] = []
    seen_ids: set[str] = set()
    for position, heading in enumerate(headings):
        entry_id = heading.group("id")
        if entry_id in seen_ids:
            raise FAQDocumentError(f"FAQ entry id '{entry_id}' is duplicated.")
        seen_ids.add(entry_id)
        question = heading.group("question").strip()
        end = headings[position + 1].start() if position + 1 < len(headings) else len(body)
        keywords, answer = _parse_keywords(body[heading.end() : end], entry_id)
        entries.append(FAQEntry(id=entry_id, question=question, keywords=keywords, answer=answer))

    return FAQIndex(
        schema_version=FAQ_SCHEMA_VERSION,
        source_version=source_version,
        entries=tuple(entries),
    )


def get_faq_index() -> FAQIndex:
    """Return the cached index, rebuilding only after the canonical file changes."""
    global _cached_signature, _cached_index
    path = _faq_path()
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FAQDocumentError("The curated FAQ is unavailable in this runtime.") from exc

    signature = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if _cached_index is not None and _cached_signature == signature:
            return _cached_index
        index = parse_faq_document(text)
        _cached_signature = signature
        _cached_index = index
        return index


def faq_status() -> Dict[str, Any]:
    """Boolean-only readiness view suitable for public-safe health checks."""
    try:
        index = get_faq_index()
    except FAQDocumentError:
        return {"loaded": False, "entries": 0}
    return {"loaded": True, "entries": len(index.entries)}


def clear_faq_cache() -> None:
    """Test helper: clear the process-local cache after swapping fixture files."""
    global _cached_signature, _cached_index
    with _cache_lock:
        _cached_signature = None
        _cached_index = None
