"""unigrok://{scope}/{kind}/{path} — scope is percent-encoded."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

SCHEME = "unigrok://"
KINDS = frozenset({"memories", "resources", "skills", "peers", "sessions"})
_SAFE = ".-_~"


class CabinetUriError(ValueError):
    """Invalid cabinet URI."""


@dataclass(frozen=True, slots=True)
class CabinetUri:
    scope: str
    kind: str
    path: str = ""

    @property
    def is_dir(self) -> bool:
        return not self.path or self.path.endswith("/")

    def normalized_path(self) -> str:
        return self.path.strip("/")

    def parent(self) -> CabinetUri | None:
        rest = self.normalized_path()
        if not rest:
            return None
        if "/" not in rest:
            return CabinetUri(scope=self.scope, kind=self.kind, path="")
        head, _sep, _tail = rest.rpartition("/")
        return CabinetUri(scope=self.scope, kind=self.kind, path=head)

    def child(self, name: str) -> CabinetUri:
        piece = str(name or "").strip().strip("/")
        if not piece or "/" in piece or piece in {".", ".."}:
            raise CabinetUriError("child name must be a single path segment")
        rest = self.normalized_path()
        nxt = f"{rest}/{piece}" if rest else piece
        return CabinetUri(scope=self.scope, kind=self.kind, path=nxt)

    def __str__(self) -> str:
        return format_uri(self.scope, self.kind, self.path)


def format_uri(scope: str, kind: str, path: str = "") -> str:
    kind_n = str(kind or "").strip()
    if kind_n not in KINDS:
        raise CabinetUriError(f"kind must be one of {sorted(KINDS)}")
    scope_n = str(scope or "").strip()
    if not scope_n:
        raise CabinetUriError("scope is required")
    encoded_scope = quote(scope_n, safe=_SAFE)
    rest = str(path or "").strip("/")
    if rest:
        parts = []
        for segment in rest.split("/"):
            if segment in {"", ".", ".."}:
                raise CabinetUriError("path must not contain empty, '.', or '..' segments")
            parts.append(quote(segment, safe=_SAFE))
        return f"{SCHEME}{encoded_scope}/{kind_n}/{'/'.join(parts)}"
    return f"{SCHEME}{encoded_scope}/{kind_n}"


def parse_uri(value: str) -> CabinetUri:
    raw = str(value or "").strip()
    if not raw.startswith(SCHEME):
        raise CabinetUriError("URI must start with unigrok://")
    body = raw[len(SCHEME) :]
    if not body or body.startswith("/"):
        raise CabinetUriError("URI is missing a scope")
    parts = body.split("/")
    if len(parts) < 2:
        raise CabinetUriError("URI must include a kind")
    scope = unquote(parts[0])
    kind = unquote(parts[1])
    if kind not in KINDS:
        raise CabinetUriError(f"kind must be one of {sorted(KINDS)}")
    segs = [unquote(p) for p in parts[2:] if p != ""]
    if any(seg in {".", "..", ""} for seg in segs):
        raise CabinetUriError("path must not contain empty, '.', or '..' segments")
    return CabinetUri(scope=scope, kind=kind, path="/".join(segs))


def _leaf_name(value: str) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        raise CabinetUriError("name is required")
    return text.replace("/", "__").replace(":", "--")


def fact_uri(scope: str, fact_id: int) -> CabinetUri:
    return CabinetUri(scope=scope, kind="memories", path=f"facts/{int(fact_id)}")


def handoff_uri(scope: str, session: str) -> CabinetUri:
    return CabinetUri(scope=scope, kind="sessions", path=f"{_leaf_name(session)}/handoff")


def peer_last_job_uri(scope: str, seat: str) -> CabinetUri:
    return CabinetUri(scope=scope, kind="peers", path=f"{_leaf_name(seat)}/last-job")
