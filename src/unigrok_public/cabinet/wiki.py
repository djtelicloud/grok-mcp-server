"""Markdown wiki: atomic writes, directory sidecars, no shared mutable globals."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .layers import TOKEN_L0, TOKEN_L1, should_refresh, zero_llm_l0, zero_llm_l1
from .uri import CabinetUri

ABSTRACT = ".abstract.md"
OVERVIEW = ".overview.md"


class WikiStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def node_dir(self, uri: CabinetUri) -> Path:
        parts = [self._safe_scope(uri.scope), uri.kind]
        rest = uri.normalized_path()
        if rest:
            parts.extend(self._safe_scope(segment) for segment in rest.split("/"))
        return self.root.joinpath(*parts)

    def leaf_path(self, uri: CabinetUri) -> Path:
        directory = self.node_dir(uri)
        if uri.normalized_path():
            return directory.parent / f"{directory.name}.md"
        return directory / "INDEX.md"

    def _safe_scope(self, scope: str) -> str:
        return scope.replace("/", "__").replace(":", "--")

    def write_leaf(self, uri: CabinetUri, body: str, *, title: str | None = None) -> Path:
        path = self.leaf_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        heading = title or uri.normalized_path() or uri.kind
        if str(body).lstrip().startswith("#"):
            text = str(body).rstrip() + "\n"
        else:
            text = f"# {heading}\n\n{body}".rstrip() + "\n"
        _atomic_write(path, text)
        parent = uri.parent() or CabinetUri(uri.scope, uri.kind, "")
        self.refresh_directory(parent, title=parent.normalized_path() or parent.kind)
        grand = parent.parent()
        if grand is not None:
            age = _sidecar_age_s(self._sidecar(grand, ABSTRACT))
            if should_refresh(1, age):
                self.refresh_directory(grand, title=grand.kind)
        return path

    def read_leaf(self, uri: CabinetUri) -> str:
        path = self.leaf_path(uri)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def delete_leaf(self, uri: CabinetUri) -> bool:
        path = self.leaf_path(uri)
        if not path.is_file():
            return False
        path.unlink()
        parent = uri.parent()
        if parent is not None:
            self.refresh_directory(parent, title=parent.kind)
        return True

    def refresh_directory(self, uri: CabinetUri, *, title: str | None = None) -> None:
        directory = self.node_dir(uri)
        directory.mkdir(parents=True, exist_ok=True)
        abstracts: list[str] = []
        children = sorted(directory.iterdir()) if directory.is_dir() else []
        for child in children:
            if child.name in {ABSTRACT, OVERVIEW} or child.name.startswith("."):
                continue
            if child.suffix == ".md" and child.is_file():
                abstracts.append(zero_llm_l0(child.read_text(encoding="utf-8"), limit=40))
            elif child.is_dir():
                sidecar = child / ABSTRACT
                if sidecar.is_file():
                    abstracts.append(zero_llm_l0(sidecar.read_text(encoding="utf-8"), limit=40))
        heading = title or uri.normalized_path() or uri.kind
        l0 = zero_llm_l0(" ".join(abstracts) or heading, limit=TOKEN_L0)
        l1 = zero_llm_l1(heading, abstracts, limit=TOKEN_L1)
        _atomic_write(directory / ABSTRACT, l0 + "\n")
        _atomic_write(directory / OVERVIEW, l1 + "\n")

    def read_l0(self, uri: CabinetUri) -> str:
        path = self._sidecar(uri, ABSTRACT)
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def read_l1(self, uri: CabinetUri) -> str:
        path = self._sidecar(uri, OVERVIEW)
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def _sidecar(self, uri: CabinetUri, name: str) -> Path:
        return self.node_dir(uri) / name


def _sidecar_age_s(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 10_000.0


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
