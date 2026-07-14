"""Strict typed completion envelopes for internal model boundaries.

This module deliberately defines only a contract.  It does not choose a model,
route a request, unwrap progress as a result, or assign semantic success.  A
model's ``complete`` status is a typed transport assertion, not proof that its
text is correct or even that it is not a promise.  Action-producing runtimes
must require authoritative evidence and still use a Grok arbiter (and later a
calibrated Needle specialist) for semantic completion.  A runtime integration
may use :func:`unwrap_complete_result` only after it has supplied the provider's
terminal reason, an explicit evidence policy, and the verifier receipts chosen
by runtime authority rather than by the model.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Annotated, Any, Iterable, Literal, Mapping, TypeAlias
from urllib.parse import unquote_to_bytes

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)


COMPLETION_VERSION = "unigrok-completion/v1"
MAX_RESULT_TEXT_CHARS = 1_000_000
MAX_ENVELOPE_BYTES = 1_250_000
MAX_STATUS_TEXT_CHARS = 32_000
MAX_QUESTION_CHARS = 8_000
MAX_EVIDENCE_REFS = 64
MAX_CALLER_SCHEMA_BYTES = 262_144
MAX_CALLER_SCHEMA_DEPTH = 64
MAX_CALLER_SCHEMA_NODES = 20_000

_DIRECT_SUBSCHEMA_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_ARRAY_SUBSCHEMA_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_MAPPING_SUBSCHEMA_KEYWORDS = frozenset(
    {"$defs", "dependentSchemas", "patternProperties", "properties"}
)
_SCOPE_CHANGING_SCHEMA_KEYWORDS = frozenset(
    {
        "$anchor",
        "$dynamicAnchor",
        "$dynamicRef",
        "$id",
        "$recursiveAnchor",
        "$recursiveRef",
        "$schema",
        "$vocabulary",
    }
)

_NORMAL_TERMINAL_REASONS = frozenset({"stop", "final_answer"})
_SAFE_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)
_HEX_CHARS = frozenset("0123456789abcdef")
_URI_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


class CompletionContractError(ValueError):
    """The completion cannot be accepted under the mechanical contract."""


class CompletionNotFinalError(CompletionContractError):
    """A progress or blocked envelope was presented as a final result."""


class EvidenceResolutionError(CompletionContractError):
    """An evidence reference did not resolve to current verified evidence."""


class SchemaCompositionError(CompletionContractError):
    """A caller result schema cannot be safely embedded in the envelope."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _is_safe_identifier(value: str) -> bool:
    return (
        1 <= len(value) <= 128
        and value[0].isalnum()
        and all(character in _SAFE_IDENTIFIER_CHARS for character in value)
    )


def _is_sha256_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in _HEX_CHARS for character in value[7:])
    )


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _decode_local_json_pointer(reference: str) -> tuple[str, ...]:
    """Decode one local URI fragment into RFC 6901 pointer tokens."""

    if not (reference == "#" or reference.startswith("#/")):
        raise SchemaCompositionError(
            "caller schema references must be local JSON pointers"
        )
    fragment = reference[1:]
    cursor = 0
    while cursor < len(fragment):
        if fragment[cursor] != "%":
            cursor += 1
            continue
        if (
            cursor + 2 >= len(fragment)
            or fragment[cursor + 1] not in _URI_HEX_CHARS
            or fragment[cursor + 2] not in _URI_HEX_CHARS
        ):
            raise SchemaCompositionError(
                "caller schema contains a malformed percent escape"
            )
        cursor += 3
    try:
        pointer = unquote_to_bytes(fragment).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SchemaCompositionError(
            "caller schema local reference is not valid UTF-8"
        ) from exc
    if not pointer:
        return ()
    if not pointer.startswith("/"):
        raise SchemaCompositionError(
            "caller schema references must be local JSON pointers"
        )

    segments: list[str] = []
    for raw_segment in pointer[1:].split("/"):
        index = 0
        while index < len(raw_segment):
            if raw_segment[index] == "~":
                if index + 1 >= len(raw_segment) or raw_segment[index + 1] not in {
                    "0",
                    "1",
                }:
                    raise SchemaCompositionError(
                        "caller schema contains an invalid local JSON pointer"
                    )
                index += 2
            else:
                index += 1
        segments.append(raw_segment.replace("~1", "/").replace("~0", "~"))
    return tuple(segments)


