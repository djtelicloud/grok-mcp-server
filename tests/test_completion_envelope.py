from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from src.completion_envelope import (
    BlockedEnvelope,
    CompleteEnvelope,
    CompletionContractError,
    CompletionNotFinalError,
    EvidenceReceipt,
    EvidenceRef,
    EvidenceResolutionError,
    ProgressEnvelope,
    SchemaCompositionError,
    compose_completion_schema,
    evidence_receipt_digest,
    parse_completion_envelope,
    unwrap_complete_result,
    validate_evidence_refs,
)


NOW = datetime(2030, 1, 1, tzinfo=UTC)
TTL = NOW + timedelta(minutes=5)
ARTIFACT_DIGEST = "sha256:" + "a" * 64


def _receipt(
    *,
    receipt_id: str = "receipt-1",
    attempt_id: str = "attempt-1",
    status: str = "verified",
    expires_at: datetime = TTL,
) -> EvidenceReceipt:
    return EvidenceReceipt(
        receipt_id=receipt_id,
        attempt_id=attempt_id,
        verification_status=status,
        artifact_digest=ARTIFACT_DIGEST,
        verified_at=NOW - timedelta(seconds=1),
        ttl_expires_at=expires_at,
    )


def _reference(receipt: EvidenceReceipt) -> EvidenceRef:
    return EvidenceRef(
        receipt_id=receipt.receipt_id,
        receipt_digest=evidence_receipt_digest(receipt),
    )


def _complete(
    result="final value",
    *,
    receipt: EvidenceReceipt | None = None,
    ttl: datetime = TTL,
) -> CompleteEnvelope:
    refs = () if receipt is None else (_reference(receipt),)
    return CompleteEnvelope(
        version="unigrok-completion/v1",
        status="complete",
        attempt_id="attempt-1",
        ttl_expires_at=ttl,
        evidence_refs=refs,
        result=result,
    )


def test_text_completion_parse_and_unwrap_returns_only_result():
    receipt = _receipt()
    payload = {
        "version": "unigrok-completion/v1",
        "status": "complete",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL.isoformat(),
        "evidence_refs": [_reference(receipt).model_dump(mode="json")],
        "result": "mechanically complete answer",
    }
    envelope = parse_completion_envelope(json.dumps(payload))

    result = unwrap_complete_result(
        envelope,
        expected_attempt_id="attempt-1",
        expected_ttl_expires_at=TTL,
        provider_finish_reason="stop",
        expected_evidence_refs=[_reference(receipt)],
        evidence_requirement="required",
        receipts=[receipt],
        now=NOW,
    )

    assert result == "mechanically complete answer"
    assert "status" not in result
    assert "evidence_refs" not in result


def test_structured_result_supports_nested_defs_and_unions_without_mutation():
    caller_schema = {
        "$defs": {
            "leaf": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "integer", "minimum": 0},
                ]
            }
        },
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/leaf"},
                "minItems": 1,
            }
        },
    }
    original = deepcopy(caller_schema)
    result = {"items": ["one", 2]}

    composed = compose_completion_schema(caller_schema)
    assert caller_schema == original
    complete_ref = composed["discriminator"]["mapping"]["complete"]
    assert complete_ref.startswith("#/$defs/")
    complete_name = complete_ref.rsplit("/", 1)[-1]
    embedded = composed["$defs"][complete_name]["properties"]["result"]
    assert embedded["properties"]["items"]["items"]["$ref"] == (
        f"{complete_ref}/properties/result/$defs/leaf"
    )

    payload = {
        "version": "unigrok-completion/v1",
        "status": "complete",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL.isoformat(),
        "evidence_refs": [],
        "result": result,
    }
    Draft202012Validator(composed).validate(payload)
    envelope = parse_completion_envelope(payload, result_schema=caller_schema)
    unwrapped = unwrap_complete_result(
        envelope,
        expected_attempt_id="attempt-1",
        expected_ttl_expires_at=TTL,
        provider_finish_reason="final_answer",
        expected_evidence_refs=[],
        evidence_requirement="none",
        receipts=[],
        now=NOW,
        result_schema=caller_schema,
    )
    assert unwrapped == result
    assert unwrapped is not envelope.result


