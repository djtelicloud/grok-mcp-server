"""Deterministic gate validators for the Needle training campaign.

Mechanical gate authority lives here, in executable repository code — not in
agent judgment. Each validator reads committed artifacts, computes typed JSON
results with artifact digests, and seals them into a receipt whose integrity
any consumer (including the ``.claude/workflows/needle-training-campaign.js``
orchestrator) can re-verify byte-for-byte.

Agents may *invoke* these validators and *summarize* their output; they may
never decide gate truth. A workflow runtime that cannot execute the validators
directly must require a precomputed receipt whose digest it recomputes and
checks, and must fail closed on any mismatch.
"""

from evals.needle_gates.receipts import (
    RECEIPT_SCHEMA_VERSION,
    ReceiptError,
    seal_receipt,
    sha256_file,
    verify_receipt,
)

__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "ReceiptError",
    "seal_receipt",
    "sha256_file",
    "verify_receipt",
]