class EvidenceRef(_StrictContract):
    """Content-bound pointer to one verifier receipt."""

    receipt_id: Annotated[str, Field(min_length=1, max_length=128)]
    receipt_digest: Annotated[str, Field(min_length=71, max_length=71)]

    @model_validator(mode="after")
    def validate_reference(self) -> "EvidenceRef":
        if not _is_safe_identifier(self.receipt_id):
            raise ValueError("receipt_id must be an opaque safe identifier")
        if not _is_sha256_digest(self.receipt_digest):
            raise ValueError("receipt_digest must be a lowercase sha256 digest")
        return self


class EvidenceReceipt(_StrictContract):
    """Minimal local receipt shape accepted by evidence resolution.

    ``verification_status`` describes the verifier receipt itself.  It is not a
    task-success label and is never promoted into semantic outcome telemetry.
    """

    version: Literal["unigrok-evidence-receipt/v1"] = "unigrok-evidence-receipt/v1"
    receipt_id: Annotated[str, Field(min_length=1, max_length=128)]
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]
    verification_status: Literal["verified", "failed"]
    artifact_digest: Annotated[str, Field(min_length=71, max_length=71)]
    verified_at: datetime
    ttl_expires_at: datetime

    @model_validator(mode="after")
    def validate_receipt(self) -> "EvidenceReceipt":
        for field_name, value in (
            ("receipt_id", self.receipt_id),
            ("attempt_id", self.attempt_id),
        ):
            if not _is_safe_identifier(value):
                raise ValueError(f"{field_name} must be an opaque safe identifier")
        if not _is_sha256_digest(self.artifact_digest):
            raise ValueError("artifact_digest must be a lowercase sha256 digest")
        _require_aware(self.verified_at, "verified_at")
        _require_aware(self.ttl_expires_at, "ttl_expires_at")
        if self.verified_at >= self.ttl_expires_at:
            raise ValueError("receipt verification must precede its TTL expiry")
        return self


class _EnvelopeBase(_StrictContract):
    version: Literal["unigrok-completion/v1"]
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]
    ttl_expires_at: datetime
    evidence_refs: Annotated[
        tuple[EvidenceRef, ...], Field(max_length=MAX_EVIDENCE_REFS)
    ]

    @model_validator(mode="after")
    def validate_common_fields(self) -> "_EnvelopeBase":
        if not _is_safe_identifier(self.attempt_id):
            raise ValueError("attempt_id must be an opaque safe identifier")
        _require_aware(self.ttl_expires_at, "ttl_expires_at")
        receipt_ids = [reference.receipt_id for reference in self.evidence_refs]
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("evidence_refs must not repeat a receipt_id")
        return self


class CompleteEnvelope(_EnvelopeBase):
    status: Literal["complete"]
    result: JsonValue


class ProgressEnvelope(_EnvelopeBase):
    status: Literal["progress"]
    summary: Annotated[str, Field(min_length=1, max_length=MAX_STATUS_TEXT_CHARS)]
    next_action: Annotated[str, Field(min_length=1, max_length=MAX_STATUS_TEXT_CHARS)]

    @field_validator("summary", "next_action")
    @classmethod
    def reject_blank_progress(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("progress text must not be blank")
        return value


BlockedCode: TypeAlias = Literal[
    "needs_input",
    "authorization_required",
    "dependency_unavailable",
    "provider_unavailable",
    "verification_failed",
    "policy_refused",
    "ttl_expired",
    "internal_error",
]
EvidenceRequirement: TypeAlias = Literal["required", "none"]


class BlockedEnvelope(_EnvelopeBase):
    status: Literal["blocked"]
    code: BlockedCode
    retryable: bool
    explanation: Annotated[str, Field(min_length=1, max_length=MAX_STATUS_TEXT_CHARS)]
    question: (
        Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARS)] | None
    ) = None

    @field_validator("explanation", "question")
    @classmethod
    def reject_blank_blocked_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blocked text must not be blank")
        return value

    @model_validator(mode="after")
    def require_input_question(self) -> "BlockedEnvelope":
        if self.code == "needs_input" and self.question is None:
            raise ValueError("needs_input blockers require a question")
        return self


