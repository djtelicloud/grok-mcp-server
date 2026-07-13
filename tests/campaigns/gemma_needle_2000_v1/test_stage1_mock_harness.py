from __future__ import annotations

import json
import stat
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from evals.campaigns.gemma_needle_2000_v1.attempt_ledger import AttemptStatus
from evals.campaigns.gemma_needle_2000_v1.provider_adapters import ProviderAdapter
from evals.campaigns.gemma_needle_2000_v1.provider_transports import (
    UniGrokMCPTransport,
    VertexADCTransport,
)
from evals.campaigns.gemma_needle_2000_v1.schemas import canonical_sha256
from evals.campaigns.gemma_needle_2000_v1.stage1_artifacts import ArtifactRef
from evals.campaigns.gemma_needle_2000_v1.stage1_harness import (
    DEFAULT_MANIFEST_PATH,
    DeterministicMockRoleExecutor,
    ManifestContractError,
    SimulatedCrash,
    Stage1RunIncomplete,
    Stage1SafetyHarness,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "ledger" / "attempts.db", tmp_path / "artifacts"


def _artifact_ref(value: dict[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        digest=value["digest"],
        relative_path=value["relative_path"],
        size_bytes=value["size_bytes"],
    )


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _walk_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _walk_keys(child)}
    return set()


def test_exact_mock_topology_is_transport_free_private_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_constructor(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mock gate constructed a provider or transport")

    monkeypatch.setattr(ProviderAdapter, "__init__", forbidden_constructor)
    monkeypatch.setattr(VertexADCTransport, "__init__", forbidden_constructor)
    monkeypatch.setattr(UniGrokMCPTransport, "__init__", forbidden_constructor)
    ledger_path, artifact_root = _paths(tmp_path)
    owner_token = "owner:" + "1" * 64
    harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        owner_token=owner_token,
    )

    first = harness.run()
    calls_after_first = harness.mock_call_counts
    second = harness.run()

    assert first.report == second.report
    assert first.report_artifact == second.report_artifact
    assert (
        harness.mock_call_counts
        == calls_after_first
        == {
            "seed_author": 30,
            "mutator": 30,
            "critic": 30,
            "adjudicator": 30,
        }
    )
    assert first.report["counts"] == {
        "accepted_candidate_artifacts": 150,
        "adjudications": 30,
        "candidate_artifacts": 150,
        "critic_disagreements": 30,
        "executor_transport_calls_observed": 0,
        "mock_role_calls": 120,
        "oracle_evaluations": 150,
        "provider_transport_calls": 0,
        "quarantined_candidate_artifacts": 0,
        "roots": 30,
        "training_writes": 0,
        "variants": 120,
    }
    assert first.report["ledger"]["role_counts"] == {
        role: 30 for role in ("seed_author", "mutator", "critic", "adjudicator")
    }
    assert first.report["ledger"]["status_counts"] == {
        "started": 0,
        "completed": 120,
        "failed": 0,
        "indeterminate": 0,
    }
    assert first.report["pack_candidate_counts"] == {
        pack: 25
        for pack in (
            "tool_selection",
            "gemma_plan_state",
            "recovery_selection",
            "resource_selection",
            "memory_selection",
            "observation_typing",
        )
    }
    assert first.report["outcome_counts"] == {
        "verified_failure": 90,
        "verified_success": 60,
    }
    assert first.report["proposal_counts"] == {
        "invalid_proposal": 60,
        "valid_proposal": 90,
    }
    assert first.report["ttl_counts"] == {"expired": 30, "fresh": 120}
    assert first.report["oracle_counts"] == {"failed": 30, "passed": 120}
    assert first.report["effect_observation_counts"] == {
        "not_observed": 30,
        "observed": 120,
    }
    feedback = first.report["review_feedback"]
    assert sum(feedback["critic_confusion_matrix"].values()) == 120
    assert all(
        sum(matrix.values()) == 20
        for matrix in feedback["critic_confusion_matrix_by_pack"].values()
    )
    assert feedback["adjudicator_resolution_counts"] == {"matches_mechanical": 30}
    assert all(
        matrix == {"matches_mechanical": 5}
        for matrix in feedback["adjudicator_resolution_counts_by_pack"].values()
    )

    report_path = artifact_root / first.report_artifact.relative_path
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact_root.stat().st_mode) == 0o700
    run_contract = harness.artifacts.read(
        _artifact_ref(first.report["run_contract_artifact"])
    )
    assert run_contract["manifest"]["live_transports_enabled"] is False
    assert run_contract["fixture_contract_digest"] == harness.fixture_contract_digest
    assert len(run_contract["implementation_digests"]) == 8
    assert "provider_adapters.py" in run_contract["implementation_digests"]

    candidate_values = [
        harness.artifacts.read(_artifact_ref(item["reference"]))
        for item in first.report["candidate_artifacts"]
    ]
    false_effects = [
        item["envelope"]
        for item in candidate_values
        if not item["envelope"]["observed_receipts"][0]["verification_result"]
    ]
    assert len(false_effects) == 30
    assert all(item["episode_outcome"] == "verified_failure" for item in false_effects)
    assert all("trusted_scenario" in item for item in candidate_values)
    assert all(
        item["trusted_scenario"]["ttl"]["declared_ttl_state"]
        == item["envelope"]["declared_ttl_state"]
        for item in candidate_values
    )

    persisted_requests: dict[str, list[dict[str, Any]]] = {
        role: [] for role in ("seed_author", "mutator", "critic", "adjudicator")
    }
    for attempt in harness.ledger.list_attempts(harness.run_id):
        role_output = harness.artifacts.read(
            harness.ledger.get_output_artifact(attempt["work_item_id"])
        )
        request_payload = role_output["request_payload"]
        ledger_request_digest = attempt["request_digest"].removeprefix("sha256:")
        assert canonical_sha256(request_payload) == ledger_request_digest
        assert role_output["request_digest"] == ledger_request_digest
        persisted_requests[attempt["role"]].append(request_payload)
    assert all(len(items) == 30 for items in persisted_requests.values())
    assert all(
        item["scenario"]["ttl"]["declared_ttl_state"] == "fresh"
        for item in persisted_requests["seed_author"]
    )
    for item in persisted_requests["mutator"]:
        assert item["seed"]
        assert [
            context["scenario"]["ttl"]["declared_ttl_state"]
            for context in item["variant_contexts"]
        ] == ["fresh", "fresh", "expired", "fresh"]
    for role in ("critic", "adjudicator"):
        for item in persisted_requests[role]:
            review = item if role == "critic" else item["review"]
            assert [
                candidate["scenario"]["ttl"]["declared_ttl_state"]
                for candidate in review["candidates"]
            ] == ["fresh", "fresh", "expired", "fresh"]
    forbidden_request_authority = {
        "episode_outcome",
        "expected_effect_observed",
        "expected_result_digest",
        "mechanical_oracle",
        "observed_receipts",
        "proposal_verdict",
        "scenario_digest",
        "stable_effect_id",
    }
    assert all(
        not (_walk_keys(request) & forbidden_request_authority)
        for requests in persisted_requests.values()
        for request in requests
    )

    harness.close()
    fresh_harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        owner_token=owner_token,
    )
    reconstructed = fresh_harness.run()
    assert reconstructed == first
    assert fresh_harness.mock_call_counts == {}
    fresh_harness.close()