def test_caller_schema_rejects_external_and_scope_changing_references():
    with pytest.raises(SchemaCompositionError, match="local JSON pointers"):
        compose_completion_schema({"$ref": "https://example.com/schema.json"})
    with pytest.raises(SchemaCompositionError, match="not safe to embed"):
        compose_completion_schema({"$id": "child", "type": "string"})
    with pytest.raises(SchemaCompositionError, match="not safe to embed"):
        compose_completion_schema({"$dynamicRef": "#node"})
    with pytest.raises(SchemaCompositionError, match="not safe to embed"):
        compose_completion_schema(
            {"$schema": "https://json-schema.org/draft/2020-12/schema"}
        )
    with pytest.raises(SchemaCompositionError, match="resolve to a schema"):
        compose_completion_schema({"title": "not a schema", "$ref": "#/title"})


def test_caller_schema_preserves_ref_shaped_literal_instance_data():
    caller_schema = {
        "const": {
            "$ref": "https://example.com/literal-not-a-schema-reference",
            "$id": "literal-not-a-schema-scope",
        }
    }
    original = deepcopy(caller_schema)
    result = deepcopy(caller_schema["const"])

    composed = compose_completion_schema(caller_schema)
    assert caller_schema == original
    complete_ref = composed["discriminator"]["mapping"]["complete"]
    complete_name = complete_ref.rsplit("/", 1)[-1]
    embedded = composed["$defs"][complete_name]["properties"]["result"]
    assert embedded == original

    payload = {
        "version": "unigrok-completion/v1",
        "status": "complete",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL.isoformat(),
        "evidence_refs": [],
        "result": result,
    }
    Draft202012Validator(composed).validate(payload)
    assert (
        parse_completion_envelope(
            payload,
            result_schema=caller_schema,
        ).result
        == result
    )


@pytest.mark.parametrize("index", ["-1", "+0", "00", "01"])
def test_caller_schema_rejects_non_rfc_array_pointer_indexes(index):
    with pytest.raises(SchemaCompositionError, match="unresolved local JSON pointer"):
        compose_completion_schema(
            {
                "allOf": [{"type": "string"}, {"minLength": 1}],
                "$ref": f"#/allOf/{index}",
            }
        )


def test_caller_schema_accepts_canonical_array_pointer_index():
    composed = compose_completion_schema(
        {
            "allOf": [{"type": "string"}, {"minLength": 1}],
            "$ref": "#/allOf/1",
        }
    )
    Draft202012Validator.check_schema(composed)


@pytest.mark.parametrize("reference", ["#/%", "#/%2", "#/%ZZ", "#/%G0"])
def test_caller_schema_rejects_malformed_percent_escapes(reference):
    with pytest.raises(SchemaCompositionError, match="malformed percent escape"):
        compose_completion_schema({"$defs": {}, "$ref": reference})


@pytest.mark.parametrize("reference", ["#/%FF", "#/%C3%28"])
def test_caller_schema_rejects_invalid_utf8_uri_fragments(reference):
    with pytest.raises(SchemaCompositionError, match="not valid UTF-8"):
        compose_completion_schema({"$defs": {}, "$ref": reference})


@pytest.mark.parametrize("reference", ["#/$defs/%7E2", "#/$defs/%7E"])
def test_percent_decoding_precedes_json_pointer_escape_validation(reference):
    with pytest.raises(SchemaCompositionError, match="invalid local JSON pointer"):
        compose_completion_schema({"$defs": {}, "$ref": reference})


@pytest.mark.parametrize("index", ["%30%30", "%30%31", "%2B0", "%2D1"])
def test_percent_decoded_array_indexes_must_be_canonical(index):
    with pytest.raises(SchemaCompositionError, match="unresolved local JSON pointer"):
        compose_completion_schema(
            {
                "allOf": [{"type": "string"}, {"minLength": 1}],
                "$ref": f"#/allOf/{index}",
            }
        )


