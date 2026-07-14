"""Tests for evals.needle_gates — deterministic gate validators + receipts."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from evals.needle_gates.harvest_request import build_next_harvest_request
from evals.needle_gates.receipts import (
    RECEIPT_SCHEMA_VERSION,
    ReceiptError,
    canonical_json_bytes,
    read_receipt,
    seal_receipt,
    sha256_file,
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


def _row(query: str, answer_class: str) -> str:
    return json.dumps(
        {
            "query": query,
            "tools": json.dumps([{"name": "t1"}]),
            "answers": json.dumps([{"route": answer_class}]),
        }
    )


def build_packet(root: Path, *, tamper: bool = False) -> Path:
    """Materialize a minimal-but-complete needle packet for validator tests."""
    packet = root / "packet"
    data = packet / "data"
    logs = packet / "logs"
    data.mkdir(parents=True)
    logs.mkdir(parents=True)

    files = {
        "route_selection.jsonl": "\n".join(
            [_row("train q1", "coding"), _row("train q2", "research")]
        )
        + "\n",
        "route_selection_sealed.jsonl": "\n".join(
            [_row("sealed q1", "planning"), _row("sealed q2", "vision")]
        )
        + "\n",
        "combined.jsonl": "\n".join(
            [_row("train q1", "coding"), _row("sealed q1", "planning")]
        )
        + "\n",
    }
    for name, text in files.items():
        (data / name).write_text(text)

    arm_candidates = [
        {
            "arm": "arm_B",
            "results_name": "B_hardneg",
            "dataset_hash": "b" * 24,
            "log": "ft-arm-B-seed42.log",
            "n": 128,
            "recipe": "hard_negatives",
            "seed": 42,
        },
        {
            "arm": "arm_E",
            "results_name": "E_worstcell",
            "dataset_hash": "e" * 24,
            "n": 128,
            "recipe": "worst_cell",
            "seed": 42,
        },
    ]
    (data / "arm_candidates.json").write_text(json.dumps(arm_candidates, indent=1))

    arm_results = [
        {
            "arm": "A_control",
            "sealed": 0.40,
            "dev_ood": 0.55,
            "in_template": 0.90,
            "forgetting": "3/3",
            "sealed_recalls": {
                "coding": 0.5,
                "planning": 0.25,
                "research": 0.5,
                "vision": 0.25,
            },
            "worst_class": ["planning", 0.25],
        },
        {
            "arm": "tfidf_baseline",
            "sealed": 0.50,
            "dev_ood": 0.50,
            "in_template": 0.60,
            "forgetting": "3/3",
            "sealed_recalls": {
                "coding": 0.5,
                "planning": 0.5,
                "research": 0.5,
                "vision": 0.5,
            },
            "worst_class": ["coding", 0.5],
        },
        {
            "arm": "B_hardneg",
            "dataset_hash": "b" * 24,
            "sealed": 0.45,
            "dev_ood": 0.60,
            "in_template": 0.92,
            "forgetting": "3/3",
            "sealed_recalls": {
                "coding": 0.75,
                "planning": 0.25,
                "research": 0.5,
                "vision": 0.25,
            },
            "worst_class": ["planning", 0.25],
        },
        {
            "arm": "E_worstcell",
            "dataset_hash": "e" * 24,
            "sealed": 0.525,
            "dev_ood": 0.60,
            "in_template": 0.93,
            "forgetting": "3/3",
            "sealed_recalls": {
                "coding": 0.75,
                "planning": 0.5,
                "research": 0.5,
                "vision": 0.35,
            },
            "worst_class": ["vision", 0.35],
        },
    ]
    (data / "arm_results.json").write_text(json.dumps(arm_results, indent=1))

    harvest_roots = {
        "planning": ["root-planning-0001", "root-planning-0002"],
        "vision": ["root-vision-0001"],
        "coding": ["root-coding-0001"],
        "research": ["root-research-0001"],
        "retention_probe": ["root-coding-0001", "root-research-0001"],
    }
    (data / "harvest_roots.json").write_text(json.dumps(harvest_roots, indent=1))

    manifest = []
    for name in sorted(files) + [
        "arm_candidates.json",
        "arm_results.json",
        "harvest_roots.json",
    ]:
        generator = "gen_datasets.py seed 42"
        if name == "combined.jsonl":
            generator = "concat; KNOWN-CONTAMINATED research control, quarantined"
        manifest.append(
            {
                "path": f"evals/needle_lab/data/{name}",
                "sha256": sha256_file(data / name),
                "n": 2,
                "generator": generator,
            }
        )
    (data / "manifest.json").write_text(json.dumps(manifest, indent=1))

    (packet / "README.md").write_text(
        "# Needle packet\n"
        "Split policy: _per_tool_split seed 42 (pinned before corpus assembly).\n"
        "Base checkpoint sha256 prefix: 40a32e91d1d4197b\n"
        "Env: python 3.11, jax 0.4, flax 0.8\n"
    )

    log_text = (
        "run start\n"
        "step 10 loss 2.31\n"
        "step 640 loss 0.42\n"
        "total steps: 640\n"
        "wall_seconds: 512.5\n"
        "training complete, best checkpoint saved\n"
    )
    (logs / "ft-arm-B-seed42.log").write_text(log_text)
    (logs / "ft-E_worstcell-seed42.log").write_text(log_text.replace("640", "512"))

    if tamper:
        (data / "route_selection.jsonl").write_text(_row("tampered", "coding") + "\n")
    return packet


# ---------------------------------------------------------------- receipts


def test_receipt_roundtrip_and_determinism():
    payload = {"schema": "x/v1", "ok": True, "violations": [], "n": 3}
    first = seal_receipt("preflight", payload)
    second = seal_receipt("preflight", payload)
    assert first == second
    assert first["schema"] == RECEIPT_SCHEMA_VERSION
    decoded = verify_receipt(first, "preflight")
    assert decoded == payload
    raw = base64.b64decode(first["payload_b64"])
    assert hashlib.sha256(raw).hexdigest() == first["payload_sha256"]


def test_receipt_tamper_detection():
    receipt = seal_receipt("corpus_veto", {"ok": False, "violations": ["x"]})
    forged_payload = canonical_json_bytes({"ok": True, "violations": []})
    tampered = dict(receipt)
    tampered["payload_b64"] = base64.b64encode(forged_payload).decode()
    with pytest.raises(ReceiptError, match="digest mismatch"):
        verify_receipt(tampered)

    bad_digest = dict(receipt)
    bad_digest["payload_sha256"] = "0" * 64
    with pytest.raises(ReceiptError, match="digest mismatch"):
        verify_receipt(bad_digest)

    with pytest.raises(ReceiptError, match="expected"):
        verify_receipt(receipt, "preflight")

    with pytest.raises(ReceiptError, match="unknown validator"):
        seal_receipt("made_up", {"ok": True})

    with pytest.raises(ReceiptError, match="schema"):
        verify_receipt({"schema": "other/v9"})


def test_receipt_file_roundtrip(tmp_path):
    receipt = seal_receipt("lane_vitals", {"ok": True, "total_steps": 5})
    path = tmp_path / "receipts" / "vitals.json"
    write_receipt(receipt, path)
    loaded = read_receipt(path, "lane_vitals")
    assert loaded == receipt

    tampered = json.loads(path.read_text())
    tampered["payload_b64"] = base64.b64encode(
        canonical_json_bytes({"ok": False, "total_steps": 0})
    ).decode()
    path.write_text(json.dumps(tampered))
    with pytest.raises(ReceiptError):
        read_receipt(path)

    with pytest.raises(ReceiptError, match="missing"):
        read_receipt(tmp_path / "nope.json")


# --------------------------------------------------------------- preflight


def test_preflight_clean_packet(tmp_path):
    packet = build_packet(tmp_path)
    payload = validate_preflight(packet)
    assert payload["ok"] is True
    assert payload["violations"] == []
    assert any("manifest.json" in k for k in payload["artifact_digests"])


def test_preflight_detects_digest_mismatch(tmp_path):
    packet = build_packet(tmp_path, tamper=True)
    payload = validate_preflight(packet)
    assert payload["ok"] is False
    assert any("sha256 mismatch" in v for v in payload["violations"])


def test_preflight_detects_unlisted_file_and_missing_pins(tmp_path):
    packet = build_packet(tmp_path)
    (packet / "data" / "rogue.jsonl").write_text(_row("rogue", "coding") + "\n")
    (packet / "README.md").write_text("# no pins here\n")
    payload = validate_preflight(packet)
    assert payload["ok"] is False
    joined = "\n".join(payload["violations"])
    assert "unlisted data file: data/rogue.jsonl" in joined
    assert "split policy not pinned" in joined
    assert "checkpoint pin" in joined
    assert "environment pins unrecorded" in joined


def test_preflight_missing_manifest_fails_closed(tmp_path):
    payload = validate_preflight(tmp_path / "empty")
    assert payload["ok"] is False
    assert any("manifest missing" in v for v in payload["violations"])


# ------------------------------------------------------------- corpus veto


def test_corpus_known_contamination_is_marked_from_manifest(tmp_path):
    packet = build_packet(tmp_path)
    payload = validate_corpus(packet)
    # combined.jsonl leaks a sealed row, but the packet manifest declares it
    # quarantined, so the violation carries the known marker and does not
    # trip the new-violation gate.
    leaks = [v for v in payload["violations"] if "leaked" in v]
    assert leaks and all("(known" in v for v in leaks)
    assert payload["new_violations"] == []
    assert payload["ok"] is True


def test_corpus_new_leakage_fails(tmp_path):
    packet = build_packet(tmp_path)
    train = packet / "data" / "route_selection.jsonl"
    train.write_text(train.read_text() + _row("sealed q2", "vision") + "\n")
    payload = validate_corpus(packet)
    assert payload["ok"] is False
    assert any(
        "leaked into data/route_selection.jsonl" in v and "(known" not in v
        for v in payload["new_violations"]
    )


def test_corpus_duplicates_and_secrets_fail(tmp_path):
    packet = build_packet(tmp_path)
    train = packet / "data" / "route_selection.jsonl"
    train.write_text(train.read_text() + _row("train q1", "coding") + "\n")
    sealed = packet / "data" / "route_selection_sealed.jsonl"
    sealed.write_text(
        sealed.read_text()
        + json.dumps({"query": "leak AKIA" + "A" * 16, "answers": "[]"})
        + "\n"
    )
    payload = validate_corpus(packet)
    assert payload["ok"] is False
    joined = "\n".join(payload["new_violations"])
    assert "exact duplicates in data/route_selection.jsonl" in joined
    assert "secret pattern aws-access-key" in joined


def test_corpus_reports_class_balance_facts(tmp_path):
    packet = build_packet(tmp_path)
    payload = validate_corpus(packet)
    balance = payload["facts"]["class_balance"]["route_selection.jsonl"]
    assert balance == {"coding": 1, "research": 1}


# ------------------------------------------------------------- lane vitals


def test_lane_vitals_ok(tmp_path):
    packet = build_packet(tmp_path)
    log = packet / "logs" / "ft-arm-B-seed42.log"
    payload = validate_lane_vitals(log, arm="B_hardneg", seed=42)
    assert payload["ok"] is True
    assert payload["vitals_veto"] == "ok"
    assert payload["total_steps"] == 640
    assert payload["completed"] is True
    assert payload["nan_found"] is False
    assert payload["wall_seconds"] == 512.5
    assert payload["log_sha256"] == sha256_file(log)


def test_lane_vitals_veto_on_nan_and_incomplete(tmp_path):
    log = tmp_path / "bad.log"
    log.write_text("step 5 loss nan\n")
    payload = validate_lane_vitals(log)
    assert payload["ok"] is False
    joined = payload["vitals_veto"]
    assert "NaN" in joined
    assert "no completion marker" in joined


def test_lane_vitals_missing_log_fails_closed(tmp_path):
    payload = validate_lane_vitals(tmp_path / "absent.log")
    assert payload["ok"] is False
    assert "training log missing" in payload["vitals_veto"]
    assert payload["total_steps"] == 0


def test_lane_vitals_zero_steps(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("training complete\n")
    payload = validate_lane_vitals(log)
    assert payload["ok"] is False
    assert "total steps not > 0" in payload["vitals_veto"]


# ------------------------------------------------------------- arm records


def test_arm_records_replays_frozen_file(tmp_path):
    packet = build_packet(tmp_path)
    payload = validate_arm_records(packet)
    assert payload["ok"] is True
    assert [a["arm"] for a in payload["arms"]] == ["arm_B", "arm_E"]
    assert all(a["seed"] == 42 for a in payload["arms"])
    assert payload["arms"][0]["dataset_hash"] == "b" * 24


def test_arm_records_missing_file_fails_closed(tmp_path):
    payload = validate_arm_records(tmp_path / "empty")
    assert payload["ok"] is False
    assert payload["arms"] == []


# ------------------------------------------------------------- arm metrics


def test_arm_metrics_legacy_key_mapping_and_vitals_join(tmp_path):
    packet = build_packet(tmp_path)
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is True

    control = payload["control"]
    assert control["results_name"] == "A_control"
    assert control["dev_ood"] == 0.40  # legacy "sealed"
    assert control["secondary_ood"] == 0.55  # legacy "dev_ood"
    assert control["retention"] == "3/3"  # legacy "forgetting"

    baseline = payload["baseline"]
    assert baseline["results_name"] == "tfidf_baseline"
    assert baseline["dev_ood"] == 0.50

    by_name = {a["results_name"]: a for a in payload["arms"]}
    assert set(by_name) == {"B_hardneg", "E_worstcell"}
    b_arm = by_name["B_hardneg"]
    assert b_arm["arm"] == "arm_B"  # bound to the frozen record's exact identity
    assert b_arm["dataset_hash"] == "b" * 24
    assert b_arm["dev_ood"] == 0.45
    assert b_arm["vitals"]["ok"] is True
    assert b_arm["vitals"]["total_steps"] == 640
    assert "ft-arm-B-seed42.log" in b_arm["vitals"]["log"]  # explicit log field
    e_arm = by_name["E_worstcell"]
    assert e_arm["vitals"]["total_steps"] == 512
    assert "ft-E_worstcell-seed42.log" in e_arm["vitals"]["log"]  # exact-name match


def test_arm_metrics_missing_log_vetoes_lane(tmp_path):
    packet = build_packet(tmp_path)
    (packet / "logs" / "ft-E_worstcell-seed42.log").unlink()
    payload = validate_arm_metrics(packet)
    by_name = {a["results_name"]: a for a in payload["arms"]}
    assert by_name["E_worstcell"]["vitals"]["ok"] is False
    assert "no training log" in by_name["E_worstcell"]["vitals"]["vitals_veto"]


def test_arm_metrics_missing_control_fails(tmp_path):
    packet = build_packet(tmp_path)
    results_path = packet / "data" / "arm_results.json"
    rows = json.loads(results_path.read_text())
    rows = [r for r in rows if r["arm"] != "A_control"]
    results_path.write_text(json.dumps(rows))
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is False
    assert any("control arm" in v for v in payload["violations"])


def test_arm_metrics_missing_trivial_baseline_fails(tmp_path):
    packet = build_packet(tmp_path)
    results_path = packet / "data" / "arm_results.json"
    rows = json.loads(results_path.read_text())
    rows = [r for r in rows if r["arm"] != "tfidf_baseline"]
    results_path.write_text(json.dumps(rows))
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is False
    assert payload["baseline"] is None
    assert any("trivial baseline tfidf_baseline missing" in v for v in payload["violations"])


def test_arm_metrics_rejects_dataset_hash_mismatch(tmp_path):
    packet = build_packet(tmp_path)
    results_path = packet / "data" / "arm_results.json"
    rows = json.loads(results_path.read_text())
    for row in rows:
        if row["arm"] == "B_hardneg":
            row["dataset_hash"] = "f" * 24
    results_path.write_text(json.dumps(rows))
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is False
    assert any("dataset_hash mismatch" in v for v in payload["violations"])
    assert all(a["results_name"] != "B_hardneg" for a in payload["arms"])


def test_arm_metrics_rejects_unbound_results_row(tmp_path):
    packet = build_packet(tmp_path)
    results_path = packet / "data" / "arm_results.json"
    rows = json.loads(results_path.read_text())
    rows.append({"arm": "Z_orphan", "sealed": 0.9, "forgetting": "3/3"})
    results_path.write_text(json.dumps(rows))
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is False
    assert any("not bound to any frozen arm record" in v for v in payload["violations"])


def test_arm_metrics_never_joins_by_partial_name(tmp_path):
    """A results row that merely shares a letter with a record must not bind."""
    packet = build_packet(tmp_path)
    results_path = packet / "data" / "arm_results.json"
    rows = json.loads(results_path.read_text())
    for row in rows:
        if row["arm"] == "B_hardneg":
            row["arm"] = "B_other_variant"  # same letter, different exact identity
    results_path.write_text(json.dumps(rows))
    payload = validate_arm_metrics(packet)
    assert payload["ok"] is False
    joined = "\n".join(payload["violations"])
    assert "no results row with exact identity 'B_hardneg'" in joined
    assert "'B_other_variant' is not bound" in joined


def test_arm_records_rejects_duplicates_aliases_and_missing_hash(tmp_path):
    packet = build_packet(tmp_path)
    candidates_path = packet / "data" / "arm_candidates.json"
    rows = json.loads(candidates_path.read_text())
    rows.append(dict(rows[0]))  # exact duplicate name
    rows.append({**rows[1], "arm": "ARM-E", "results_name": "E_alias"})  # alias of arm_E
    rows.append({"arm": "arm_H", "results_name": "H_x", "n": 1, "recipe": "r", "seed": 1})
    candidates_path.write_text(json.dumps(rows))
    payload = validate_arm_records(packet)
    assert payload["ok"] is False
    joined = "\n".join(payload["violations"])
    assert "duplicate arm name" in joined
    assert "aliased arm names" in joined
    assert "arm arm_H: dataset_hash missing" in joined


def test_arm_records_rejects_duplicate_results_identity(tmp_path):
    packet = build_packet(tmp_path)
    candidates_path = packet / "data" / "arm_candidates.json"
    rows = json.loads(candidates_path.read_text())
    rows.append(
        {"arm": "arm_F", "results_name": "B_hardneg", "dataset_hash": "f" * 24}
    )
    candidates_path.write_text(json.dumps(rows))
    payload = validate_arm_records(packet)
    assert payload["ok"] is False
    assert any("already claimed" in v for v in payload["violations"])


# --------------------------------------------------------- harvest request


def test_harvest_request_typed_and_request_only(tmp_path):
    packet = build_packet(tmp_path)
    request = build_next_harvest_request(
        packet,
        campaign_id="needle-r2",
        source_dataset_id="D0001",
        target_dataset_id="D0002",
    )
    assert request["schema"] == "needle-next-harvest-request/v1"
    assert request["request_only"] is True
    assert request["authorizes_generation"] is False
    assert request["authorizes_training"] is False

    weak = {(c["arm"], c["cell"]) for c in request["weak_confusion_cells"]}
    assert ("B_hardneg", "planning") in weak
    assert ("B_hardneg", "vision") in weak
    assert ("E_worstcell", "vision") in weak
    assert ("B_hardneg", "coding") not in weak

    # Every cell names the exact roots that produced it, so a harvester can
    # generate variants of the FAILING root rather than guessing.
    weak_by_cell = {c["cell"]: c for c in request["weak_confusion_cells"]}
    assert weak_by_cell["planning"]["root_ids"] == [
        "root-planning-0001",
        "root-planning-0002",
    ]
    assert weak_by_cell["vision"]["root_ids"] == ["root-vision-0001"]

    retention = {(c["arm"], c["cell"]) for c in request["retention_cells"]}
    assert ("B_hardneg", "coding") in retention
    assert ("B_hardneg", "retention_probe") in retention
    retention_by_cell = {c["cell"]: c for c in request["retention_cells"]}
    assert retention_by_cell["coding"]["root_ids"] == ["root-coding-0001"]
    assert retention_by_cell["retention_probe"]["root_ids"] == [
        "root-coding-0001",
        "root-research-0001",
    ]
    assert request["evidence_digests"]  # exact digests present
    assert any("harvest_roots.json" in k for k in request["evidence_digests"])


def test_harvest_request_rejects_same_dataset(tmp_path):
    packet = build_packet(tmp_path)
    with pytest.raises(ValueError, match="never modifies"):
        build_next_harvest_request(
            packet,
            campaign_id="needle-r2",
            source_dataset_id="D0001",
            target_dataset_id="D0001",
        )


def test_harvest_request_deterministic(tmp_path):
    packet = build_packet(tmp_path)
    kwargs = dict(
        campaign_id="needle-r2",
        source_dataset_id="D0001",
        target_dataset_id="D0002",
    )
    first = seal_receipt("harvest_request", build_next_harvest_request(packet, **kwargs))
    second = seal_receipt("harvest_request", build_next_harvest_request(packet, **kwargs))
    assert first == second


# ------------------------------------------------------------- identifiers


def test_identifier_validation_allows_safe_and_rejects_shell_syntax():
    from evals.needle_gates.identifiers import (
        IdentifierError,
        validate_identifier,
        validate_packet_path,
    )

    assert validate_identifier("campaign", "needle-r2.v1_x") == "needle-r2.v1_x"
    for bad in (
        "a b",
        "x;rm -rf /",
        "$(whoami)",
        "`id`",
        "a|b",
        "a>b",
        "-flag",
        "",
        "x" * 70,
        "über",
    ):
        with pytest.raises(IdentifierError):
            validate_identifier("campaign", bad)

    assert validate_packet_path("evals/needle_gates/fixtures/mock_packet")
    assert validate_packet_path("/tmp/needle-gates-run1/preflight.json")
    for bad in ("a b/c", "pkt;rm", "$(pwd)/pkt", "-pkt", "../secrets", "a/../../b"):
        with pytest.raises(IdentifierError):
            validate_packet_path(bad)


# ------------------------------------------------------- committed fixture


def test_committed_mock_packet_fixture_passes_all_gates():
    """The default mock packet advertised by the workflow exists on this
    branch and passes every validator, so the workflow never advertises a
    packet absent from main."""
    fixture = Path("evals/needle_gates/fixtures/mock_packet")
    assert fixture.is_dir(), "committed mock packet fixture missing"

    for func in (validate_preflight, validate_corpus, validate_arm_records):
        payload = func(fixture)
        assert payload["ok"] is True, payload["violations"]

    metrics = validate_arm_metrics(fixture)
    assert metrics["ok"] is True, metrics["violations"]
    assert metrics["baseline"]["results_name"] == "tfidf_baseline"
    assert metrics["control"]["results_name"] == "A_control"
    # Fixture numbers make the trivial-baseline gate do real work:
    # B_hardneg beats the control but NOT the baseline.
    by_name = {a["results_name"]: a for a in metrics["arms"]}
    assert by_name["B_hardneg"]["dev_ood"] > metrics["control"]["dev_ood"]
    assert by_name["B_hardneg"]["dev_ood"] < metrics["baseline"]["dev_ood"]
    assert by_name["E_worstcell"]["dev_ood"] > metrics["baseline"]["dev_ood"]

    request = build_next_harvest_request(
        fixture,
        campaign_id="fixture-check",
        source_dataset_id="D0001",
        target_dataset_id="D0002",
    )
    assert request["request_only"] is True
    assert all(c["root_ids"] for c in request["weak_confusion_cells"])


# ----------------------------------------------------------- determinism


def test_validators_are_deterministic(tmp_path):
    packet = build_packet(tmp_path)
    for func in (validate_preflight, validate_corpus, validate_arm_metrics):
        first = seal_receipt("preflight", func(packet))
        second = seal_receipt("preflight", func(packet))
        assert first == second


# ------------------------------------------------------------------- CLI


def test_cli_emits_and_verifies_receipts(tmp_path, capsys):
    from evals.needle_gates.__main__ import main

    packet = build_packet(tmp_path)
    out = tmp_path / "receipts" / "preflight.json"
    assert main(["preflight", "--packet", str(packet), "--out", str(out)]) == 0
    receipt = json.loads(out.read_text())
    payload = verify_receipt(receipt, "preflight")
    assert payload["ok"] is True

    assert (
        main(
            [
                "verify",
                "--receipt",
                str(out),
                "--expect-validator",
                "preflight",
                "--expect-digest",
                receipt["payload_sha256"],
            ]
        )
        == 0
    )

    # Pinned-digest mismatch fails closed (non-zero exit).
    assert (
        main(["verify", "--receipt", str(out), "--expect-digest", "0" * 64]) == 1
    )

    # Tampered payload fails closed.
    forged = dict(receipt)
    forged["payload_b64"] = base64.b64encode(
        canonical_json_bytes({"ok": True, "violations": []})
    ).decode()
    out.write_text(json.dumps(forged))
    assert main(["verify", "--receipt", str(out)]) == 1
    capsys.readouterr()


def test_cli_harvest_request(tmp_path, capsys):
    from evals.needle_gates.__main__ import main

    packet = build_packet(tmp_path)
    assert (
        main(
            [
                "harvest-request",
                "--packet",
                str(packet),
                "--campaign",
                "needle-r2",
                "--source-dataset",
                "D0001",
                "--target-dataset",
                "D0002",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    payload = verify_receipt(receipt, "harvest_request")
    assert payload["request_only"] is True


def test_cli_rejects_unsafe_embedded_values(tmp_path, capsys):
    """Shell syntax, whitespace, and traversal in caller-supplied values are
    rejected at the argument boundary before any validator runs."""
    from evals.needle_gates.__main__ import main

    packet = build_packet(tmp_path)
    bad_invocations = [
        ["preflight", "--packet", "pkt;rm -rf /"],
        ["preflight", "--packet", "pkt $(whoami)"],
        ["preflight", "--packet", "../../../etc"],
        ["arm-metrics", "--packet", str(packet), "--out", "/tmp/x;touch pwned"],
        [
            "harvest-request",
            "--packet",
            str(packet),
            "--campaign",
            "c1; curl evil",
            "--source-dataset",
            "D0001",
            "--target-dataset",
            "D0002",
        ],
        [
            "harvest-request",
            "--packet",
            str(packet),
            "--campaign",
            "c1",
            "--source-dataset",
            "$(id)",
            "--target-dataset",
            "D0002",
        ],
        [
            "harvest-request",
            "--packet",
            str(packet),
            "--campaign",
            "c1",
            "--source-dataset",
            "D0001",
            "--target-dataset",
            "D0002|tee /tmp/x",
        ],
    ]
    for argv in bad_invocations:
        with pytest.raises(SystemExit) as excinfo:
            main(argv)
        assert excinfo.value.code == 2, argv
        capsys.readouterr()
