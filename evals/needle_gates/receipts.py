"""Digest-sealed receipt envelope for Needle gate validators.

A receipt is the only unit of gate truth exchanged between the deterministic
validators (Python, this package) and any orchestrator (for example the
``.claude/workflows/needle-training-campaign.js`` workflow). The envelope is
deliberately transport-hostile to tampering:

- The typed payload is serialized to canonical JSON (sorted keys, compact
  separators, ``ensure_ascii=True``) and stored as base64 of those exact
  bytes in ``payload_b64``.
- ``payload_sha256`` is the SHA-256 of those exact bytes. A consumer never
  needs to re-canonicalize JSON (or agree on float formatting) to verify:
  it base64-decodes, hashes, and compares.
- A consumer that cannot run Python still verifies with nothing more than
  base64 + SHA-256, both available in any workflow runtime.

Any mismatch — wrong schema, wrong digest, undecodable payload — raises
``ReceiptError``. Consumers must treat a raised error as gate failure
(fail closed), never as an invitation to fall back to agent prose.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA_VERSION = "needle-gate-receipt/v1"

_ALLOWED_VALIDATORS = (
    "preflight",
    "corpus_veto",
    "lane_vitals",
    "arm_records",
    "arm_metrics",
    "harvest_request",
)


class ReceiptError(ValueError):
    """A receipt failed structural or cryptographic verification."""


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize ``payload`` to the canonical byte form that gets digested."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Stream a file's SHA-256 (files may be multi-megabyte JSONL shards)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal_receipt(validator: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a typed validator payload in the digest-sealed envelope."""
    if validator not in _ALLOWED_VALIDATORS:
        raise ReceiptError(f"unknown validator {validator!r}")
    payload_bytes = canonical_json_bytes(payload)
    return {
        "schema": RECEIPT_SCHEMA_VERSION,
        "validator": validator,
        "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        "payload_sha256": sha256_bytes(payload_bytes),
    }


def verify_receipt(
    receipt: dict[str, Any], expected_validator: str | None = None
) -> dict[str, Any]:
    """Verify an envelope and return the decoded payload.

    Raises ``ReceiptError`` on any structural or digest mismatch. Callers
    must fail closed on that error.
    """
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt is not an object")
    if receipt.get("schema") != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError(
            f"receipt schema {receipt.get('schema')!r} != {RECEIPT_SCHEMA_VERSION!r}"
        )
    validator = receipt.get("validator")
    if validator not in _ALLOWED_VALIDATORS:
        raise ReceiptError(f"unknown validator {validator!r}")
    if expected_validator is not None and validator != expected_validator:
        raise ReceiptError(
            f"receipt validator {validator!r} != expected {expected_validator!r}"
        )
    payload_b64 = receipt.get("payload_b64")
    payload_sha256 = receipt.get("payload_sha256")
    if not isinstance(payload_b64, str) or not isinstance(payload_sha256, str):
        raise ReceiptError("receipt missing payload_b64/payload_sha256 strings")
    try:
        payload_bytes = base64.b64decode(payload_b64.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReceiptError(f"payload_b64 is not valid base64: {exc}") from exc
    actual = sha256_bytes(payload_bytes)
    if actual != payload_sha256:
        raise ReceiptError(
            f"payload digest mismatch: computed {actual}, declared {payload_sha256}"
        )
    try:
        payload = json.loads(payload_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"payload is not canonical ASCII JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("payload is not an object")
    return payload


def write_receipt(receipt: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")


def read_receipt(path: Path, expected_validator: str | None = None) -> dict[str, Any]:
    """Load a receipt file and verify it before returning the payload."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ReceiptError(f"receipt file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"receipt file is not JSON: {path}: {exc}") from exc
    verify_receipt(raw, expected_validator)
    return raw