@pytest.mark.parametrize(
    "reference",
    ["#/$defs/a%20b"],
)
def test_local_ref_percent_decoding_and_rebasing(reference):
    caller_schema = {
        "$defs": {"a b": {"type": "string", "minLength": 1}},
        "$ref": reference,
    }
    composed = compose_completion_schema(caller_schema)
    complete_ref = composed["discriminator"]["mapping"]["complete"]
    complete_name = complete_ref.rsplit("/", 1)[-1]
    embedded = composed["$defs"][complete_name]["properties"]["result"]
    expected_suffix = reference[1:]
    assert embedded["$ref"] == f"{complete_ref}/properties/result{expected_suffix}"
    Draft202012Validator(composed).validate(
        {
            "version": "unigrok-completion/v1",
            "status": "complete",
            "attempt_id": "attempt-1",
            "ttl_expires_at": TTL.isoformat(),
            "evidence_refs": [],
            "result": "valid",
        }
    )
    assert (
        parse_completion_envelope(
            {
                "version": "unigrok-completion/v1",
                "status": "complete",
                "attempt_id": "attempt-1",
                "ttl_expires_at": TTL.isoformat(),
                "evidence_refs": [],
                "result": "valid",
            },
            result_schema=caller_schema,
        ).result
        == "valid"
    )


def test_percent_encoded_canonical_array_index_is_accepted():
    composed = compose_completion_schema(
        {
            "allOf": [{"type": "string"}, {"minLength": 1}],
            "$ref": "#/allOf/%31",
        }
    )
    Draft202012Validator.check_schema(composed)


def test_percent_encoded_pointer_delimiter_is_not_reinterpreted_as_syntax():
    with pytest.raises(SchemaCompositionError, match="local JSON pointers"):
        compose_completion_schema(
            {"$defs": {"value": {"type": "string"}}, "$ref": "#%2F$defs%2Fvalue"}
        )


def test_structured_result_must_match_caller_schema():
    caller_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "integer"}},
    }
    payload = {
        "version": "unigrok-completion/v1",
        "status": "complete",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL.isoformat(),
        "evidence_refs": [],
        "result": {"value": "wrong"},
    }
    with pytest.raises(CompletionContractError, match="caller schema"):
        parse_completion_envelope(payload, result_schema=caller_schema)


def test_envelope_variants_forbid_extras_and_discriminate():
    progress = parse_completion_envelope(
        {
            "version": "unigrok-completion/v1",
            "status": "progress",
            "attempt_id": "attempt-1",
            "ttl_expires_at": TTL.isoformat(),
            "evidence_refs": [],
            "summary": "One step ran.",
            "next_action": "Run the verifier.",
        }
    )
    assert isinstance(progress, ProgressEnvelope)

    blocked = parse_completion_envelope(
        {
            "version": "unigrok-completion/v1",
            "status": "blocked",
            "attempt_id": "attempt-1",
            "ttl_expires_at": TTL.isoformat(),
            "evidence_refs": [],
            "code": "needs_input",
            "retryable": True,
            "explanation": "A human choice is required.",
            "question": "Which account should be used?",
        }
    )
    assert isinstance(blocked, BlockedEnvelope)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_completion_envelope(
            {
                "version": "unigrok-completion/v1",
                "status": "progress",
                "attempt_id": "attempt-1",
                "ttl_expires_at": TTL.isoformat(),
                "evidence_refs": [],
                "summary": "One step ran.",
                "next_action": "Continue.",
                "result": "must not leak into progress",
            }
        )


@pytest.mark.parametrize("kind", ["progress", "blocked"])
def test_noncomplete_variants_can_never_be_unwrapped(kind):
    common = {
        "version": "unigrok-completion/v1",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL,
        "evidence_refs": (),
    }
    envelope = (
        ProgressEnvelope(
            status="progress",
            summary="Still running.",
            next_action="Continue.",
            **common,
        )
        if kind == "progress"
        else BlockedEnvelope(
            status="blocked",
            code="dependency_unavailable",
            retryable=True,
            explanation="Dependency is offline.",
            **common,
        )
    )
    with pytest.raises(CompletionNotFinalError, match="not final"):
        unwrap_complete_result(
            envelope,
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )


def test_completion_rejects_expired_ttl_and_nonterminal_or_truncated_reason():
    with pytest.raises(CompletionContractError, match="TTL has expired"):
        unwrap_complete_result(
            _complete(ttl=NOW),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=NOW,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )
    for reason in ("length", "tool_calls", "content_filter", "unknown", "error"):
        with pytest.raises(CompletionContractError, match="normal terminal"):
            unwrap_complete_result(
                _complete(),
                expected_attempt_id="attempt-1",
                expected_ttl_expires_at=TTL,
                provider_finish_reason=reason,
                expected_evidence_refs=[],
                evidence_requirement="none",
                receipts=[],
                now=NOW,
            )
    with pytest.raises(CompletionContractError, match="strict JSON"):
        parse_completion_envelope('{"version":"unigrok-completion/v1"')


