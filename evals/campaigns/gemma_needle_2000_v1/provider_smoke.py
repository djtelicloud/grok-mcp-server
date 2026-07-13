"""Bounded Stage 0.5 provider wiring smoke for Vertex and UniGrok.

This command performs no dataset generation. A live run is limited to one
synthetic probe per configured provider, with zero retries, and requires an
explicit command-line confirmation. Replay re-validates only the private live
cache and never constructs a provider transport.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .provider_adapters import (
    CAMPAIGN_ID,
    ProviderAdapter,
    RunMode,
    _reject_secret_like_payload,
)
from .provider_transports import UniGrokMCPTransport, VertexADCTransport


PROBE_SCHEMA_VERSION = "provider-contract-v1"
PROBE_TEMPLATE = (
    "Return exactly this one-line JSON object and nothing else: "
    '{"probe":"provider-contract-v1","nonce":"<NONCE>"}. '
    "Do not use markdown, commentary, promises, or additional fields."
)
PROBE_TEMPLATE_DIGEST = "sha256:" + hashlib.sha256(
    PROBE_TEMPLATE.encode("utf-8")
).hexdigest()
_MAX_PROFILE_BYTES = 64 * 1024
_SAFE_BINDING_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$"


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
    return any((candidate / ".git").exists() for candidate in (absolute, *absolute.parents))


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                raise ValueError("Provider paths cannot traverse symbolic links.")
        except FileNotFoundError:
            continue


def default_profile_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "UniGrok"
        / "campaigns"
        / CAMPAIGN_ID
        / "providers.json"
    )


def default_receipts_root() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "UniGrok"
        / "campaigns"
        / CAMPAIGN_ID
        / "receipts"
    )


class ProbeArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe: Literal["provider-contract-v1"]
    nonce: str = Field(min_length=8, max_length=96, pattern=r"^[A-Za-z0-9_.-]+$")


class VertexBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["vertex"]
    credential_binding_id: str = Field(pattern=_SAFE_BINDING_ID)
    auth_kind: Literal["google_adc"]
    project: str = Field(min_length=4, max_length=128, pattern=r"^[a-z][a-z0-9-]+$")
    location: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    model: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    role: Literal["mutation-adjudication-smoke"]


class UniGrokBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["unigrok"]
    credential_binding_id: str = Field(pattern=_SAFE_BINDING_ID)
    auth_kind: Literal["server_managed"]
    endpoint: str
    model: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    plane: Literal["cli"]
    fallback_policy: Literal["same_plane"]
    role: Literal["seed-critic-smoke"]

    @field_validator("endpoint")
    @classmethod
    def require_loopback_mcp(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 4765
            or parsed.path.rstrip("/") != "/mcp"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "UniGrok endpoint must be the uncredentialed loopback :4765/mcp URL."
            )
        return value


ProviderBinding = Annotated[
    VertexBinding | UniGrokBinding,
    Field(discriminator="provider"),
]


class ProviderSmokeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    campaign_id: Literal["gemma-needle-2000-v1"]
    max_live_calls: Literal[2]
    orchestrator_retry_limit: Literal[0]
    vertex_max_output_tokens: int = Field(ge=16, le=128)
    bindings: list[ProviderBinding] = Field(min_length=2, max_length=2)

    @field_validator("bindings")
    @classmethod
    def require_one_binding_per_provider(
        cls,
        value: list[ProviderBinding],
    ) -> list[ProviderBinding]:
        if {binding.provider for binding in value} != {"vertex", "unigrok"}:
            raise ValueError("Profile must contain exactly one Vertex and one UniGrok binding.")
        return value


def _owner_private_file(path: Path) -> None:
    metadata = path.stat(follow_symlinks=False)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Provider profile must be a regular file.")
    if (
        stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
    ):
        raise ValueError("Provider profile must be owner-owned and mode 0600.")
    parent = path.parent.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
        or (hasattr(os, "geteuid") and parent.st_uid != os.geteuid())
    ):
        raise ValueError("Provider profile parent must be owner-owned and mode 0700.")


def load_profile(path: Path) -> ProviderSmokeProfile:
    expanded = path.expanduser()
    _reject_symlink_components(expanded)
    resolved = expanded.resolve(strict=True)
    if _is_within(resolved, _repo_root()) or _is_in_git_workspace(resolved):
        raise ValueError("Live provider profiles must remain outside the repository.")
    _owner_private_file(resolved)
    if resolved.stat().st_size > _MAX_PROFILE_BYTES:
        raise ValueError("Provider profile exceeds the size limit.")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        profile = ProviderSmokeProfile.model_validate(raw)
        _reject_secret_like_payload(profile.model_dump(mode="json"))
        return profile
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        raise ValueError("Provider profile failed strict validation.") from exc


def _private_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if path.exists():
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        ):
            raise ValueError("Existing receipt directories must be owner-only (0700).")
        return

    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    _reject_symlink_components(cursor)
    for candidate in reversed(missing):
        candidate.mkdir(mode=0o700)
        metadata = candidate.stat(follow_symlinks=False)
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("Receipt directory creation was not owner-only.")


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    _private_directory(path.parent)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, path, follow_symlinks=False)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _transport(binding: ProviderBinding):
    if isinstance(binding, VertexBinding):
        return VertexADCTransport(
            project=binding.project,
            location=binding.location,
            model=binding.model,
        )
    return UniGrokMCPTransport(
        endpoint=binding.endpoint,
        model=binding.model,
        plane=binding.plane,
        fallback_policy=binding.fallback_policy,
    )


def _safe_error_code(exc: Exception) -> str:
    message = str(exc).casefold()
    if "pure json" in message:
        return "invalid_json_artifact"
    if "requested schema" in message or "different probe nonce" in message:
        return "schema_contract_failed"
    if "transport receipt" in message or "credential plane" in message:
        return "provenance_contract_failed"
    if "transport failed" in message:
        return "transport_failed"
    return "provider_contract_failed"


def run_smoke(
    *,
    profile: ProviderSmokeProfile,
    mode: RunMode,
    nonce: str,
    receipts_root: Path,
) -> tuple[dict[str, Any], bool]:
    if mode not in {RunMode.LIVE, RunMode.REPLAY}:
        raise ValueError("Stage 0.5 supports only live or replay mode.")
    expanded_receipts = receipts_root.expanduser()
    _reject_symlink_components(expanded_receipts)
    resolved_receipts = expanded_receipts.resolve(strict=False)
    if _is_within(resolved_receipts, _repo_root()) or _is_in_git_workspace(
        resolved_receipts
    ):
        raise ValueError("Provider receipts must remain outside the repository.")
    _private_directory(resolved_receipts)

    expected = ProbeArtifact(probe=PROBE_SCHEMA_VERSION, nonce=nonce)
    request = PROBE_TEMPLATE.replace("<NONCE>", nonce)
    provider_receipts: list[dict[str, Any]] = []

    for binding in profile.bindings:
        response_observed = False
        settings = (
            {
                "max_output_tokens": profile.vertex_max_output_tokens,
                "temperature": 0,
                "total_attempt_limit": 1,
            }
            if isinstance(binding, VertexBinding)
            else {"accepted_artifact": PROBE_SCHEMA_VERSION, "mode": "fast"}
        )
        adapter = ProviderAdapter(
            provider=binding.provider,
            model=binding.model,
            plane=getattr(binding, "plane", "api"),
            role=binding.role,
            mode=mode,
            credential_binding_id=binding.credential_binding_id,
            transport=_transport(binding) if mode == RunMode.LIVE else None,
        )
        try:
            response = adapter.execute(
                request=request,
                schema_version=PROBE_SCHEMA_VERSION,
                template_digest=PROBE_TEMPLATE_DIGEST,
                settings=settings,
                response_schema=ProbeArtifact,
            )
            response_observed = mode == RunMode.LIVE
            artifact = ProbeArtifact.model_validate_json(response["content"])
            if artifact != expected:
                raise ValueError("Provider returned a different probe nonce.")
            provider_receipts.append(
                {
                    "artifact_digest": _sha256_json(artifact.model_dump(mode="json")),
                    "artifact_source": (
                        "live_cache" if mode == RunMode.REPLAY else "live_transport"
                    ),
                    "evidence_class": (
                        "cache_integrity_checked_origin_unverified"
                        if mode == RunMode.REPLAY
                        else "live_transport_contract_passed"
                    ),
                    "cache_hit": response.get("_cache_hit") is True,
                    "credential_binding_id": binding.credential_binding_id,
                    "model": binding.model,
                    "provider": binding.provider,
                    "request_digest": _sha256_json(request),
                    "source_transport_receipt": response.get("transport_receipt"),
                    "source_transport_receipt_digest": _sha256_json(
                        response.get("transport_receipt")
                    ),
                    "status": "passed",
                    "transport_invoked_current_run": mode == RunMode.LIVE,
                    "transport_invocation_attempts_current_run": (
                        1 if mode == RunMode.LIVE else 0
                    ),
                    "transport_response_observed_current_run": mode == RunMode.LIVE,
                }
            )
        except (RuntimeError, ValueError, ValidationError) as exc:
            provider_receipts.append(
                {
                    "credential_binding_id": binding.credential_binding_id,
                    "evidence_class": (
                        "cache_replay_contract_failed"
                        if mode == RunMode.REPLAY
                        else "live_transport_contract_failed"
                    ),
                    "error_class": type(exc).__name__,
                    "error_code": _safe_error_code(exc),
                    "model": binding.model,
                    "provider": binding.provider,
                    "request_digest": _sha256_json(request),
                    "status": "failed",
                    "transport_invoked_current_run": mode == RunMode.LIVE,
                    "transport_invocation_attempts_current_run": (
                        1 if mode == RunMode.LIVE else 0
                    ),
                    "transport_response_observed_current_run": bool(
                        response_observed
                        or getattr(exc, "transport_response_observed", False)
                    ),
                }
            )

    passed = all(item["status"] == "passed" for item in provider_receipts)
    recorded_at = datetime.now(timezone.utc)
    receipt = {
        "campaign_id": profile.campaign_id,
        "dataset_write_operations_current_run": 0,
        "evidence_class": (
            (
                "cache_integrity_checked_origin_unverified"
                if passed
                else "cache_replay_contract_failed"
            )
            if mode == RunMode.REPLAY
            else (
                "live_transport_contract_passed"
                if passed
                else "live_transport_contract_failed"
            )
        ),
        "live_call_limit": profile.max_live_calls if mode == RunMode.LIVE else 0,
        "mode": mode.value,
        "orchestrator_retry_limit": profile.orchestrator_retry_limit,
        "passed": passed,
        "transport_responses_observed_current_run": sum(
            int(item["transport_response_observed_current_run"])
            for item in provider_receipts
        ),
        "providers": provider_receipts,
        "recorded_at": recorded_at.isoformat(),
        "schema_version": PROBE_SCHEMA_VERSION,
        "template_digest": PROBE_TEMPLATE_DIGEST,
        "transport_invocation_attempts_current_run": sum(
            item["transport_invocation_attempts_current_run"]
            for item in provider_receipts
        ),
    }
    receipt_digest = _sha256_json(receipt)
    stamp = recorded_at.strftime("%Y%m%dT%H%M%S.%fZ")
    receipt_path = resolved_receipts / (
        f"stage0-5-{mode.value}-{stamp}-{receipt_digest[7:19]}.json"
    )
    _write_receipt(receipt_path, receipt)
    summary = {
        "passed": passed,
        "provider_statuses": {
            item["provider"]: item["status"] for item in provider_receipts
        },
        "receipt_path": str(receipt_path),
        "receipt_digest": receipt_digest,
    }
    return summary, passed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=(RunMode.LIVE.value, RunMode.REPLAY.value),
        required=True,
    )
    parser.add_argument("--profile", type=Path, default=default_profile_path())
    parser.add_argument("--receipts-root", type=Path, default=default_receipts_root())
    parser.add_argument("--nonce", default="stage0-5-wiring-v1")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required confirmation for the two-call live mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    mode = RunMode(args.mode)
    if mode == RunMode.LIVE and not args.confirm_live:
        raise SystemExit("Live mode requires --confirm-live.")
    profile = load_profile(args.profile)
    summary, passed = run_smoke(
        profile=profile,
        mode=mode,
        nonce=args.nonce,
        receipts_root=args.receipts_root,
    )
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
