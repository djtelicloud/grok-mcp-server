"""Credential-blind provider execution and cache isolation for the campaign.

This module deliberately knows only an opaque ``credential_binding_id``.  A
transport resolves the real credential through its provider-owned mechanism
(for example Google ADC or the local UniGrok service) and returns model output.
Secret material must never enter adapter settings, cache keys, or receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


CAMPAIGN_ID = "gemma-needle-2000-v1"
_SECRET_SETTING_NAMES = (
    "api_key",
    "access_token",
    "auth_token",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
)
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_CACHE_FORMAT_VERSION = 1
_SECRET_VALUE_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"\bAIza[A-Za-z0-9_-]{25,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\bBearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/=-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:^|[^A-Za-z0-9_])["']?[A-Z0-9_-]*(?:API[_-]?KEY|SESSION[_-]?TOKEN|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)["']?\s*[:=]\s*(?!["']?(?:<|\$\{|\[))(?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[A-Za-z0-9._~+/=@!#$%^&*()-]{8,})""",
        re.IGNORECASE,
    ),
)
_PII_VALUE_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
    ),
    re.compile(r"(?:^|[\s\"'])(?:/Users/|/home/)[^\s\"']+"),
    re.compile(r"(?:^|[\s\"'])[A-Za-z]:\\Users\\[^\s\"']+", re.IGNORECASE),
)


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


class RunMode(str, Enum):
    MOCK = "mock"
    REPLAY = "replay"
    LIVE = "live"


class ProviderContractError(ValueError):
    """A transport returned, but its artifact/provenance contract failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.transport_response_observed = True


ProviderTransport = Callable[[str, dict[str, Any]], dict[str, Any]]
ResponseSchema = type[BaseModel]


def default_cache_root() -> Path:
    """Return a user-private cache root outside every repository worktree."""

    if os.name == "posix" and Path.home().joinpath("Library").is_dir():
        return (
            Path.home() / "Library" / "Caches" / "UniGrok" / "campaigns" / CAMPAIGN_ID
        )
    return Path.home() / ".cache" / "unigrok" / "campaigns" / CAMPAIGN_ID


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_in_git_workspace(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    return any(
        (candidate / ".git").exists() for candidate in (absolute, *absolute.parents)
    )


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                raise ValueError("Campaign cache paths cannot traverse symbolic links.")
        except FileNotFoundError:
            continue


def _is_owner_private_directory(path: Path) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o700
        and (not hasattr(os, "geteuid") or metadata.st_uid == os.geteuid())
    )


def _private_directory(path: Path) -> None:
    """Create missing leaves privately; never chmod a caller-owned directory."""

    _reject_symlink_components(path)
    if path.exists():
        if not _is_owner_private_directory(path):
            raise ValueError(
                "Existing campaign cache directories must be owner-only (0700)."
            )
        return

    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    _reject_symlink_components(cursor)
    for candidate in reversed(missing):
        candidate.mkdir(mode=0o700)
        if not _is_owner_private_directory(candidate):
            raise ValueError("Campaign cache directory creation was not owner-only.")


def _segment(value: str) -> str:
    cleaned = value.strip()
    if not _SAFE_IDENTITY.fullmatch(cleaned):
        raise ValueError(
            "Provider cache identity fields must be safe opaque identifiers."
        )
    _reject_secret_like_payload(cleaned)
    return cleaned


def _reject_secret_setting_names(value: Any, *, path: str = "settings") -> None:
    """Fail closed when caller settings try to carry credential material."""

    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            if any(name in key for name in _SECRET_SETTING_NAMES):
                raise ValueError(
                    f"Secret or credential setting is forbidden at {path}.{raw_key}."
                )
            _reject_secret_setting_names(child, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_setting_names(child, path=f"{path}[{index}]")


def _reject_secret_like_payload(value: Any) -> None:
    """Reject provider output containing recognizable credential material."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Provider response must contain deterministic JSON values."
        ) from exc
    if any(pattern.search(serialized) for pattern in _SECRET_VALUE_PATTERNS):
        raise ValueError(
            "Provider response contains secret-like content and was not cached."
        )


def _iter_text_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _iter_text_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_text_values(child)


def _reject_pii_like_payload(value: Any) -> None:
    if any(
        pattern.search(text)
        for text in _iter_text_values(value)
        for pattern in _PII_VALUE_PATTERNS
    ):
        raise ValueError("Provider artifact contains PII or a private user path.")


def _canonical_json_bytes(value: Any) -> bytes:
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
            "Provider data must contain deterministic JSON values."
        ) from exc


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


