"""Canonical UniGrok Insider IntelligenceCapsule v1 primitives.

This module is intentionally independent from the public-consumer SQLite
store.  It validates and canonicalizes the immutable envelopes exchanged by
Insider local and cloud executors; it does not persist them.

The protocol uses a restricted domain of RFC 8785 JSON Canonicalization:
ASCII snake_case object keys, NFC metadata strings, no nulls or floating-point
values, and only JavaScript-safe integers.  Payload strings remain byte-exact.
Those restrictions make the emitted bytes identical in Python and JavaScript
without a numeric compatibility shim.  This module is not a general-purpose
JCS implementation; it accepts only the UniGrok profile and uses an explicit
RFC-compatible string escape table.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


PROTOCOL = "org.grokmcp.intelligence-capsule"
VERSION = 1
CAPSULE_ID_PREFIX = "ucap1:sha256:"
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_PARENTS = 64
MAX_ENVELOPE_BYTES = 1024 * 1024

CAPSULE_KINDS = frozenset(
    {
        "benchmark",
        "candidate",
        "decision",
        "evaluation",
        "failure",
        "lesson",
        "observation",
        "policy",
        "promotion",
        "release",
        "task",
    }
)
ACTOR_ROLES = frozenset({"admin", "automation", "contributor"})
EXECUTION_RUNTIMES = frozenset({"cloud", "local"})
EXECUTION_PLANES = frozenset({"api", "cli", "none"})
EXECUTION_TARGETS = frozenset({"cloud_api", "deterministic", "local_api", "local_cli"})
SIGNATURE_PROFILES = frozenset({"openpgp", "sigstore_bundle", "ssh_ed25519"})

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPSULE_ID_RE = re.compile(r"^ucap1:sha256:[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_GIT_OID_RE = re.compile(r"^(?:sha1:[a-f0-9]{40}|sha256:[a-f0-9]{64})$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_PAYLOAD_SCHEMA_RE = re.compile(r"^org\.grokmcp\.[a-z][a-z0-9_.-]*\.v[1-9][0-9]*$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TIMESTAMP_RE = re.compile(
    r"^(?!0000)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_UUID7_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-7[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]{16,16384}$")


class CapsuleValidationError(ValueError):
    """Raised when a value cannot be a canonical IntelligenceCapsule."""


def canonicalize(value: Any) -> bytes:
    """Return constrained RFC 8785-compatible UTF-8 bytes for ``value``."""

    normalized = _validate_canonical_value(value, path="$")
    try:
        return _serialize_canonical(normalized).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise CapsuleValidationError(
            f"value is not canonical UTF-8 JSON: {exc}"
        ) from exc


def parse_canonical(raw: bytes) -> Any:
    """Decode canonical wire bytes and reject alternate JSON spellings.

    Verifiers must call this on the original bytes instead of accepting an
    already-parsed framework object.  Re-encoding catches duplicate keys,
    whitespace, alternate escapes, key-order drift, BOMs, and numeric aliases.
    """

    if type(raw) is not bytes:
        raise CapsuleValidationError("canonical wire input must be bytes")
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise CapsuleValidationError("canonical wire input exceeds the 1 MiB limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise CapsuleValidationError("canonical JSON must not contain a UTF-8 BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CapsuleValidationError("canonical JSON must be strict UTF-8") from exc

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise CapsuleValidationError(f"duplicate JSON key {key!r}")
            output[key] = value
        return output

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_float=lambda value: _raise_float(value),
            parse_constant=lambda value: _raise_float(value),
        )
    except CapsuleValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise CapsuleValidationError(f"invalid canonical JSON: {exc}") from exc
    if canonicalize(value) != raw:
        raise CapsuleValidationError("wire bytes are valid JSON but not canonical")
    return value


def digest_body(body: Mapping[str, Any]) -> str:
    """Return the lowercase SHA-256 digest of a validated capsule body."""

    validate_body(body)
    return hashlib.sha256(canonicalize(body)).hexdigest()


def capsule_id(body: Mapping[str, Any]) -> str:
    """Return the stable protocol identity for ``body``."""

    return f"{CAPSULE_ID_PREFIX}{digest_body(body)}"


def build_envelope(
    body: Mapping[str, Any],
    *,
    signatures: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build and validate an IntelligenceCapsule envelope."""

    validate_body(body)
    signature_list = [dict(item) for item in signatures]
    envelope = {
        "body": dict(body),
        "digest": {"algorithm": "sha-256", "value": digest_body(body)},
        "signatures": signature_list,
    }
    validate_envelope_integrity(envelope)
    return envelope


