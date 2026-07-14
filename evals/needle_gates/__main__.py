"""CLI entry point: ``python -m evals.needle_gates <command> ...``.

Agents run these commands and transcribe the emitted receipt envelope
back to the orchestrator. The envelope's ``payload_sha256`` binds the
typed payload byte-for-byte; the orchestrator (or any reviewer) verifies
it independently and derives gate truth only from the verified payload.

``verify`` exits non-zero on any mismatch so shell pipelines fail closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals.needle_gates.harvest_request import (
    DEFAULT_WEAK_RECALL_THRESHOLD,
    build_next_harvest_request,
)
from evals.needle_gates.receipts import (
    ReceiptError,
    read_receipt,
    seal_receipt,
    verify_receipt,
    write_receipt,
)
from evals.needle_gates.validators import (
    validate_arm_metrics,
    validate_arm_records,
    validate_corpus,
    validate_lane_vitals,
    validate_preflight,
)


def _emit(receipt: dict, out: str | None) -> None:
    if out:
        write_receipt(receipt, Path(out))
    print(json.dumps(receipt, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.needle_gates")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("preflight", "corpus-veto", "arm-records", "arm-metrics"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--packet", required=True)
        cmd.add_argument("--out")

    vitals = sub.add_parser("lane-vitals")
    vitals.add_argument("--log", required=True)
    vitals.add_argument("--arm", default="")
    vitals.add_argument("--seed", type=int, default=0)
    vitals.add_argument("--out")

    harvest = sub.add_parser("harvest-request")
    harvest.add_argument("--packet", required=True)
    harvest.add_argument("--campaign", required=True)
    harvest.add_argument("--source-dataset", required=True)
    harvest.add_argument("--target-dataset", required=True)
    harvest.add_argument(
        "--recall-threshold", type=float, default=DEFAULT_WEAK_RECALL_THRESHOLD
    )
    harvest.add_argument("--out")

    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--expect-validator")
    verify.add_argument("--expect-digest")

    args = parser.parse_args(argv)

    if args.command == "preflight":
        payload = validate_preflight(Path(args.packet))
        _emit(seal_receipt("preflight", payload), args.out)
    elif args.command == "corpus-veto":
        payload = validate_corpus(Path(args.packet))
        _emit(seal_receipt("corpus_veto", payload), args.out)
    elif args.command == "arm-records":
        payload = validate_arm_records(Path(args.packet))
        _emit(seal_receipt("arm_records", payload), args.out)
    elif args.command == "arm-metrics":
        payload = validate_arm_metrics(Path(args.packet))
        _emit(seal_receipt("arm_metrics", payload), args.out)
    elif args.command == "lane-vitals":
        payload = validate_lane_vitals(Path(args.log), arm=args.arm, seed=args.seed)
        _emit(seal_receipt("lane_vitals", payload), args.out)
    elif args.command == "harvest-request":
        payload = build_next_harvest_request(
            Path(args.packet),
            campaign_id=args.campaign,
            source_dataset_id=args.source_dataset,
            target_dataset_id=args.target_dataset,
            weak_recall_threshold=args.recall_threshold,
        )
        _emit(seal_receipt("harvest_request", payload), args.out)
    elif args.command == "verify":
        try:
            receipt = read_receipt(Path(args.receipt), args.expect_validator)
            if args.expect_digest and receipt["payload_sha256"] != args.expect_digest:
                raise ReceiptError(
                    f"receipt digest {receipt['payload_sha256']} != "
                    f"pinned {args.expect_digest}"
                )
            verify_receipt(receipt, args.expect_validator)
        except ReceiptError as exc:
            print(f"RECEIPT VERIFICATION FAILED: {exc}", file=sys.stderr)
            return 1
        print("receipt verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