CompletionEnvelope: TypeAlias = Annotated[
    CompleteEnvelope | ProgressEnvelope | BlockedEnvelope,
    Field(discriminator="status"),
]
_COMPLETION_ADAPTER: TypeAdapter[CompletionEnvelope] = TypeAdapter(CompletionEnvelope)


def _json_boundary(payload: Mapping[str, Any] | str | bytes | bytearray) -> str:
    def reject_nonfinite(token: str) -> None:
        raise CompletionContractError(
            f"completion payload contains non-JSON number {token!r}"
        )

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise CompletionContractError(
                    f"completion payload repeats object key {key!r}"
                )
            decoded[key] = value
        return decoded

    if isinstance(payload, (str, bytes, bytearray)):
        try:
            encoded_payload = (
                payload if not isinstance(payload, str) else payload.encode("utf-8")
            )
        except UnicodeError as exc:
            raise CompletionContractError(
                "completion payload must be valid UTF-8 JSON"
            ) from exc
        if len(encoded_payload) > MAX_ENVELOPE_BYTES:
            raise CompletionContractError("completion payload exceeds the size bound")
        try:
            payload = json.loads(
                payload,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_nonfinite,
            )
        except CompletionContractError:
            raise
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise CompletionContractError(
                "completion payload must be strict JSON"
            ) from exc
    elif not isinstance(payload, Mapping):
        raise CompletionContractError("completion payload must be a JSON object")

    def require_json(value: Any) -> None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, list):
            for item in value:
                require_json(item)
            return
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise CompletionContractError("completion object keys must be strings")
            for item in value.values():
                require_json(item)
            return
        raise CompletionContractError(
            "completion payload must contain only JSON values"
        )

    if not isinstance(payload, Mapping):
        raise CompletionContractError("completion payload must be a JSON object")
    require_json(payload)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        encoded_size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CompletionContractError(
            "completion payload must contain only JSON values"
        ) from exc
    if encoded_size > MAX_ENVELOPE_BYTES:
        raise CompletionContractError("completion payload exceeds the size bound")
    return encoded


