"""Private, content-addressed artifacts for the Stage 1 safety gate.

The mock safety gate writes research evidence, never a training dataset.  Every
artifact lives outside Git workspaces under owner-only directories and is
published atomically with a digest-bound name.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .provider_adapters import (
    _is_in_git_workspace,
    _is_within,
    _private_directory,
    _reject_secret_like_payload,
    _reject_symlink_components,
    _repo_root,
    _segment,
)


MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_CONTENT_PREFIX_LENGTH = 31


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic JSON bytes or fail closed for non-JSON values."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Stage 1 artifacts must contain deterministic JSON values."
        ) from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class ArtifactRef:
    digest: str
    relative_path: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
        }


class PrivateArtifactStore:
    """Write immutable JSON artifacts beneath one safe external root."""

    def __init__(self, root: Path | str) -> None:
        expanded = Path(root).expanduser()
        _reject_symlink_components(expanded)
        resolved = expanded.resolve(strict=False)
        if _is_within(resolved, _repo_root()) or _is_in_git_workspace(resolved):
            raise ValueError("Stage 1 artifacts must remain outside Git workspaces.")
        existed = resolved.exists()
        _private_directory(resolved)
        self.root = resolved
        if not existed:
            self._fsync_directory(resolved.parent)

    def _namespace(self, segments: Iterable[str]) -> Path:
        current = self.root
        for raw_segment in segments:
            current /= _segment(raw_segment)
            existed = current.exists()
            _private_directory(current)
            if not existed:
                self._fsync_directory(current.parent)
        return current

    @staticmethod
    def _safe_file_name(value: str) -> str:
        if not value.endswith(".json"):
            raise ValueError("Stage 1 artifact files must use a .json suffix.")
        stem = value[:-5]
        return f"{_segment(stem)}.json"

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _verify_private_file(path: Path, expected: bytes | None = None) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
                or metadata.st_size > MAX_ARTIFACT_BYTES
            ):
                raise ValueError(
                    "Stage 1 artifact file is not owner-private and bounded."
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                payload = handle.read(MAX_ARTIFACT_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ValueError("Stage 1 artifact exceeds the size limit.")
        if expected is not None and payload != expected:
            raise FileExistsError(
                "Immutable Stage 1 artifact path has different content."
            )
        return payload

    def write_named(
        self,
        namespace: Iterable[str],
        file_name: str,
        value: Any,
    ) -> ArtifactRef:
        """Publish one immutable named artifact, idempotently for equal bytes."""

        if not isinstance(value, dict):
            raise ValueError("Stage 1 artifact root must be an object.")
        directory = self._namespace(namespace)
        safe_name = self._safe_file_name(file_name)
        path = directory / safe_name
        _reject_secret_like_payload(value)
        payload = canonical_json_bytes(value)
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ValueError("Stage 1 artifact exceeds the size limit.")
        digest = hashlib.sha256(payload).hexdigest()

        if path.exists():
            self._verify_private_file(path, payload)
        else:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{safe_name}.", dir=directory
            )
            temp_path = Path(temp_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temp_path, path, follow_symlinks=False)
                except FileExistsError:
                    self._verify_private_file(path, payload)
                path.chmod(0o600, follow_symlinks=False)
                self._fsync_directory(directory)
                self._verify_private_file(path, payload)
            finally:
                temp_path.unlink(missing_ok=True)
                self._fsync_directory(directory)

        return ArtifactRef(
            digest=digest,
            relative_path=path.relative_to(self.root).as_posix(),
            size_bytes=len(payload),
        )

    def write_content_addressed(
        self,
        namespace: Iterable[str],
        prefix: str,
        value: Any,
    ) -> ArtifactRef:
        if not isinstance(value, dict):
            raise ValueError("Stage 1 artifact root must be an object.")
        safe_prefix = _segment(prefix)
        if len(safe_prefix) > MAX_CONTENT_PREFIX_LENGTH:
            raise ValueError(
                f"Content-addressed artifact prefix must be at most "
                f"{MAX_CONTENT_PREFIX_LENGTH} characters."
            )
        payload = canonical_json_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        return self.write_named(namespace, f"{safe_prefix}-{digest}.json", value)

    def read(self, reference: ArtifactRef) -> dict[str, Any]:
        relative = Path(reference.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe Stage 1 artifact reference.")
        path = self.root / relative
        _reject_symlink_components(path)
        payload = self._verify_private_file(path)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != reference.digest or len(payload) != reference.size_bytes:
            raise ValueError("Stage 1 artifact reference failed digest validation.")
        try:
            value = json.loads(
                payload,
                parse_constant=_reject_non_finite_json,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Stage 1 artifact is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Stage 1 artifact root must be an object.")
        return value
