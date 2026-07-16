"""Focused tests for prep-only superiority pareto harness (H1–H6 / G1–G10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.superiority_baseline_pareto import (
    FROZEN_POINTS,
    LABELS,
    OracleError,
    ProvenanceError,
    brute_force_front0,
    build_receipt,
    fixture_sha256,
    full_front_partition,
    import_pareto,
    measure_latency_ms,
    measure_peak_mem_bytes,
    run_capture,
)


ROOT = Path(__file__).resolve().parents[1]
PARETO = ROOT / "src" / "swarm" / "pareto.py"


class TestH1Provenance:
    def test_normal_import_resolves_worktree_file(self):
        mod = import_pareto(PARETO)
        assert Path(mod.__file__).resolve() == PARETO.resolve()
        assert callable(mod.fast_non_dominated_sort)

    def test_provenance_refusal_on_wrong_target(self, tmp_path: Path):
        decoy = tmp_path / "pareto.py"
        decoy.write_text("# decoy\n", encoding="utf-8")
        with pytest.raises(ProvenanceError):
            import_pareto(decoy)


class TestH2Oracle:
    def test_full_front_matches_brute_force_and_covers_indices(self):
        mod = import_pareto(PARETO)
        result = full_front_partition(
            FROZEN_POINTS, mod.fast_non_dominated_sort, mod.dominates
        )
        assert sorted(result["front0"]) == brute_force_front0(
            FROZEN_POINTS, mod.dominates
        )
        flat = [i for front in result["fronts"] for i in front]
        assert sorted(flat) == list(range(len(FROZEN_POINTS)))
        assert len(result["front_partition_sha256"]) == 64

    def test_oracle_corruption_detected(self):
        mod = import_pareto(PARETO)

        def bad_sort(points):
            fronts = mod.fast_non_dominated_sort(points)
            # Drop an index → partition integrity fails.
            if fronts and fronts[0]:
                fronts = [fronts[0][1:], *fronts[1:]]
            return fronts

        with pytest.raises(OracleError):
            full_front_partition(FROZEN_POINTS, bad_sort, mod.dominates)


class TestH3LatencyMemorySplit:
    def test_latency_samples_without_tracemalloc_and_noise_floor(self):
        mod = import_pareto(PARETO)
        latency = measure_latency_ms(
            mod.fast_non_dominated_sort,
            FROZEN_POINTS,
            outer_samples=9,
            inner_loops=50,
            warmups=1,
        )
        assert latency["tracemalloc"] is False
        assert len(latency["samples_ms"]) == 9
        assert latency["median_ms"] > 0
        assert latency["noise_floor_pct"] >= 5.0

    def test_peak_mem_measured_separately(self):
        mod = import_pareto(PARETO)
        memory = measure_peak_mem_bytes(
            mod.fast_non_dominated_sort,
            FROZEN_POINTS,
            repeats=3,
            inner_loops=50,
            warmups=1,
        )
        assert memory["method"] == "tracemalloc_separate_from_latency"
        assert memory["peak_mem_bytes"] >= 0
        assert len(memory["samples_peak_mem_bytes"]) == 3


class TestH4H5H6Receipt:
    def test_labels_restricted(self):
        assert LABELS == ("ORIGINAL", "TEAM_FINAL", "SWARM_FINAL")

    def test_receipt_deterministic_fixture_and_bundle_loc(self):
        receipt = run_capture(
            label="ORIGINAL",
            target_path=PARETO,
            outer_samples=9,
            inner_loops=40,
            warmups=1,
            mem_repeats=2,
            bundle_files=["src/swarm/pareto.py"],
        )
        assert receipt["schema"] == "unigrok-superiority-receipt-v1"
        assert receipt["label"] == "ORIGINAL"
        assert receipt["fixture_sha256"] == fixture_sha256(FROZEN_POINTS)
        assert receipt["source_sha256"]
        assert receipt["git_commit"]
        assert receipt["python"]["executable"]
        assert receipt["frozen_inputs"]["points"]
        assert receipt["latency"]["outer_samples"] == 9
        assert receipt["oracle"]["front_partition_sha256"]
        assert receipt["bundle"]["total_loc"] == receipt["bundle"]["loc_by_file"][
            "src/swarm/pareto.py"
        ]
        # Round-trip JSON stability for key identity fields.
        blob = json.dumps(receipt, sort_keys=True)
        again = json.loads(blob)
        assert again["fixture_sha256"] == receipt["fixture_sha256"]
        assert again["source_sha256"] == receipt["source_sha256"]

    def test_bundle_schema_names_multiple_files(self):
        mod = import_pareto(PARETO)
        oracle = full_front_partition(
            FROZEN_POINTS, mod.fast_non_dominated_sort, mod.dominates
        )
        latency = {
            "samples_ms": [1.0] * 9,
            "median_ms": 1.0,
            "p50_ms": 1.0,
            "p95_ms": 1.0,
            "noise_floor_pct": 5.0,
            "outer_samples": 9,
            "inner_loops": 1,
            "warmups": 0,
            "tracemalloc": False,
        }
        memory = {
            "samples_peak_mem_bytes": [10],
            "peak_mem_bytes": 10,
            "median_peak_mem_bytes": 10,
            "repeats": 1,
            "inner_loops": 1,
            "warmups": 0,
            "method": "tracemalloc_separate_from_latency",
        }
        receipt = build_receipt(
            label="TEAM_FINAL",
            pareto_mod=mod,
            target_path=PARETO,
            points=FROZEN_POINTS,
            latency=latency,
            memory=memory,
            oracle=oracle,
            bundle_files=["src/swarm/pareto.py", "src/swarm/__init__.py"],
        )
        assert set(receipt["bundle"]["files"]) == {
            "src/swarm/pareto.py",
            "src/swarm/__init__.py",
        }
        assert receipt["bundle"]["total_loc"] == sum(
            receipt["bundle"]["loc_by_file"].values()
        )
