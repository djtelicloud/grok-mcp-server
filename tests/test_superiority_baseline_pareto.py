"""Focused tests for prep-only superiority pareto harness (H1–H10 / G1–G10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.superiority_baseline_pareto import (
    FROZEN_POINTS,
    LABELS,
    BundleError,
    CaptureParamError,
    DirtyTreeError,
    OracleError,
    ProvenanceError,
    brute_force_peel,
    canonicalize_bundle,
    compute_method_identity,
    fixture_sha256,
    full_front_partition,
    import_pareto,
    independent_dominates,
    measure_latency_ms,
    measure_traced_python_alloc_bytes,
    oracle_matrix_hashes,
    repo_root,
    run_capture,
    validate_capture_params,
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


class TestH7Oracle:
    def test_full_peel_matches_every_front(self):
        mod = import_pareto(PARETO)
        result = full_front_partition(FROZEN_POINTS, mod.fast_non_dominated_sort)
        expected = brute_force_peel(FROZEN_POINTS, independent_dominates)
        assert result["fronts"] == expected
        flat = [i for front in result["fronts"] for i in front]
        assert sorted(flat) == list(range(len(FROZEN_POINTS)))

    def test_wrong_later_front_rejected(self):
        mod = import_pareto(PARETO)

        def bad_sort(points):
            fronts = mod.fast_non_dominated_sort(points)
            if len(fronts) >= 2:
                # Collapse later fronts incorrectly.
                return [fronts[0], [i for f in fronts[1:] for i in f][:1]]
            return fronts

        with pytest.raises(OracleError):
            full_front_partition(FROZEN_POINTS, bad_sort)

    def test_production_dominates_corruption_ignored_by_independent_oracle(self):
        mod = import_pareto(PARETO)

        def lie_dominates(a, b):
            return False

        # Candidate uses real sort (correct fronts); independent oracle still peels.
        result = full_front_partition(
            FROZEN_POINTS, mod.fast_non_dominated_sort, lie_dominates
        )
        assert result["fronts"] == brute_force_peel(
            FROZEN_POINTS, independent_dominates
        )

    def test_oracle_matrix_hashes_stable(self):
        mod = import_pareto(PARETO)
        a = oracle_matrix_hashes(mod.fast_non_dominated_sort)
        b = oracle_matrix_hashes(mod.fast_non_dominated_sort)
        assert a["oracle_matrix_sha256"] == b["oracle_matrix_sha256"]
        assert "frozen_main" in a["cases"]
        assert "dominated_chain" in a["cases"]


class TestH8Params:
    def test_refuse_zero_and_negative_work(self):
        with pytest.raises(CaptureParamError):
            validate_capture_params(
                outer_samples=1, inner_loops=0, warmups=0, mem_repeats=0
            )
        with pytest.raises(CaptureParamError):
            run_capture(
                label="ORIGINAL",
                outer_samples=1,
                inner_loops=0,
                warmups=0,
                mem_repeats=0,
                allow_dirty=True,
                include_perf_matrix=False,
            )


class TestH9Bundle:
    def test_target_always_included_when_omitted(self):
        entries = canonicalize_bundle(
            root=repo_root(),
            target_path=PARETO,
            extra=["src/swarm/__init__.py"],
        )
        paths = [e["path"] for e in entries]
        assert paths[0] == "src/swarm/pareto.py"
        assert "src/swarm/__init__.py" in paths
        assert all("sha256" in e and e["loc"] > 0 for e in entries)

    def test_absolute_and_escaping_rejected(self):
        with pytest.raises(BundleError):
            canonicalize_bundle(
                root=repo_root(),
                target_path=PARETO,
                extra=["/tmp/evil.py"],
            )
        with pytest.raises(BundleError):
            canonicalize_bundle(
                root=repo_root(),
                target_path=PARETO,
                extra=["../outside.py"],
            )
        with pytest.raises(BundleError):
            canonicalize_bundle(
                root=repo_root(),
                target_path=PARETO,
                extra=["src/swarm/does_not_exist.py"],
            )


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

    def test_traced_python_alloc_measured_separately(self):
        mod = import_pareto(PARETO)
        memory = measure_traced_python_alloc_bytes(
            mod.fast_non_dominated_sort,
            FROZEN_POINTS,
            repeats=3,
            inner_loops=50,
            warmups=1,
        )
        assert memory["metric"] == "traced_python_allocation_bytes"
        assert len(memory["samples_traced_python_alloc_bytes"]) == 3


class TestH10MethodAndDirty:
    def test_method_hash_stable_across_labels(self):
        mod = import_pareto(PARETO)
        matrix = oracle_matrix_hashes(mod.fast_non_dominated_sort)
        a = compute_method_identity(
            outer_samples=9,
            inner_loops=40,
            warmups=1,
            mem_repeats=3,
            oracle_matrix=matrix,
        )
        b = compute_method_identity(
            outer_samples=9,
            inner_loops=40,
            warmups=1,
            mem_repeats=3,
            oracle_matrix=matrix,
        )
        assert a["method_sha256"] == b["method_sha256"]

    def test_method_hash_drifts_with_params(self):
        mod = import_pareto(PARETO)
        matrix = oracle_matrix_hashes(mod.fast_non_dominated_sort)
        a = compute_method_identity(
            outer_samples=9,
            inner_loops=40,
            warmups=1,
            mem_repeats=3,
            oracle_matrix=matrix,
        )
        b = compute_method_identity(
            outer_samples=10,
            inner_loops=40,
            warmups=1,
            mem_repeats=3,
            oracle_matrix=matrix,
        )
        assert a["method_sha256"] != b["method_sha256"]

    def test_dirty_refusal_without_allow(self, monkeypatch: pytest.MonkeyPatch):
        from scripts import superiority_baseline_pareto as harness

        monkeypatch.setattr(harness, "path_is_dirty", lambda _cwd, _rel: True)
        with pytest.raises(DirtyTreeError):
            run_capture(
                label="ORIGINAL",
                outer_samples=9,
                inner_loops=20,
                warmups=1,
                mem_repeats=3,
                allow_dirty=False,
                include_perf_matrix=False,
            )


class TestH4H5H6Receipt:
    def test_labels_restricted(self):
        assert LABELS == ("ORIGINAL", "TEAM_FINAL", "SWARM_FINAL")

    def test_receipt_diagnostic_not_official_original(self):
        receipt = run_capture(
            label="ORIGINAL",
            target_path=PARETO,
            outer_samples=9,
            inner_loops=40,
            warmups=1,
            mem_repeats=3,
            allow_dirty=True,
            include_perf_matrix=False,
        )
        assert receipt["schema"] == "unigrok-superiority-receipt-v2"
        assert receipt["baseline_status"] == "diagnostic_superseded"
        assert receipt["official_original"] is False
        assert receipt["fixture_sha256"] == fixture_sha256(FROZEN_POINTS)
        assert receipt["method"]["method_sha256"]
        assert receipt["oracle_matrix"]["oracle_matrix_sha256"]
        assert "src/swarm/pareto.py" in receipt["bundle"]["sha256_by_file"]
        blob = json.dumps(receipt, sort_keys=True)
        again = json.loads(blob)
        assert again["method"]["method_sha256"] == receipt["method"]["method_sha256"]