def validate_envelope_integrity(value: Mapping[str, Any]) -> None:
    """Validate structure and body digest, not authorship or signature validity."""

    envelope = _require_mapping(value, "$", {"body", "digest", "signatures"})
    body = _require_mapping(envelope["body"], "$.body")
    validate_body(body)

    digest = _require_mapping(envelope["digest"], "$.digest", {"algorithm", "value"})
    if digest["algorithm"] != "sha-256" or not _matches(digest["value"], _DIGEST_RE):
        raise CapsuleValidationError("$.digest must contain a lowercase sha-256 value")
    expected = hashlib.sha256(canonicalize(body)).hexdigest()
    if digest["value"] != expected:
        raise CapsuleValidationError("$.digest.value does not match the canonical body")

    signatures = _require_list(envelope["signatures"], "$.signatures", maximum=32)
    signature_order: list[tuple[str, str, str]] = []
    for index, raw in enumerate(signatures):
        path = f"$.signatures[{index}]"
        signature = _require_mapping(raw, path, {"key_id", "profile", "value"})
        if signature["profile"] not in SIGNATURE_PROFILES:
            raise CapsuleValidationError(f"{path}.profile is unsupported")
        if not _matches(signature["key_id"], _IDENTIFIER_RE):
            raise CapsuleValidationError(f"{path}.key_id is invalid")
        if not _matches(signature["value"], _BASE64URL_RE):
            raise CapsuleValidationError(f"{path}.value is not unpadded base64url")
        signature_order.append(
            (signature["profile"], signature["key_id"], signature["value"])
        )
    if signature_order != sorted(signature_order) or len(signature_order) != len(
        set(signature_order)
    ):
        raise CapsuleValidationError(
            "$.signatures must be unique and canonically sorted"
        )

    _validate_canonical_value(envelope, path="$")
    if len(canonicalize(envelope)) > MAX_ENVELOPE_BYTES:
        raise CapsuleValidationError("envelope exceeds the 1 MiB canonical limit")