class ProviderAdapter:
    """Validate provider output and persist only isolated, schema-valid cache rows."""

    def __init__(
        self,
        provider: str,
        model: str,
        plane: str,
        role: str,
        mode: RunMode,
        *,
        cache_root: Path | str | None = None,
        credential_binding_id: str = "unbound",
        transport: ProviderTransport | None = None,
    ) -> None:
        self.provider = provider.strip()
        self.model = model.strip()
        self.plane = plane.strip()
        self.role = role.strip()
        self.mode = RunMode(mode)
        self.credential_binding_id = credential_binding_id.strip()
        self.transport = transport

        for label, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("plane", self.plane),
            ("role", self.role),
            ("credential_binding_id", self.credential_binding_id),
        ):
            try:
                _segment(value)
            except ValueError as exc:
                raise ValueError(f"{label} must be a safe opaque identifier.") from exc

        root = (
            Path(cache_root).expanduser()
            if cache_root is not None
            else default_cache_root()
        )
        _reject_symlink_components(root)
        resolved = root.resolve(strict=False)
        if _is_within(resolved, _repo_root()) or _is_in_git_workspace(resolved):
            raise ValueError(
                "Campaign cache root must remain outside the repository workspace."
            )
        self.cache_root = resolved
        _private_directory(self.cache_root)

    def _source_mode(self) -> RunMode:
        return RunMode.LIVE if self.mode == RunMode.REPLAY else self.mode

    def _namespace_dir(self) -> Path:
        path = self.cache_root / self._source_mode().value
        for value in (
            self.credential_binding_id,
            self.provider,
            self.model,
            self.plane,
            self.role,
        ):
            path /= _segment(value)
        current = self.cache_root
        for part in path.relative_to(self.cache_root).parts:
            current /= part
            _private_directory(current)
        return path

    def _compute_cache_key(
        self,
        schema_version: str,
        template_digest: str,
        settings: dict[str, Any],
        request: str,
    ) -> str:
        _reject_secret_setting_names(settings)
        key_data = {
            "credential_binding_id": self.credential_binding_id,
            "provider": self.provider,
            "model": self.model,
            "plane": self.plane,
            "role": self.role,
            "schema_version": schema_version,
            "template_digest": template_digest,
            "settings": settings,
            "request": request,
        }
        _reject_secret_like_payload(key_data)
        key_bytes = _canonical_json_bytes(key_data)
        return hashlib.sha256(key_bytes).hexdigest()

    def _cache_provenance(
        self,
        schema_version: str,
        template_digest: str,
        settings: dict[str, Any],
        request: str,
    ) -> dict[str, str]:
        return {
            "request_digest": hashlib.sha256(request.encode("utf-8")).hexdigest(),
            "schema_version": schema_version,
            "settings_digest": _sha256_json(settings),
            "template_digest": template_digest,
        }

    def _cache_path(self, key: str) -> Path:
        return self._namespace_dir() / f"{key}.json"

    def _cache_identity(self) -> dict[str, str]:
        return {
            "credential_binding_id": self.credential_binding_id,
            "model": self.model,
            "plane": self.plane,
            "provider": self.provider,
            "role": self.role,
        }

    def _atomic_write_cache(
        self,
        key: str,
        response: dict[str, Any],
        provenance: dict[str, str],
    ) -> None:
        path = self._cache_path(key)
        envelope = {
            "cache_key": key,
            "format_version": _CACHE_FORMAT_VERSION,
            "identity": self._cache_identity(),
            "provenance": provenance,
            "response": response,
            "response_digest": _sha256_json(response),
        }
        payload = _canonical_json_bytes(envelope)
        if len(payload) > _MAX_CACHE_BYTES:
            raise ValueError("Provider response exceeds the campaign cache size limit.")

        fd, temp_name = tempfile.mkstemp(prefix=f".{key}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            path.chmod(0o600)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _read_cache(
        self,
        key: str,
        provenance: dict[str, str],
    ) -> dict[str, Any] | None:
        path = self._cache_path(key)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
                or metadata.st_size > _MAX_CACHE_BYTES
            ):
                return None
            handle = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = -1
            with handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(payload, dict) or set(payload) != {
            "cache_key",
            "format_version",
            "identity",
            "provenance",
            "response",
            "response_digest",
        }:
            return None
        response = payload.get("response")
        if (
            payload.get("format_version") != _CACHE_FORMAT_VERSION
            or payload.get("cache_key") != key
            or payload.get("identity") != self._cache_identity()
            or payload.get("provenance") != provenance
            or not isinstance(response, dict)
            or payload.get("response_digest") != _sha256_json(response)
        ):
            return None
        return response

    def extract_json_artifact(self, content: str) -> dict[str, Any] | None:
        """Accept exactly one JSON object and reject prose or fence wrappers."""

        if not isinstance(content, str) or not content.strip():
            return None
        try:
            artifact = json.loads(
                content,
                parse_constant=_reject_non_finite_json,
                object_pairs_hook=_reject_duplicate_object_pairs,
            )
        except (json.JSONDecodeError, ValueError):
            return None
        return artifact if isinstance(artifact, dict) else None

    def _validated_artifact(
        self,
        response: dict[str, Any],
        response_schema: ResponseSchema | None,
        *,
        require_transport_receipt: bool = False,
    ) -> dict[str, Any]:
        if not response or not isinstance(response, dict):
            raise ValueError("Provider response must be an object.")
        artifact = self.extract_json_artifact(response.get("content", ""))
        if artifact is None:
            raise ValueError("Provider response must contain one pure JSON object.")
        if response_schema is not None:
            try:
                validated = response_schema.model_validate(artifact)
            except ValidationError as exc:
                raise ValueError(
                    "Provider response failed the requested schema."
                ) from exc
            artifact = validated.model_dump(mode="json")
        _reject_secret_like_payload(response)
        _reject_pii_like_payload(artifact)
        if require_transport_receipt:
            self._validate_transport_receipt(response)
        return artifact

    def _validate_transport_receipt(self, response: dict[str, Any]) -> None:
        receipt = response.get("transport_receipt")
        if not isinstance(receipt, dict):
            raise ValueError("Provider response is missing its transport receipt.")
        required = {
            "configured_model",
            "provider",
            "resolved_model",
            "resolved_plane",
        }
        if not required.issubset(receipt):
            raise ValueError("Provider transport receipt is incomplete.")
        if receipt.get("provider") != self.provider:
            raise ValueError("Provider transport receipt has the wrong provider.")
        if receipt.get("configured_model") != self.model:
            raise ValueError(
                "Provider transport receipt has the wrong configured model."
            )
        if not str(receipt.get("resolved_model") or "").strip():
            raise ValueError("Provider transport receipt has no resolved model.")
        if str(receipt.get("resolved_plane") or "").casefold() != self.plane.casefold():
            raise ValueError(
                "Provider transport receipt has the wrong credential plane."
            )
        if self.provider == "vertex" and (
            receipt.get("auth_kind") != "google_adc"
            or receipt.get("total_attempt_limit") != 1
            or str(receipt.get("resolved_plane") or "").casefold() != "api"
        ):
            raise ValueError(
                "Vertex transport receipt violates the ADC attempt contract."
            )
        if self.provider == "unigrok" and (
            receipt.get("auth_kind") != "server_managed"
            or receipt.get("fallback_policy") != "same_plane"
            or receipt.get("finish_reason") != "final_answer"
            or receipt.get("resolved_model") != self.model
            or str(receipt.get("resolved_plane") or "").casefold() != "cli"
        ):
            raise ValueError(
                "UniGrok transport receipt violates the pinned-plane contract."
            )

    def validate_response(
        self,
        response: dict[str, Any],
        response_schema: ResponseSchema | None = None,
        *,
        require_transport_receipt: bool = False,
    ) -> bool:
        try:
            self._validated_artifact(
                response,
                response_schema,
                require_transport_receipt=require_transport_receipt,
            )
            return True
        except ValueError:
            return False

    def execute(
        self,
        request: str,
        schema_version: str,
        template_digest: str,
        settings: dict[str, Any],
        response_schema: ResponseSchema | None = None,
    ) -> dict[str, Any]:
        key = self._compute_cache_key(
            schema_version, template_digest, settings, request
        )
        provenance = self._cache_provenance(
            schema_version,
            template_digest,
            settings,
            request,
        )

        if self.mode == RunMode.REPLAY:
            cached = self._read_cache(key, provenance)
            if cached is None or not self.validate_response(
                cached,
                response_schema,
                require_transport_receipt=response_schema is not None,
            ):
                raise ValueError(
                    "Replay cache miss or invalid cached provider response."
                )
            return {**cached, "_cache_hit": True}

        if self.mode == RunMode.MOCK:
            cached = self._read_cache(key, provenance)
            if cached is not None and self.validate_response(cached, response_schema):
                return {**cached, "_cache_hit": True}
            response: dict[str, Any] = {"content": '{"mocked_artifact":true}'}
            self._validated_artifact(response, response_schema)
            self._atomic_write_cache(key, response, provenance)
            return {**response, "_cache_hit": False}

        if self.transport is None:
            raise RuntimeError(
                "Live provider execution requires an injected transport."
            )
        if response_schema is None:
            raise ValueError(
                "Live provider execution requires an explicit response schema."
            )

        # LIVE deliberately does not read a previous cache row.  Every live call
        # produces fresh transport evidence; REPLAY is the only zero-call reuse.
        try:
            response = self.transport(request, dict(settings))
        except Exception as exc:
            raise RuntimeError("Live provider transport failed.") from exc
        try:
            self._validated_artifact(
                response,
                response_schema,
                require_transport_receipt=True,
            )
            self._atomic_write_cache(key, response, provenance)
        except (OSError, ValueError) as exc:
            raise ProviderContractError(str(exc)) from exc
        return {**response, "_cache_hit": False}