def _validate_caller_schema_shape(schema: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = deepcopy(dict(schema))
        encoded = json.dumps(
            copied,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SchemaCompositionError("caller result schema must be JSON") from exc
    if len(encoded) > MAX_CALLER_SCHEMA_BYTES:
        raise SchemaCompositionError("caller result schema exceeds the size bound")

    nodes = 0

    def enforce_bounds(value: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > MAX_CALLER_SCHEMA_DEPTH:
            raise SchemaCompositionError("caller result schema exceeds the depth bound")
        if nodes > MAX_CALLER_SCHEMA_NODES:
            raise SchemaCompositionError("caller result schema exceeds the node bound")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise SchemaCompositionError("caller schema keys must be strings")
                enforce_bounds(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                enforce_bounds(child, depth + 1)

    enforce_bounds(copied, 0)
    try:
        Draft202012Validator.check_schema(copied)
    except SchemaError as exc:
        raise SchemaCompositionError("caller result schema is invalid") from exc

    schema_paths: set[tuple[str, ...]] = set()
    local_refs: list[str] = []

    def walk_schema(value: Any, path: tuple[str, ...]) -> None:
        schema_paths.add(path)
        if isinstance(value, bool):
            return
        if not isinstance(value, dict):  # pragma: no cover - metaschema invariant
            raise SchemaCompositionError("caller result schema is invalid")
        for key in _SCOPE_CHANGING_SCHEMA_KEYWORDS.intersection(value):
            raise SchemaCompositionError(
                f"caller schema keyword {key} is not safe to embed"
            )
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not (
                reference == "#" or reference.startswith("#/")
            ):
                raise SchemaCompositionError(
                    "caller schema references must be local JSON pointers"
                )
            local_refs.append(reference)
        for keyword in _DIRECT_SUBSCHEMA_KEYWORDS.intersection(value):
            walk_schema(value[keyword], (*path, keyword))
        for keyword in _ARRAY_SUBSCHEMA_KEYWORDS.intersection(value):
            for index, child in enumerate(value[keyword]):
                walk_schema(child, (*path, keyword, str(index)))
        for keyword in _MAPPING_SUBSCHEMA_KEYWORDS.intersection(value):
            for name, child in value[keyword].items():
                walk_schema(child, (*path, keyword, name))

    walk_schema(copied, ())

    def resolve_pointer(reference: str) -> None:
        current: Any = copied
        path: list[str] = []
        for segment in _decode_local_json_pointer(reference):
            try:
                if isinstance(current, list):
                    if not (
                        segment == "0"
                        or (
                            segment
                            and segment[0] in "123456789"
                            and all(character in "0123456789" for character in segment)
                        )
                    ):
                        raise ValueError(segment)
                    current = current[int(segment)]
                elif isinstance(current, dict):
                    current = current[segment]
                else:
                    raise KeyError(segment)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise SchemaCompositionError(
                    "caller schema contains an unresolved local JSON pointer"
                ) from exc
            path.append(segment)
        if tuple(path) not in schema_paths or not isinstance(current, (dict, bool)):
            raise SchemaCompositionError(
                "caller schema local references must resolve to a schema"
            )

    for reference in local_refs:
        resolve_pointer(reference)
    return copied


def _rebase_local_refs(value: Any, root_pointer: str) -> Any:
    if isinstance(value, bool):
        return value
    rebound = deepcopy(value)
    reference = value.get("$ref")
    if reference is not None:
        suffix = "" if reference == "#" else reference[1:]
        rebound["$ref"] = f"{root_pointer}{suffix}"
    for keyword in _DIRECT_SUBSCHEMA_KEYWORDS.intersection(value):
        rebound[keyword] = _rebase_local_refs(value[keyword], root_pointer)
    for keyword in _ARRAY_SUBSCHEMA_KEYWORDS.intersection(value):
        rebound[keyword] = [
            _rebase_local_refs(child, root_pointer) for child in value[keyword]
        ]
    for keyword in _MAPPING_SUBSCHEMA_KEYWORDS.intersection(value):
        rebound[keyword] = {
            name: _rebase_local_refs(child, root_pointer)
            for name, child in value[keyword].items()
        }
    return rebound


def compose_completion_schema(
    result_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a strict JSON Schema for one completion envelope.

    A caller schema is copied, checked for remote or scope-changing references,
    and embedded under ``complete.result``.  Local JSON-pointer references are
    rebased to that location.  The caller's object is never mutated.
    """

    schema = deepcopy(_COMPLETION_ADAPTER.json_schema())
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):  # pragma: no cover - Pydantic invariant
        raise RuntimeError("completion schema discriminator is unavailable")
    mapping = discriminator.get("mapping")
    if not isinstance(mapping, dict):  # pragma: no cover - Pydantic invariant
        raise RuntimeError("completion schema mapping is unavailable")
    complete_pointer = mapping.get("complete")
    if not isinstance(complete_pointer, str) or not complete_pointer.startswith("#/"):
        raise RuntimeError("complete envelope schema is unavailable")

    complete_schema: Any = schema
    for segment in complete_pointer[2:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        complete_schema = complete_schema[segment]
    result_pointer = f"{complete_pointer}/properties/result"
    if result_schema is None:
        embedded: dict[str, Any] = {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RESULT_TEXT_CHARS,
        }
    else:
        checked = _validate_caller_schema_shape(result_schema)
        embedded = _rebase_local_refs(checked, result_pointer)
    complete_schema["properties"]["result"] = embedded
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    return schema


def parse_completion_envelope(
    payload: Mapping[str, Any] | str | bytes | bytearray,
    *,
    result_schema: Mapping[str, Any] | None = None,
) -> CompletionEnvelope:
    """Parse an envelope locally and validate a complete result's caller schema."""

    envelope = _COMPLETION_ADAPTER.validate_json(_json_boundary(payload))
    if isinstance(envelope, CompleteEnvelope):
        _validate_result_contract(envelope.result, result_schema)
    return envelope


def _validate_result_contract(
    result: JsonValue,
    result_schema: Mapping[str, Any] | None,
) -> None:
    if result_schema is None:
        if not isinstance(result, str):
            raise CompletionContractError(
                "complete result must be text when no caller schema is supplied"
            )
        if len(result) > MAX_RESULT_TEXT_CHARS:
            raise CompletionContractError("complete text result exceeds the size bound")
        return
    checked = _validate_caller_schema_shape(result_schema)
    try:
        Draft202012Validator(checked).validate(result)
    except JsonSchemaError as exc:
        raise CompletionContractError(
            "complete result does not match the caller schema"
        ) from exc


def evidence_receipt_digest(receipt: EvidenceReceipt) -> str:
    """Return the canonical content digest used by an :class:`EvidenceRef`."""

    payload = json.dumps(
        receipt.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def validate_evidence_refs(
    envelope: CompletionEnvelope,
    receipts: Iterable[EvidenceReceipt],
    *,
    now: datetime | None = None,
) -> tuple[EvidenceReceipt, ...]:
    """Resolve envelope references to current, verified, same-attempt receipts.

    This resolver intentionally says nothing about which receipts were required
    for the attempt.  Finalization must additionally compare the model-authored
    references with a runtime-authoritative set via
    :func:`unwrap_complete_result`.
    """

    checked_at = now or datetime.now(UTC)
    _require_aware(checked_at, "now")
    by_id: dict[str, EvidenceReceipt] = {}
    for receipt in receipts:
        if len(by_id) >= MAX_EVIDENCE_REFS:
            raise EvidenceResolutionError("too many supplied evidence receipts")
        if not isinstance(receipt, EvidenceReceipt):
            receipt = EvidenceReceipt.model_validate(receipt)
        if receipt.receipt_id in by_id:
            raise EvidenceResolutionError("supplied receipt IDs must be unique")
        by_id[receipt.receipt_id] = receipt

    resolved: list[EvidenceReceipt] = []
    for reference in envelope.evidence_refs:
        receipt = by_id.get(reference.receipt_id)
        if receipt is None:
            raise EvidenceResolutionError(
                f"evidence receipt {reference.receipt_id!r} was not supplied"
            )
        if receipt.attempt_id != envelope.attempt_id:
            raise EvidenceResolutionError("evidence receipt belongs to another attempt")
        if receipt.ttl_expires_at != envelope.ttl_expires_at:
            raise EvidenceResolutionError(
                "evidence receipt has a different attempt TTL"
            )
        if receipt.verification_status != "verified":
            raise EvidenceResolutionError("evidence receipt was not verified")
        if receipt.verified_at > checked_at:
            raise EvidenceResolutionError("evidence receipt is dated in the future")
        if checked_at >= receipt.ttl_expires_at:
            raise EvidenceResolutionError("evidence receipt is stale")
        if evidence_receipt_digest(receipt) != reference.receipt_digest:
            raise EvidenceResolutionError("evidence receipt digest does not match")
        resolved.append(receipt)
    return tuple(resolved)


def _bind_authoritative_evidence(
    envelope: CompleteEnvelope,
    expected_evidence_refs: Iterable[EvidenceRef],
    evidence_requirement: EvidenceRequirement,
) -> None:
    if evidence_requirement not in {"required", "none"}:
        raise EvidenceResolutionError("evidence requirement is invalid")

    expected: dict[str, EvidenceRef] = {}
    for reference in expected_evidence_refs:
        if len(expected) >= MAX_EVIDENCE_REFS:
            raise EvidenceResolutionError("too many expected evidence references")
        if not isinstance(reference, EvidenceRef):
            try:
                reference = EvidenceRef.model_validate(reference)
            except ValueError as exc:
                raise EvidenceResolutionError(
                    "expected evidence reference is invalid"
                ) from exc
        if reference.receipt_id in expected:
            raise EvidenceResolutionError(
                "expected evidence receipt IDs must be unique"
            )
        expected[reference.receipt_id] = reference

    if evidence_requirement == "required" and not expected:
        raise EvidenceResolutionError(
            "evidence-required completion needs authoritative receipt references"
        )
    if evidence_requirement == "none" and expected:
        raise EvidenceResolutionError(
            "no-evidence completion cannot declare expected receipt references"
        )

    actual = {reference.receipt_id: reference for reference in envelope.evidence_refs}
    if actual != expected:
        raise EvidenceResolutionError(
            "completion evidence does not match the authoritative receipt set"
        )


def unwrap_complete_result(
    envelope: CompletionEnvelope,
    *,
    expected_attempt_id: str,
    expected_ttl_expires_at: datetime,
    provider_finish_reason: str,
    expected_evidence_refs: Iterable[EvidenceRef],
    evidence_requirement: EvidenceRequirement,
    receipts: Iterable[EvidenceReceipt],
    now: datetime | None = None,
    result_schema: Mapping[str, Any] | None = None,
) -> JsonValue:
    """Mechanically accept and return only a complete result.

    This is intentionally not a semantic-success decision.  It checks the
    authoritative attempt and TTL, the provider's terminal reason, result
    presence, the runtime-selected evidence set, and receipt resolution, then
    returns a defensive copy of the result without leaking wrapper fields.

    ``evidence_requirement`` has no default.  Action-producing paths must use
    ``"required"`` with a nonempty authoritative reference set.  The ``"none"``
    policy is an explicit escape hatch for direct-answer paths and rejects any
    expected or model-supplied references.  Neither policy proves that free text
    is semantically complete: a typed ``complete`` result can still contain a
    promise, so Grok arbitration (and later calibrated Needle evaluation) stays
    mandatory wherever semantic completion matters.
    """

    if not isinstance(envelope, CompleteEnvelope):
        raise CompletionNotFinalError(
            f"{envelope.status} envelopes are not final results"
        )
    checked_at = now or datetime.now(UTC)
    _require_aware(checked_at, "now")
    _require_aware(expected_ttl_expires_at, "expected_ttl_expires_at")
    if not _is_safe_identifier(expected_attempt_id):
        raise CompletionContractError("expected_attempt_id must be safe")
    if envelope.attempt_id != expected_attempt_id:
        raise CompletionContractError("completion belongs to another attempt")
    if envelope.ttl_expires_at != expected_ttl_expires_at:
        raise CompletionContractError("completion TTL does not match the attempt")
    if checked_at >= expected_ttl_expires_at:
        raise CompletionContractError("completion TTL has expired")
    if provider_finish_reason not in _NORMAL_TERMINAL_REASONS:
        raise CompletionContractError(
            "provider did not report a normal terminal reason"
        )
    result = envelope.result
    if result is None or result == "" or result == [] or result == {}:
        raise CompletionContractError("complete result must be nonempty")
    if isinstance(result, str) and not result.strip():
        raise CompletionContractError("complete result must be nonblank")
    _validate_result_contract(result, result_schema)
    _bind_authoritative_evidence(
        envelope,
        expected_evidence_refs,
        evidence_requirement,
    )
    validate_evidence_refs(envelope, receipts, now=checked_at)
    return deepcopy(result)


__all__ = [
    "BlockedCode",
    "BlockedEnvelope",
    "COMPLETION_VERSION",
    "CompleteEnvelope",
    "CompletionContractError",
    "CompletionEnvelope",
    "CompletionNotFinalError",
    "EvidenceReceipt",
    "EvidenceRequirement",
    "EvidenceRef",
    "EvidenceResolutionError",
    "ProgressEnvelope",
    "SchemaCompositionError",
    "compose_completion_schema",
    "evidence_receipt_digest",
    "parse_completion_envelope",
    "unwrap_complete_result",
    "validate_evidence_refs",
]