def validate_body(value: Mapping[str, Any]) -> None:
    """Validate the normative IntelligenceCapsule v1 body schema."""

    required = {
        "actor",
        "created_at",
        "evidence",
        "kind",
        "parents",
        "payload",
        "protocol",
        "provenance",
        "run_id",
        "subject",
        "version",
    }
    optional = {"execution", "metrics"}
    body = _require_mapping(value, "$.body", required, optional)
    if body["protocol"] != PROTOCOL or body["version"] != VERSION:
        raise CapsuleValidationError(
            "$.body protocol/version is not IntelligenceCapsule v1"
        )
    if body["kind"] not in CAPSULE_KINDS:
        raise CapsuleValidationError("$.body.kind is unsupported")
    if not _matches(body["run_id"], _UUID7_RE):
        raise CapsuleValidationError("$.body.run_id must be a lowercase UUIDv7")
    if not _matches(body["created_at"], _TIMESTAMP_RE):
        raise CapsuleValidationError(
            "$.body.created_at must be UTC with exactly millisecond precision"
        )
    try:
        datetime.strptime(body["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError) as exc:
        raise CapsuleValidationError(
            "$.body.created_at is not a real UTC timestamp"
        ) from exc
    _require_nfc(body["created_at"], "$.body.created_at")

    subject = _require_mapping(
        body["subject"], "$.body.subject", {"commit", "repository"}
    )
    if not _matches(subject["repository"], _REPOSITORY_RE):
        raise CapsuleValidationError("$.body.subject.repository is invalid")
    if not _matches(subject["commit"], _COMMIT_RE):
        raise CapsuleValidationError(
            "$.body.subject.commit must be a full Git object id"
        )
    _require_nfc(subject["repository"], "$.body.subject.repository")

    parents = _require_list(body["parents"], "$.body.parents", maximum=MAX_PARENTS)
    if not all(_matches(item, _CAPSULE_ID_RE) for item in parents):
        raise CapsuleValidationError("$.body.parents contains an invalid capsule id")
    _require_sorted_unique(parents, "$.body.parents")

    actor = _require_mapping(
        body["actor"], "$.body.actor", {"agent_id", "github_login", "role"}
    )
    _require_nfc(actor["agent_id"], "$.body.actor.agent_id")
    _require_nfc(actor["github_login"], "$.body.actor.github_login")
    if not _matches(actor["agent_id"], _IDENTIFIER_RE):
        raise CapsuleValidationError("$.body.actor.agent_id is invalid")
    if not _matches(actor["github_login"], _IDENTIFIER_RE):
        raise CapsuleValidationError("$.body.actor.github_login is invalid")
    if actor["role"] not in ACTOR_ROLES:
        raise CapsuleValidationError("$.body.actor.role is invalid")

    payload = _require_mapping(body["payload"], "$.body.payload", {"data", "schema"})
    if not _matches(payload["schema"], _PAYLOAD_SCHEMA_RE):
        raise CapsuleValidationError("$.body.payload.schema is invalid")
    _require_nfc(payload["schema"], "$.body.payload.schema")
    _require_mapping(payload["data"], "$.body.payload.data")

    evidence = _require_list(body["evidence"], "$.body.evidence", maximum=256)
    evidence_order: list[tuple[str, str]] = []
    for index, raw in enumerate(evidence):
        path = f"$.body.evidence[{index}]"
        item = _require_mapping(
            raw,
            path,
            {"bytes", "media_type", "name", "sha256"},
            {"git_oid"},
        )
        if not _matches(item["name"], _IDENTIFIER_RE):
            raise CapsuleValidationError(f"{path}.name is invalid")
        if not _matches(item["media_type"], _MEDIA_TYPE_RE):
            raise CapsuleValidationError(f"{path}.media_type is invalid")
        if type(item["bytes"]) is not int:
            raise CapsuleValidationError(f"{path}.bytes must be an integer")
        if not 0 <= item["bytes"] <= MAX_SAFE_INTEGER:
            raise CapsuleValidationError(f"{path}.bytes is outside the safe range")
        if not _matches(item["sha256"], _DIGEST_RE):
            raise CapsuleValidationError(f"{path}.sha256 is invalid")
        if "git_oid" in item and not _matches(item["git_oid"], _GIT_OID_RE):
            raise CapsuleValidationError(f"{path}.git_oid is invalid")
        _require_nfc(item["name"], f"{path}.name")
        _require_nfc(item["media_type"], f"{path}.media_type")
        evidence_order.append((item["name"], item["sha256"]))
    if evidence_order != sorted(evidence_order) or len(evidence_order) != len(
        set(evidence_order)
    ):
        raise CapsuleValidationError("$.body.evidence must be unique and sorted")

    provenance = _require_mapping(
        body["provenance"],
        "$.body.provenance",
        {"generator", "generator_version", "source_commit"},
    )
    if not _matches(provenance["generator"], _IDENTIFIER_RE):
        raise CapsuleValidationError("$.body.provenance.generator is invalid")
    if not _matches(provenance["generator_version"], _IDENTIFIER_RE):
        raise CapsuleValidationError("$.body.provenance.generator_version is invalid")
    if not _matches(provenance["source_commit"], _COMMIT_RE):
        raise CapsuleValidationError("$.body.provenance.source_commit is invalid")
    _require_nfc(provenance["generator"], "$.body.provenance.generator")
    _require_nfc(provenance["generator_version"], "$.body.provenance.generator_version")

    if "execution" in body:
        execution = _require_mapping(
            body["execution"],
            "$.body.execution",
            {"model", "plane", "runtime", "target"},
        )
        if not _matches(execution["model"], _IDENTIFIER_RE):
            raise CapsuleValidationError("$.body.execution.model is invalid")
        if execution["runtime"] not in EXECUTION_RUNTIMES:
            raise CapsuleValidationError("$.body.execution.runtime is invalid")
        if execution["plane"] not in EXECUTION_PLANES:
            raise CapsuleValidationError("$.body.execution.plane is invalid")
        if execution["target"] not in EXECUTION_TARGETS:
            raise CapsuleValidationError("$.body.execution.target is invalid")
        _require_nfc(execution["model"], "$.body.execution.model")

    metrics = _require_list(body.get("metrics", []), "$.body.metrics", maximum=256)
    metric_order: list[tuple[str, str]] = []
    for index, raw in enumerate(metrics):
        path = f"$.body.metrics[{index}]"
        metric = _require_mapping(raw, path, {"name", "unit", "value"})
        if not _matches(metric["name"], _IDENTIFIER_RE):
            raise CapsuleValidationError(f"{path}.name is invalid")
        if not _matches(metric["unit"], _IDENTIFIER_RE):
            raise CapsuleValidationError(f"{path}.unit is invalid")
        if not _matches(metric["value"], _DECIMAL_RE):
            raise CapsuleValidationError(f"{path}.value must be a plain decimal string")
        _require_nfc(metric["name"], f"{path}.name")
        _require_nfc(metric["unit"], f"{path}.unit")
        metric_order.append((metric["name"], metric["unit"]))
    if metric_order != sorted(metric_order) or len(metric_order) != len(
        set(metric_order)
    ):
        raise CapsuleValidationError("$.body.metrics must be unique and sorted")

    _validate_canonical_value(body, path="$.body")
    if len(canonicalize(body)) > 256 * 1024:
        raise CapsuleValidationError("$.body exceeds the 256 KiB canonical limit")


def _validate_canonical_value(
    value: Any,
    *,
    path: str,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _nodes: list[int] | None = None,
) -> Any:
    if _depth > 64:
        raise CapsuleValidationError(f"{path} exceeds the maximum nesting depth")
    _seen = _seen if _seen is not None else set()
    _nodes = _nodes if _nodes is not None else [0]
    _nodes[0] += 1
    if _nodes[0] > 100_000:
        raise CapsuleValidationError("canonical value exceeds the maximum node count")
    if value is None:
        raise CapsuleValidationError(
            f"{path} must omit inapplicable fields, not use null"
        )
    if type(value) is bool:
        return value
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CapsuleValidationError(f"{path} integer is outside the safe range")
        return value
    if type(value) is float:
        raise CapsuleValidationError(
            f"{path} must use a decimal string instead of float"
        )
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CapsuleValidationError(
                f"{path} contains an invalid Unicode scalar"
            ) from exc
        return value
    if type(value) is dict:
        identity = id(value)
        if identity in _seen:
            raise CapsuleValidationError(f"{path} contains a reference cycle")
        _seen.add(identity)
        output: dict[str, Any] = {}
        try:
            for key, child in value.items():
                if type(key) is not str or not _KEY_RE.fullmatch(key):
                    raise CapsuleValidationError(
                        f"{path} contains a non-canonical object key"
                    )
                output[key] = _validate_canonical_value(
                    child,
                    path=f"{path}.{key}",
                    _depth=_depth + 1,
                    _seen=_seen,
                    _nodes=_nodes,
                )
            return output
        finally:
            _seen.remove(identity)
    if type(value) is list:
        identity = id(value)
        if identity in _seen:
            raise CapsuleValidationError(f"{path} contains a reference cycle")
        _seen.add(identity)
        try:
            return [
                _validate_canonical_value(
                    child,
                    path=f"{path}[{index}]",
                    _depth=_depth + 1,
                    _seen=_seen,
                    _nodes=_nodes,
                )
                for index, child in enumerate(value)
            ]
        finally:
            _seen.remove(identity)
    raise CapsuleValidationError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )


def _serialize_canonical(value: Any) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return _quote_canonical_string(value)
    if type(value) is list:
        return "[" + ",".join(_serialize_canonical(item) for item in value) + "]"
    if type(value) is dict:
        return (
            "{"
            + ",".join(
                f"{_quote_canonical_string(key)}:{_serialize_canonical(value[key])}"
                for key in sorted(value)
            )
            + "}"
        )
    raise CapsuleValidationError(
        f"cannot serialize canonical value {type(value).__name__}"
    )


def _quote_canonical_string(value: str) -> str:
    short_escapes = {
        0x08: "\\b",
        0x09: "\\t",
        0x0A: "\\n",
        0x0C: "\\f",
        0x0D: "\\r",
    }
    output = ['"']
    for character in value:
        codepoint = ord(character)
        if character == '"':
            output.append('\\"')
        elif character == "\\":
            output.append("\\\\")
        elif codepoint in short_escapes:
            output.append(short_escapes[codepoint])
        elif codepoint <= 0x1F:
            output.append(f"\\u{codepoint:04x}")
        else:
            output.append(character)
    output.append('"')
    return "".join(output)


def _require_mapping(
    value: Any,
    path: str,
    required: set[str] | None = None,
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise CapsuleValidationError(f"{path} must be an object")
    required = required or set()
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional if required or optional else set()
    if missing:
        raise CapsuleValidationError(f"{path} is missing {sorted(missing)}")
    if extra:
        raise CapsuleValidationError(
            f"{path} contains unsupported fields {sorted(extra)}"
        )
    return value


def _require_list(value: Any, path: str, *, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise CapsuleValidationError(f"{path} must be an array")
    if len(value) > maximum:
        raise CapsuleValidationError(f"{path} exceeds its maximum of {maximum} items")
    return value


def _require_sorted_unique(values: list[Any], path: str) -> None:
    if values != sorted(values) or len(values) != len(set(values)):
        raise CapsuleValidationError(
            f"{path} must be unique and lexicographically sorted"
        )


def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _require_nfc(value: Any, path: str) -> None:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise CapsuleValidationError(f"{path} must be Unicode NFC")


def _raise_float(value: str) -> Any:
    raise CapsuleValidationError(
        f"canonical JSON forbids floating-point token {value!r}; use a decimal string"
    )