def test_reviewers_see_content_but_no_truth_authority_or_lineage(
    tmp_path: Path,
) -> None:
    ledger_path, artifact_root = _paths(tmp_path)
    harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
    )
    harness.run()

    reviews = harness.review_evidence
    assert len(reviews["seed_author"]) == 30
    assert len(reviews["mutator"]) == 30
    assert len(reviews["critic"]) == 30
    assert len(reviews["adjudicator"]) == 30
    forbidden = {
        "ancestor_ids",
        "episode_outcome",
        "expected_effect_observed",
        "expected_result_digest",
        "immutable_artifact_digest",
        "mechanical_oracle",
        "observed_receipts",
        "parent_id",
        "proposal_verdict",
        "provenance",
        "root_id",
        "root_reference",
        "scenario_digest",
        "semantic_signature",
        "stable_effect_id",
    }
    for generation in reviews["seed_author"]:
        assert generation["scenario"]["ttl"]["declared_ttl_state"] == "fresh"
        assert not (_walk_keys(generation) & forbidden)
    for mutation in reviews["mutator"]:
        assert mutation["seed"]
        assert [
            item["scenario"]["ttl"]["declared_ttl_state"]
            for item in mutation["variant_contexts"]
        ] == ["fresh", "fresh", "expired", "fresh"]
        assert not (_walk_keys(mutation) & forbidden)
    for review in reviews["critic"]:
        assert len(review["candidates"]) == 4
        assert {"objective", "observations", "result"}.issubset(review["candidates"][0])
        assert [
            item["scenario"]["ttl"]["declared_ttl_state"]
            for item in review["candidates"]
        ] == ["fresh", "fresh", "expired", "fresh"]
        assert not (_walk_keys(review) & forbidden)
    for adjudication in reviews["adjudicator"]:
        assert len(adjudication["disagreements"]) == 1
        assert len(adjudication["review"]["candidates"]) == 4
        assert [
            item["scenario"]["ttl"]["declared_ttl_state"]
            for item in adjudication["review"]["candidates"]
        ] == ["fresh", "fresh", "expired", "fresh"]
        assert not (_walk_keys(adjudication) & forbidden)


