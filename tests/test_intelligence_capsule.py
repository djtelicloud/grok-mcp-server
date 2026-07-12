import copy
import inspect
import json
from pathlib import Path

import pytest

from src import intelligence_capsule as capsule


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "intelligence_capsule" / "v1"


def golden_envelope() -> dict:
    return json.loads(
        (FIXTURE_DIR / "golden-envelope.json").read_text(encoding="utf-8")
    )


def golden_bytes() -> bytes:
    return bytes.fromhex((FIXTURE_DIR / "golden-body.hex").read_text().strip())


def test_python_matches_the_cross_language_golden_vector():
    envelope = golden_envelope()
    expected_digest = (FIXTURE_DIR / "golden-body.sha256").read_text().strip()

    assert capsule.canonicalize(envelope["body"]) == golden_bytes()
    assert capsule.digest_body(envelope["body"]) == expected_digest
    assert capsule.capsule_id(envelope["body"]) == f"ucap1:sha256:{expected_digest}"
    capsule.validate_envelope_integrity(envelope)


def test_builder_recomputes_digest_without_changing_body_identity():
    original = golden_envelope()
    built = capsule.build_envelope(original["body"])

    assert built == original
    built["signatures"] = [
        {
            "key_id": "admin-key-1",
            "profile": "ssh_ed25519",
            "value": "A" * 86,
        }
    ]
    capsule.validate_envelope_integrity(built)
    assert capsule.capsule_id(built["body"]) == capsule.capsule_id(original["body"])


def test_payload_unicode_is_preserved_but_metadata_must_be_nfc():
    envelope = golden_envelope()
    assert envelope["body"]["payload"]["data"]["decomposed_payload"] == "e\u0301"
    assert b"e\xcc\x81" in capsule.canonicalize(envelope["body"])

    invalid = copy.deepcopy(envelope)
    invalid["body"]["actor"]["agent_id"] = "cafe\u0301"
    with pytest.raises(capsule.CapsuleValidationError, match="Unicode NFC"):
        capsule.validate_envelope_integrity(invalid)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda body: body["payload"]["data"].update(score=1.5), "decimal string"),
        (lambda body: body["payload"]["data"].update(optional=None), "not use null"),
        (
            lambda body: body["payload"]["data"].update(unsafe=2**53),
            "safe range",
        ),
        (lambda body: body.update(unknown_field=True), "unsupported fields"),
        (lambda body: body["parents"].reverse(), "lexicographically sorted"),
        (lambda body: body["metrics"].reverse(), "unique and sorted"),
    ],
)
def test_semantic_profile_rejects_noncanonical_values(mutate, message):
    body = copy.deepcopy(golden_envelope()["body"])
    mutate(body)
    with pytest.raises(capsule.CapsuleValidationError, match=message):
        capsule.validate_body(body)


def test_digest_mismatch_is_rejected():
    envelope = golden_envelope()
    envelope["digest"]["value"] = "f" * 64
    with pytest.raises(capsule.CapsuleValidationError, match="does not match"):
        capsule.validate_envelope_integrity(envelope)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":1}',
        b'{ "a":1}',
        b'{"a":1.0}',
        b'{"a":-0}',
        b'\xef\xbb\xbf{"a":1}',
        b'{"a":"\xff"}',
    ],
)
def test_raw_wire_parser_rejects_alternate_or_invalid_bytes(raw):
    with pytest.raises(capsule.CapsuleValidationError):
        capsule.parse_canonical(raw)


def test_raw_wire_parser_accepts_exact_golden_body():
    parsed = capsule.parse_canonical(golden_bytes())
    assert parsed == golden_envelope()["body"]


def test_raw_wire_parser_rejects_oversized_input_before_json_decode():
    raw = b" " * (capsule.MAX_ENVELOPE_BYTES + 1)
    with pytest.raises(capsule.CapsuleValidationError, match="1 MiB"):
        capsule.parse_canonical(raw)


def test_year_zero_is_rejected_consistently():
    body = copy.deepcopy(golden_envelope()["body"])
    body["created_at"] = "0000-01-01T00:00:00.000Z"
    with pytest.raises(capsule.CapsuleValidationError, match="millisecond precision"):
        capsule.validate_body(body)


def test_python_rejects_primitive_subclasses_before_serialization():
    class InjectingInt(int):
        def __str__(self):
            return '1,"injected":true'

    class InjectingString(str):
        def __iter__(self):
            return iter("injected")

    class ReorderingKey(str):
        def __lt__(self, other):
            return not super().__lt__(other)

    for value in (
        {"value": InjectingInt(1)},
        {"value": InjectingString("safe")},
        {ReorderingKey("a"): 1, ReorderingKey("b"): 2},
    ):
        with pytest.raises(capsule.CapsuleValidationError, match="unsupported|key"):
            capsule.canonicalize(value)


def test_public_consumer_sqlite_is_outside_the_capsule_module():
    source = inspect.getsource(capsule)
    assert "aiosqlite" not in source
    assert "GrokSessionStore" not in source
    assert "grok_sessions.db" not in source


def test_published_schema_is_strict_and_versioned():
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "docs"
            / "okf"
            / "intelligence-capsule-v1.schema.json"
        ).read_text()
    )
    assert (
        schema["$id"]
        == "https://grokmcp.org/docs/okf/intelligence-capsule-v1.schema.json"
    )
    assert schema["additionalProperties"] is False
    assert (
        schema["$defs"]["body"]["properties"]["protocol"]["const"] == capsule.PROTOCOL
    )
    assert schema["$defs"]["body"]["properties"]["version"]["const"] == capsule.VERSION
    assert (
        set(schema["$defs"]["body"]["properties"]["kind"]["enum"])
        == capsule.CAPSULE_KINDS
    )
    assert (
        set(schema["$defs"]["actor"]["properties"]["role"]["enum"])
        == capsule.ACTOR_ROLES
    )
    assert (
        set(schema["$defs"]["execution"]["properties"]["runtime"]["enum"])
        == capsule.EXECUTION_RUNTIMES
    )
    assert (
        set(schema["$defs"]["execution"]["properties"]["plane"]["enum"])
        == capsule.EXECUTION_PLANES
    )
    assert (
        set(schema["$defs"]["execution"]["properties"]["target"]["enum"])
        == capsule.EXECUTION_TARGETS
    )
    assert (
        set(schema["$defs"]["signature"]["properties"]["profile"]["enum"])
        == capsule.SIGNATURE_PROFILES
    )
    assert (
        schema["$defs"]["body"]["properties"]["parents"]["maxItems"]
        == capsule.MAX_PARENTS
    )
    assert schema["$defs"]["body"]["properties"]["created_at"]["pattern"].startswith(
        "^(?!0000)"
    )