@pytest.mark.parametrize("non_json_number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_raw_json_rejects_nonfinite_numbers(non_json_number):
    payload = (
        '{"version":"unigrok-completion/v1","status":"complete",'
        '"attempt_id":"attempt-1","ttl_expires_at":"2030-01-01T00:05:00Z",'
        f'"evidence_refs":[],"result":{non_json_number}}}'
    )
    with pytest.raises(CompletionContractError, match="non-JSON|only JSON"):
        parse_completion_envelope(payload, result_schema={"type": "number"})


def test_raw_json_rejects_duplicate_object_keys():
    payload = (
        '{"version":"unigrok-completion/v1","status":"complete",'
        '"attempt_id":"attempt-1","ttl_expires_at":"2030-01-01T00:05:00Z",'
        '"evidence_refs":[],"result":"first","result":"second"}'
    )
    with pytest.raises(CompletionContractError, match="repeats object key 'result'"):
        parse_completion_envelope(payload)


@pytest.mark.parametrize("empty", [None, "", "   ", [], {}])
def test_completion_rejects_empty_result(empty):
    with pytest.raises(CompletionContractError, match="nonempty|nonblank"):
        unwrap_complete_result(
            _complete(empty),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )


def test_evidence_rejects_invented_stale_failed_and_cross_attempt_receipts():
    valid = _receipt()
    envelope = _complete(receipt=valid)

    with pytest.raises(EvidenceResolutionError, match="not supplied"):
        validate_evidence_refs(envelope, [], now=NOW)

    stale = _receipt(expires_at=NOW)
    stale_envelope = _complete(receipt=stale, ttl=NOW)
    with pytest.raises(EvidenceResolutionError, match="stale"):
        validate_evidence_refs(stale_envelope, [stale], now=NOW)

    failed = _receipt(status="failed")
    failed_envelope = _complete(receipt=failed)
    with pytest.raises(EvidenceResolutionError, match="not verified"):
        validate_evidence_refs(failed_envelope, [failed], now=NOW)

    other_attempt = _receipt(attempt_id="attempt-2")
    crossed = _complete(receipt=other_attempt)
    with pytest.raises(EvidenceResolutionError, match="another attempt"):
        validate_evidence_refs(crossed, [other_attempt], now=NOW)


def test_evidence_rejects_digest_substitution_and_duplicate_supplies():
    receipt = _receipt()
    bad_reference = EvidenceRef(
        receipt_id=receipt.receipt_id,
        receipt_digest="sha256:" + "b" * 64,
    )
    envelope = CompleteEnvelope(
        version="unigrok-completion/v1",
        status="complete",
        attempt_id="attempt-1",
        ttl_expires_at=TTL,
        evidence_refs=(bad_reference,),
        result="answer",
    )
    with pytest.raises(EvidenceResolutionError, match="digest"):
        validate_evidence_refs(envelope, [receipt], now=NOW)
    with pytest.raises(EvidenceResolutionError, match="unique"):
        validate_evidence_refs(_complete(), [receipt, receipt], now=NOW)

    different_ttl = _receipt(expires_at=TTL + timedelta(seconds=1))
    mismatched = _complete(receipt=different_ttl)
    with pytest.raises(EvidenceResolutionError, match="different attempt TTL"):
        validate_evidence_refs(mismatched, [different_ttl], now=NOW)


def test_action_completion_rejects_model_omission_of_required_evidence():
    receipt = _receipt()
    promise = _complete("I will run the tests and report back.")

    with pytest.raises(EvidenceResolutionError, match="authoritative receipt set"):
        unwrap_complete_result(
            promise,
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[_reference(receipt)],
            evidence_requirement="required",
            receipts=[receipt],
            now=NOW,
        )


def test_evidence_policy_must_be_explicit_and_internally_consistent():
    receipt = _receipt()
    reference = _reference(receipt)

    with pytest.raises(EvidenceResolutionError, match="needs authoritative"):
        unwrap_complete_result(
            _complete(),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="required",
            receipts=[],
            now=NOW,
        )
    with pytest.raises(EvidenceResolutionError, match="cannot declare expected"):
        unwrap_complete_result(
            _complete(),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[reference],
            evidence_requirement="none",
            receipts=[receipt],
            now=NOW,
        )
    with pytest.raises(EvidenceResolutionError, match="requirement is invalid"):
        unwrap_complete_result(
            _complete(),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="optional",  # type: ignore[arg-type]
            receipts=[],
            now=NOW,
        )


def test_typed_complete_is_not_a_semantic_promise_classifier():
    receipt = _receipt()
    envelope = _complete(
        "I will run the tests and report back.",
        receipt=receipt,
    )

    assert (
        unwrap_complete_result(
            envelope,
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[_reference(receipt)],
            evidence_requirement="required",
            receipts=[receipt],
            now=NOW,
        )
        == "I will run the tests and report back."
    )


def test_typed_models_reject_naive_timestamps_and_unsafe_evidence_ids():
    with pytest.raises(ValidationError, match="timezone-aware"):
        CompleteEnvelope(
            version="unigrok-completion/v1",
            status="complete",
            attempt_id="attempt-1",
            ttl_expires_at=datetime(2030, 1, 1),
            evidence_refs=(),
            result="answer",
        )
    with pytest.raises(ValidationError, match="safe identifier"):
        EvidenceRef(
            receipt_id="../receipt",
            receipt_digest="sha256:" + "a" * 64,
        )

    with pytest.raises(ValidationError, match="version"):
        parse_completion_envelope(
            {
                "status": "complete",
                "attempt_id": "attempt-1",
                "ttl_expires_at": TTL.isoformat(),
                "evidence_refs": [],
                "result": "answer",
            }
        )


def test_text_mode_rejects_structured_result_without_a_caller_schema():
    payload = {
        "version": "unigrok-completion/v1",
        "status": "complete",
        "attempt_id": "attempt-1",
        "ttl_expires_at": TTL.isoformat(),
        "evidence_refs": [],
        "result": {"value": 1},
    }
    with pytest.raises(CompletionContractError, match="must be text"):
        parse_completion_envelope(payload)
    envelope = CompleteEnvelope(
        version="unigrok-completion/v1",
        status="complete",
        attempt_id="attempt-1",
        ttl_expires_at=TTL,
        evidence_refs=(),
        result={"value": 1},
    )
    with pytest.raises(CompletionContractError, match="must be text"):
        unwrap_complete_result(
            envelope,
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )


def test_evidence_reference_count_is_bounded_and_unique():
    references = tuple(
        EvidenceRef(
            receipt_id=f"receipt-{index}",
            receipt_digest="sha256:" + f"{index:064x}",
        )
        for index in range(65)
    )
    with pytest.raises(ValidationError, match="at most 64"):
        CompleteEnvelope(
            version="unigrok-completion/v1",
            status="complete",
            attempt_id="attempt-1",
            ttl_expires_at=TTL,
            evidence_refs=references,
            result="answer",
        )
    with pytest.raises(EvidenceResolutionError, match="too many supplied"):
        validate_evidence_refs(
            _complete(),
            [_receipt(receipt_id=f"supplied-{index}") for index in range(65)],
            now=NOW,
        )


def test_unwrap_binds_model_envelope_to_authoritative_attempt_and_ttl():
    with pytest.raises(CompletionContractError, match="another attempt"):
        unwrap_complete_result(
            _complete(),
            expected_attempt_id="attempt-2",
            expected_ttl_expires_at=TTL,
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )
    with pytest.raises(CompletionContractError, match="does not match"):
        unwrap_complete_result(
            _complete(),
            expected_attempt_id="attempt-1",
            expected_ttl_expires_at=TTL + timedelta(seconds=1),
            provider_finish_reason="stop",
            expected_evidence_refs=[],
            evidence_requirement="none",
            receipts=[],
            now=NOW,
        )


def test_recursive_mapping_is_normalized_to_completion_contract_error():
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(CompletionContractError, match="nesting depth"):
        parse_completion_envelope(recursive)


def test_invalid_supplied_receipt_is_normalized_to_evidence_error():
    with pytest.raises(EvidenceResolutionError, match="violates its contract"):
        validate_evidence_refs(
            _complete(),
            [{"receipt_id": "not-a-complete-receipt"}],
            now=NOW,
        )