@pytest.mark.parametrize(
    "method_name",
    (
        "_override",
        "_expected_result",
        "seed",
        "mutate",
        "critic",
        "adjudicate",
        "contract_digest",
    ),
)
def test_executor_callables_cannot_be_injected_after_contract_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    ledger_path, artifact_root = _paths(tmp_path)
    harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
    )
    called = False

    def injected(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(AttributeError):
        setattr(harness._executor, method_name, injected)

    monkeypatch.setattr(DeterministicMockRoleExecutor, method_name, injected)
    with pytest.raises(
        Stage1RunIncomplete, match="callable identity|executor code|fixtures changed"
    ):
        harness.run()
    assert called is False


def test_wrong_seed_result_fails_the_bound_root_profile(tmp_path: Path) -> None:
    ledger_path, artifact_root = _paths(tmp_path)
    wrong_seed = {
        "objective": "Choose the intentionally wrong operation.",
        "observations": [{"kind": "scenario", "value": "case-00"}],
        "capabilities": ["select_tool_selection"],
        "forbidden_effects": ["unverified_external_write"],
        "result": {
            "type": "action",
            "tool_name": "reject_tool_selection",
            "tool_arguments": {"choice": "scenario-00"},
        },
        "decision_summary": "Use a contrasting operation for the negative fixture.",
        "plan_state": "ready_for_mechanical_verification",
    }
    harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        fixture_overrides={("seed_author", "tool_selection", 0): wrong_seed},
    )

    with pytest.raises(Stage1RunIncomplete, match="Seed failed trusted fixture"):
        harness.run()
    attempts = harness.ledger.list_attempts(harness.run_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == AttemptStatus.FAILED.value


@pytest.mark.parametrize(
    "payload",
    [
        {
            "objective": "Unsafe authority alias.",
            "observations": [{"stableEffectId": "provider-controlled"}],
            "capabilities": ["select_tool_selection"],
            "forbidden_effects": ["unverified_external_write"],
            "result": {
                "type": "action",
                "tool_name": "select_tool_selection",
                "tool_arguments": {"choice": "scenario-00"},
            },
        },
        {
            "objective": "Contact operator at person@example.com.",
            "observations": [],
            "capabilities": ["select_tool_selection"],
            "forbidden_effects": ["unverified_external_write"],
            "result": {
                "type": "action",
                "tool_name": "select_tool_selection",
                "tool_arguments": {"choice": "scenario-00"},
            },
        },
    ],
)
def test_untrusted_authority_and_pii_fail_before_raw_persistence(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    ledger_path, artifact_root = _paths(tmp_path)
    harness = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        fixture_overrides={("seed_author", "tool_selection", 0): payload},
    )

    with pytest.raises(Stage1RunIncomplete):
        harness.run()

    attempts = harness.ledger.list_attempts(harness.run_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == AttemptStatus.FAILED.value
    artifact_values = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in artifact_root.rglob("*.json")
    ]
    persisted = json.dumps(artifact_values, sort_keys=True)
    assert "person@example.com" not in persisted
    assert "provider-controlled" not in persisted
    assert all("raw_role_output" not in _walk_keys(value) for value in artifact_values)


def test_manifest_preflight_rejects_drift_duplicates_and_nonfinite(
    tmp_path: Path,
) -> None:
    original = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    variants = (
        original.replace(
            '"live_transports_enabled": false',
            '"live_transports_enabled": true',
        ),
        '{"stage":"1_mock_safety_gate","stage":"1_mock_safety_gate"}',
        original.replace('"run_lease_minutes": 60', '"run_lease_minutes": NaN'),
    )
    for index, content in enumerate(variants):
        manifest = tmp_path / f"manifest-{index}.json"
        manifest.write_text(content, encoding="utf-8")
        ledger_path = tmp_path / f"ledger-{index}" / "attempts.db"
        with pytest.raises(ManifestContractError):
            Stage1SafetyHarness(
                ledger_path=ledger_path,
                artifact_root=tmp_path / f"artifacts-{index}",
                manifest_path=manifest,
            )
        assert not ledger_path.exists()


def test_crash_takeover_marks_indeterminate_and_never_retries(tmp_path: Path) -> None:
    ledger_path, artifact_root = _paths(tmp_path)
    clock = MutableClock(datetime(2026, 1, 20, tzinfo=timezone.utc))
    first = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        owner_token="owner:" + "1" * 64,
        clock=clock,
        crash_after_claim=2,
    )
    with pytest.raises(SimulatedCrash):
        first.run()
    assert first.mock_call_counts == {"seed_author": 1}
    first.close()

    clock.advance(timedelta(minutes=61))
    resumed = Stage1SafetyHarness(
        ledger_path=ledger_path,
        artifact_root=artifact_root,
        owner_token="owner:" + "2" * 64,
        clock=clock,
    )
    with pytest.raises(Stage1RunIncomplete, match="indeterminate"):
        resumed.run()

    assert resumed.mock_call_counts == {}
    assert resumed.ledger.get_total_attempts(resumed.run_id) == 2
    statuses = Counter(
        item["status"] for item in resumed.ledger.list_attempts(resumed.run_id)
    )
    assert statuses == {"completed": 1, "indeterminate": 1}
