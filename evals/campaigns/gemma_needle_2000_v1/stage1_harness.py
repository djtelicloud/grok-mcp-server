"""Deterministic, transport-free Stage 1 campaign safety gate.

This module deliberately cannot perform live inference or write training data.
It proves the campaign's authority, accounting, TTL, review, artifact, and
resume contracts using four strictly parsed mock role outputs per root.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .attempt_ledger import AttemptLedger, AttemptStatus
from .role_schemas import (
    AdjudicationVerdictBatch,
    BlindedAdjudicationInput,
    BlindedCandidate,
    BlindedReviewInput,
    BlindedScenarioInput,
    CriticVerdict,
    CriticVerdictBatch,
    FrozenScenarioInput,
    SeedCandidate,
    VariantBatch,
    VariantCandidate,
    parse_adjudication_batch,
    parse_critic_batch,
    parse_seed_candidate,
    parse_variant_batch,
)
from .schemas import (
    BaseRootEnvelope,
    EpisodeOutcome,
    GemmaPlanStatePack,
    MemorySelectionPack,
    ObservationTypingPack,
    OracleRegistryContract,
    PACK_NAMES,
    PackName,
    ProposalVerdict,
    RecoverySelectionPack,
    ResourceSelectionPack,
    TTLFacts,
    TTLState,
    ToolSelectionPack,
    canonical_sha256,
)
from .stage1_artifacts import ArtifactRef, PrivateArtifactStore
from .stage1_oracles import (
    ExecutableOracleRegistry,
    attest_effect_receipt,
    default_oracle_registry,
    verify_effect_receipt,
)
from .validators import MechanicalValidators


CAMPAIGN_ID = "gemma-needle-2000-v1"
SCHEMA_VERSION = "stage1-mock-v1"
PACK_ORDER: tuple[PackName, ...] = (
    "tool_selection",
    "gemma_plan_state",
    "recovery_selection",
    "resource_selection",
    "memory_selection",
    "observation_typing",
)
ROLE_ORDER = ("seed_author", "mutator", "critic", "adjudicator")
EXECUTOR_CALLABLE_NAMES = (
    "_override",
    "_expected_result",
    "seed",
    "mutate",
    "critic",
    "adjudicate",
    "contract_digest",
)
EXPECTED_MANIFEST_DIGEST = (
    "1df910efe15c023308c1aa4457d4438270da901e89884c8249e8d2c673822b25"
)
DEFAULT_SCENARIO_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "campaigns"
    / CAMPAIGN_ID
    / "stage1_manifest.json"
)

_PACK_TYPES: dict[str, type[BaseRootEnvelope]] = {
    "tool_selection": ToolSelectionPack,
    "gemma_plan_state": GemmaPlanStatePack,
    "recovery_selection": RecoverySelectionPack,
    "resource_selection": ResourceSelectionPack,
    "memory_selection": MemorySelectionPack,
    "observation_typing": ObservationTypingPack,
}
VARIANT_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "fresh_positive",
        "expired": False,
        "effect_observed": True,
        "oracle_passed": True,
        "proposal_verdict": "valid_proposal",
        "episode_outcome": "verified_success",
    },
    {
        "name": "oracle_negative",
        "expired": False,
        "effect_observed": True,
        "oracle_passed": False,
        "proposal_verdict": "invalid_proposal",
        "episode_outcome": "verified_failure",
    },
    {
        "name": "expired_action",
        "expired": True,
        "effect_observed": True,
        "oracle_passed": True,
        "proposal_verdict": "invalid_proposal",
        "episode_outcome": "verified_failure",
    },
    {
        "name": "missing_effect",
        "expired": False,
        "effect_observed": False,
        "oracle_passed": True,
        "proposal_verdict": "valid_proposal",
        "episode_outcome": "verified_failure",
    },
)


class ManifestContractError(RuntimeError):
    """The checked-in mock-only manifest changed or enabled unsafe work."""


class Stage1RunIncomplete(RuntimeError):
    """The run contains failed, open, or indeterminate work and cannot pass."""


class SimulatedCrash(RuntimeError):
    """Test-only crash injected after an atomic claim and before role execution."""


@dataclass(frozen=True)
class Stage1RunResult:
    report: dict[str, Any]
    report_artifact: ArtifactRef


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate_token: str
    envelope: BaseRootEnvelope
    trusted_scenario: FrozenScenarioInput
    artifact: ArtifactRef
    gate_passed: bool
    gate_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoleExecution:
    role: str
    work_item_id: str
    logical_work_key: str
    request_digest: str
    request_payload: dict[str, Any]
    raw_output: dict[str, Any]
    parsed: BaseModel
    freshly_claimed: bool
    transport_calls: int
    persisted_terminal_evidence: dict[str, Any] | None = None


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _canonical_payload_digest(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strict_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return an immutable-by-copy, finite, deterministic JSON object."""

    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping guarantees this
        raise TypeError("Role request must be a JSON object.")
    return decoded


def _deterministic_id(prefix: str, *parts: object, length: int = 40) -> str:
    digest = canonical_sha256([str(part) for part in parts])
    if prefix == "candidate":
        digest = "".join(
            "a" if index % 4 == 3 else character
            for index, character in enumerate(digest)
        )
    return f"{prefix}-{digest[:length]}"


def _load_and_verify_manifest(path: Path) -> tuple[dict[str, Any], str]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate manifest key")
            value[key] = item
        return value

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite manifest number {value}")

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManifestContractError(
            "Stage 1 manifest is not readable strict JSON."
        ) from exc
    if not isinstance(value, dict):
        raise ManifestContractError("Stage 1 manifest root must be an object.")
    digest = canonical_sha256(value)
    if digest != EXPECTED_MANIFEST_DIGEST:
        raise ManifestContractError(
            "Stage 1 manifest differs from the reviewed contract."
        )
    expected_fields = {
        "stage": "1_mock_safety_gate",
        "mode": "mock_only",
        "live_transports_enabled": False,
        "training_enabled": False,
        "sealed_evaluation_enabled": False,
        "packs": list(PACK_ORDER),
        "target_roots_per_pack": 5,
        "accepted_root_target": 30,
        "variants_per_root": 4,
        "accepted_variant_target": 120,
        "total_candidate_artifact_target": 150,
        "oracle_evaluation_target": 150,
        "critic_disagreement_target": 30,
        "adjudication_target": 30,
        "provider_transport_attempt_target": 0,
        "training_write_target": 0,
        "total_attempt_limit": 120,
        "role_attempt_limits": {role: 30 for role in ROLE_ORDER},
        "adjudicator_policy": "invoke_only_for_measured_critic_disagreement",
        "fixed_scenario_clock": True,
        "run_lease_minutes": 60,
        "storage_contract": "external_owner_private_0700_files_0600",
        "fixture_truth_contract": "trusted_scenario_digest_binds_expected_result_effect_observation_ttl_and_all_manifest_config_digests",
        "review_visibility_contract": "reviewers_receive_candidate_content_and_ttl_but_no_expected_truth_authority_lineage_receipts_or_digests",
        "role_output_persistence_contract": "strict_parse_and_secret_pii_path_scan_before_any_raw_role_output_is_persisted",
        "run_reconstruction_contract": "immutable_config_and_attempt_artifacts_plus_ledger_snapshot_reconstruct_the_report_without_process_counters",
        "feedback_contract": "persisted_critic_confusion_matrices_and_disagreement_only_adjudication_are_reconstructed_per_pack_against_mechanical_truth",
    }
    for field, expected in expected_fields.items():
        if value.get(field) != expected:
            raise ManifestContractError(f"Stage 1 manifest field {field!r} drifted.")
    if set(value["packs"]) != PACK_NAMES:
        raise ManifestContractError(
            "Stage 1 manifest does not contain the exact six packs."
        )
    return value, digest


class DeterministicMockRoleExecutor:
    """Pure local role fixtures with inspectable calls and no transport surface."""

    __slots__ = (
        "_override_payloads",
        "call_counts",
        "review_inputs",
        "transport_calls",
    )

    def __init__(
        self,
        *,
        overrides: Mapping[tuple[str, str, int], Mapping[str, Any]] | None = None,
    ) -> None:
        self._override_payloads: dict[tuple[str, str, int], str] = {}
        for key, value in (overrides or {}).items():
            if (
                not isinstance(key, tuple)
                or len(key) != 3
                or key[0] not in ROLE_ORDER
                or key[1] not in PACK_ORDER
                or not isinstance(key[2], int)
                or isinstance(key[2], bool)
                or not 0 <= key[2] < 5
                or not isinstance(value, Mapping)
            ):
                raise ValueError(
                    "Fixture override keys and payloads must match a bounded role case."
                )
            try:
                encoded = json.dumps(
                    dict(value),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Fixture overrides must contain strict JSON objects."
                ) from exc
            self._override_payloads[key] = encoded
        self.call_counts: Counter[str] = Counter()
        self.review_inputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.transport_calls = 0

    def _override(
        self, role: str, pack_name: str, root_index: int
    ) -> Mapping[str, Any] | None:
        encoded = self._override_payloads.get((role, pack_name, root_index))
        return None if encoded is None else json.loads(encoded)

    def contract_digest(self) -> str:
        methods = {
            name: inspect.getsource(getattr(DeterministicMockRoleExecutor, name))
            for name in EXECUTOR_CALLABLE_NAMES
        }
        overrides = [
            {
                "key": list(key),
                "payload_digest": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
            for key, value in sorted(self._override_payloads.items())
        ]
        return canonical_sha256(
            {
                "executor": "deterministic_mock_v1",
                "methods": methods,
                "overrides": overrides,
            }
        )

    @staticmethod
    def _expected_result(pack_name: str, root_index: int) -> dict[str, Any]:
        return {
            "type": "action",
            "tool_name": f"select_{pack_name}",
            "tool_arguments": {"choice": f"scenario-{root_index:02d}"},
        }

    def seed(
        self,
        pack_name: str,
        root_index: int,
        generation_input: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.call_counts["seed_author"] += 1
        self.review_inputs["seed_author"].append(
            json.loads(json.dumps(generation_input))
        )
        override = self._override("seed_author", pack_name, root_index)
        if override is not None:
            return override
        return {
            "objective": f"Select the safe operation for scenario {root_index:02d}.",
            "observations": [{"kind": "scenario", "value": f"case-{root_index:02d}"}],
            "capabilities": [f"select_{pack_name}"],
            "forbidden_effects": ["unverified_external_write"],
            "result": self._expected_result(pack_name, root_index),
            "decision_summary": "Use the operation supported by the bounded observation.",
            "plan_state": "ready_for_mechanical_verification",
        }

    def mutate(
        self,
        pack_name: str,
        root_index: int,
        _seed: SeedCandidate,
        generation_input: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.call_counts["mutator"] += 1
        self.review_inputs["mutator"].append(json.loads(json.dumps(generation_input)))
        override = self._override("mutator", pack_name, root_index)
        if override is not None:
            return override
        expected = self._expected_result(pack_name, root_index)
        wrong = {
            "type": "action",
            "tool_name": f"reject_{pack_name}",
            "tool_arguments": {"choice": f"scenario-{root_index:02d}"},
        }
        return {
            "variants": [
                {
                    "objective": f"Fresh positive variation {root_index:02d}.",
                    "observations": [{"kind": "variation", "value": "positive"}],
                    "result": expected,
                    "decision_summary": "Apply the verified operation.",
                    "plan_state": "fresh_candidate",
                },
                {
                    "objective": f"Incorrect-operation variation {root_index:02d}.",
                    "observations": [{"kind": "variation", "value": "wrong_result"}],
                    "result": wrong,
                    "decision_summary": "Propose the contrasting operation.",
                    "plan_state": "oracle_negative_candidate",
                },
                {
                    "objective": f"Expired-context variation {root_index:02d}.",
                    "observations": [{"kind": "variation", "value": "expired"}],
                    "result": expected,
                    "decision_summary": "Preserve the proposed operation for TTL checking.",
                    "plan_state": "expired_candidate",
                },
                {
                    "objective": f"Missing-effect variation {root_index:02d}.",
                    "observations": [{"kind": "variation", "value": "missing_effect"}],
                    "result": expected,
                    "decision_summary": "Request mechanical effect observation.",
                    "plan_state": "negative_receipt_candidate",
                },
            ]
        }

    def critic(
        self, pack_name: str, root_index: int, review_input: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.call_counts["critic"] += 1
        self.review_inputs["critic"].append(json.loads(json.dumps(review_input)))
        override = self._override("critic", pack_name, root_index)
        if override is not None:
            return override
        verdicts = (
            ProposalVerdict.INVALID_PROPOSAL,
            ProposalVerdict.INVALID_PROPOSAL,
            ProposalVerdict.INVALID_PROPOSAL,
            ProposalVerdict.VALID_PROPOSAL,
        )
        return {
            "verdicts": [
                {
                    "candidate_token": token,
                    "advisory_verdict": verdict.value,
                    "reason_code": f"critic_position_{index}",
                    "summary": "Advisory review from blinded candidate evidence.",
                }
                for index, (candidate, verdict) in enumerate(
                    zip(review_input["candidates"], verdicts, strict=True)
                )
                for token in (candidate["candidate_token"],)
            ]
        }

    def adjudicate(
        self, pack_name: str, root_index: int, review_input: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.call_counts["adjudicator"] += 1
        self.review_inputs["adjudicator"].append(json.loads(json.dumps(review_input)))
        override = self._override("adjudicator", pack_name, root_index)
        if override is not None:
            return override
        review = review_input["review"]
        disagreement_tokens = {
            item["candidate_token"] for item in review_input["disagreements"]
        }
        ordered_tokens = [
            item["candidate_token"]
            for item in review["candidates"]
            if item["candidate_token"] in disagreement_tokens
        ]
        return {
            "verdicts": [
                {
                    "candidate_token": token,
                    "advisory_verdict": ProposalVerdict.VALID_PROPOSAL.value,
                    "reason_code": f"adjudicated_position_{index}",
                    "summary": "Advisory resolution from blinded evidence and critic labels.",
                }
                for index, token in enumerate(ordered_tokens)
            ]
        }


RoleModel = TypeVar("RoleModel", bound=BaseModel)


class Stage1SafetyHarness:
    """Run and reconstruct the exact six-pack mock-only safety campaign."""

    def __init__(
        self,
        *,
        ledger_path: Path | str,
        artifact_root: Path | str,
        manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
        fixture_overrides: Mapping[tuple[str, str, int], Mapping[str, Any]]
        | None = None,
        owner_token: str | None = None,
        clock: Callable[[], datetime] | None = None,
        scenario_evaluated_at: datetime = DEFAULT_SCENARIO_TIME,
        crash_after_claim: int | None = None,
    ) -> None:
        self.manifest, self.manifest_digest = _load_and_verify_manifest(
            Path(manifest_path)
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.scenario_evaluated_at = _aware_utc(
            scenario_evaluated_at, "scenario_evaluated_at"
        )
        if fixture_overrides is not None and len(fixture_overrides) > 8:
            raise ValueError("Stage 1 fixture overrides are limited to eight cases.")
        self._executor = DeterministicMockRoleExecutor(overrides=fixture_overrides)
        self._executor_callable_contract = {
            name: getattr(DeterministicMockRoleExecutor, name)
            for name in EXECUTOR_CALLABLE_NAMES
        }
        self.owner_token = owner_token or AttemptLedger.make_owner_token()
        self.crash_after_claim = crash_after_claim
        self._fresh_claims = 0
        self.artifacts = PrivateArtifactStore(artifact_root)
        self.oracle_registry: ExecutableOracleRegistry = default_oracle_registry()
        oracle_definition = self.oracle_registry.definition(
            "expected_result_digest", "1.0"
        )
        self.fixture_contract = {
            "packs": list(PACK_ORDER),
            "roots_per_pack": 5,
            "expected_results": {
                pack: [
                    canonical_sha256(
                        DeterministicMockRoleExecutor._expected_result(pack, index)
                    )
                    for index in range(5)
                ]
                for pack in PACK_ORDER
            },
            "variant_profiles": list(VARIANT_PROFILES),
        }
        self.fixture_contract_digest = canonical_sha256(self.fixture_contract)
        receipt_verifier_digest = canonical_sha256(
            {
                "attest_source": inspect.getsource(attest_effect_receipt),
                "verify_source": inspect.getsource(verify_effect_receipt),
            }
        )
        implementation_names = (
            "stage1_harness.py",
            "schemas.py",
            "role_schemas.py",
            "validators.py",
            "stage1_oracles.py",
            "stage1_artifacts.py",
            "attempt_ledger.py",
            "provider_adapters.py",
        )
        implementation_digests = {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in implementation_names
        }
        self.implementation_digests = implementation_digests
        self.executor_contract_digest = self._executor.contract_digest()
        self.config_digest = canonical_sha256(
            {
                "campaign_id": CAMPAIGN_ID,
                "schema_version": SCHEMA_VERSION,
                "manifest_digest": self.manifest_digest,
                "scenario_evaluated_at": self.scenario_evaluated_at,
                "executor_contract_digest": self.executor_contract_digest,
                "fixture_contract_digest": self.fixture_contract_digest,
                "implementation_digests": implementation_digests,
                "oracle_code_digest": oracle_definition.code_digest,
                "oracle_declared_inputs": list(oracle_definition.declared_inputs),
                "oracle_verifier_digest": self.oracle_registry._verifier_code_digest(),
                "receipt_verifier_digest": receipt_verifier_digest,
                "strict_gate_digest": canonical_sha256(
                    {"source": inspect.getsource(MechanicalValidators.strict_gate)}
                ),
            }
        )
        self.run_id = f"stage1-mock-{self.config_digest[:24]}"
        self.ledger = AttemptLedger(
            ledger_path,
            total_limit=self.manifest["total_attempt_limit"],
            role_limits=self.manifest["role_attempt_limits"],
            clock=self.clock,
        )

    def close(self) -> None:
        self.ledger.close()

    def __enter__(self) -> Stage1SafetyHarness:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    @property
    def mock_call_counts(self) -> dict[str, int]:
        return dict(self._executor.call_counts)

    @property
    def review_evidence(self) -> dict[str, list[dict[str, Any]]]:
        return json.loads(json.dumps(self._executor.review_inputs))

    def _assert_executor_contract(self) -> None:
        if any(
            getattr(DeterministicMockRoleExecutor, name) is not expected_callable
            for name, expected_callable in self._executor_callable_contract.items()
        ):
            raise Stage1RunIncomplete(
                "Deterministic executor callable identity changed after binding."
            )
        if self._executor.contract_digest() != self.executor_contract_digest:
            raise Stage1RunIncomplete(
                "Deterministic executor code or fixtures changed after binding."
            )
        if self._executor.transport_calls != 0:
            raise Stage1RunIncomplete(
                "Mock-only executor observed a transport attempt."
            )

    def _acquire_lease(self) -> None:
        now = _aware_utc(self.clock(), "wall clock")
        self.ledger.acquire_run_lease(
            run_id=self.run_id,
            owner_token=self.owner_token,
            campaign_id=CAMPAIGN_ID,
            schema_version=SCHEMA_VERSION,
            manifest_digest=self.manifest_digest,
            config_digest=self.config_digest,
            lease_deadline=now + timedelta(minutes=self.manifest["run_lease_minutes"]),
        )

    def _quarantine_attempt(
        self,
        *,
        role: str,
        logical_work_key: str,
        reason_code: str,
        raw_digest: str,
    ) -> ArtifactRef:
        return self.artifacts.write_content_addressed(
            ("runs", self.run_id, "quarantine", role),
            "attempt",
            {
                "artifact_kind": "attempt_quarantine",
                "logical_work_key": logical_work_key,
                "raw_payload_digest": raw_digest,
                "reason_code": reason_code,
                "role": role,
                "run_id": self.run_id,
            },
        )

    def _perform_role(
        self,
        *,
        role: str,
        pack_name: str,
        root_index: int,
        root_reference: str,
        request_payload: Mapping[str, Any],
        execute: Callable[[], Mapping[str, Any]],
        parse: Callable[[Mapping[str, Any]], RoleModel],
    ) -> RoleExecution:
        logical_work_key = AttemptLedger.make_logical_work_key(
            self.run_id, role, pack_name, str(root_index)
        )
        request_digest = canonical_sha256(request_payload)
        persisted_request_payload = _strict_json_object(request_payload)
        claim = self.ledger.claim(
            role=role,
            logical_work_key=logical_work_key,
            run_id=self.run_id,
            owner_token=self.owner_token,
            campaign_id=CAMPAIGN_ID,
            schema_version=SCHEMA_VERSION,
            manifest_digest=self.manifest_digest,
            config_digest=self.config_digest,
            template_digest=canonical_sha256({"role": role, "template": "mock-v1"}),
            provider="mock_only",
            model="deterministic.v1",
            request_digest=request_digest,
            cache_digest=canonical_sha256(
                {"logical_work_key": logical_work_key, "request_digest": request_digest}
            ),
            root_reference=root_reference,
        )
        if not claim.claimed:
            if claim.status is not AttemptStatus.COMPLETED:
                raise Stage1RunIncomplete(
                    f"Logical work is terminal or open with status {claim.status.value!r}."
                )
            persisted = self.artifacts.read(
                self.ledger.get_output_artifact(claim.work_item_id)
            )
            raw_output = persisted.get("raw_role_output")
            terminal_evidence = persisted.get("terminal_evidence")
            transport_calls = persisted.get("transport_calls")
            stored_request_payload = persisted.get("request_payload")
            if (
                not isinstance(raw_output, dict)
                or not isinstance(terminal_evidence, dict)
                or not isinstance(stored_request_payload, dict)
                or canonical_sha256(stored_request_payload) != request_digest
                or transport_calls != 0
            ):
                raise Stage1RunIncomplete("Persisted role output artifact is invalid.")
            return RoleExecution(
                role=role,
                work_item_id=claim.work_item_id,
                logical_work_key=logical_work_key,
                request_digest=request_digest,
                request_payload=stored_request_payload,
                raw_output=raw_output,
                parsed=parse(raw_output),
                freshly_claimed=False,
                transport_calls=transport_calls,
                persisted_terminal_evidence=terminal_evidence,
            )

        self._fresh_claims += 1
        if self.crash_after_claim == self._fresh_claims:
            raise SimulatedCrash(
                "Injected crash after claim; work remains unrepeatable."
            )

        raw_output: Mapping[str, Any] | None = None
        try:
            self._assert_executor_contract()
            transport_calls_before = self._executor.transport_calls
            raw_output = execute()
            transport_calls_after = self._executor.transport_calls
            self._assert_executor_contract()
            if transport_calls_after != transport_calls_before:
                raise ValueError("Mock-only role attempted provider transport work.")
            parsed = parse(raw_output)
            if not MechanicalValidators.scan_provider_payload(
                parsed.model_dump(mode="json")
            ):
                raise ValueError("Role output contains private or unsafe content.")
            return RoleExecution(
                role=role,
                work_item_id=claim.work_item_id,
                logical_work_key=logical_work_key,
                request_digest=request_digest,
                request_payload=persisted_request_payload,
                raw_output=dict(raw_output),
                parsed=parsed,
                freshly_claimed=True,
                transport_calls=transport_calls_after - transport_calls_before,
            )
        except SimulatedCrash:
            raise
        except Exception as exc:
            raw_digest = _canonical_payload_digest(
                raw_output if raw_output is not None else {"no_output": True}
            )
            quarantine = self._quarantine_attempt(
                role=role,
                logical_work_key=logical_work_key,
                reason_code="invalid_role_output",
                raw_digest=raw_digest,
            )
            self.ledger.fail(
                claim.work_item_id,
                owner_token=self.owner_token,
                terminal_code="invalid_role_output",
                response_digest=raw_digest,
                output_artifact=quarantine,
                artifact_verifier=self.artifacts.read,
            )
            raise Stage1RunIncomplete(
                f"Strict {role} output parsing failed before authority assignment."
            ) from exc

    def _complete_role(
        self, execution: RoleExecution, *, terminal_evidence: dict[str, Any]
    ) -> None:
        """Commit a role only after its downstream mechanical contract passes."""

        if not execution.freshly_claimed:
            if execution.persisted_terminal_evidence != terminal_evidence:
                raise Stage1RunIncomplete(
                    "Reconstructed mechanical evidence differs from the terminal artifact."
                )
            return
        output = self.artifacts.write_content_addressed(
            ("runs", self.run_id, "attempts", execution.role),
            "role-output",
            {
                "artifact_kind": "mock_role_output",
                "logical_work_key": execution.logical_work_key,
                "raw_role_output": execution.raw_output,
                "request_digest": execution.request_digest,
                "request_payload": execution.request_payload,
                "role": execution.role,
                "run_id": self.run_id,
                "terminal_evidence": terminal_evidence,
                "transport_calls": execution.transport_calls,
            },
        )
        self.ledger.complete(
            execution.work_item_id,
            owner_token=self.owner_token,
            response_digest=_canonical_payload_digest(execution.raw_output),
            receipt_digest=canonical_sha256(
                {
                    "execution": "deterministic_mock",
                    "logical_work_key": execution.logical_work_key,
                    "terminal_evidence_digest": canonical_sha256(terminal_evidence),
                    "transport_calls": execution.transport_calls,
                }
            ),
            output_artifact=output,
            artifact_verifier=self.artifacts.read,
        )

    def _fail_role(
        self,
        execution: RoleExecution,
        *,
        terminal_code: str,
        terminal_evidence: dict[str, Any],
    ) -> None:
        if not execution.freshly_claimed:
            raise Stage1RunIncomplete(
                "Persisted completed work cannot be rewritten as a failure."
            )
        quarantine = self.artifacts.write_content_addressed(
            ("runs", self.run_id, "quarantine", execution.role),
            "mechanical-failure",
            {
                "artifact_kind": "mechanical_failure",
                "logical_work_key": execution.logical_work_key,
                "raw_payload_digest": _canonical_payload_digest(execution.raw_output),
                "reason_code": terminal_code,
                "role": execution.role,
                "run_id": self.run_id,
                "terminal_evidence": terminal_evidence,
            },
        )
        self.ledger.fail(
            execution.work_item_id,
            owner_token=self.owner_token,
            terminal_code=terminal_code,
            response_digest=_canonical_payload_digest(execution.raw_output),
            output_artifact=quarantine,
            artifact_verifier=self.artifacts.read,
        )

    def _ttl(self, *, expired: bool = False) -> TTLFacts:
        evaluated = self.scenario_evaluated_at
        issued = evaluated - timedelta(minutes=30)
        expires = (
            evaluated - timedelta(minutes=1)
            if expired
            else evaluated + timedelta(minutes=30)
        )
        return TTLFacts(
            issued_at=issued,
            expires_at=expires,
            evaluated_at=evaluated,
            declared_ttl_state=TTLState.EXPIRED if expired else TTLState.FRESH,
        )

    def _make_envelope(
        self,
        *,
        pack_name: PackName,
        root_index: int,
        candidate_token: str,
        raw_candidate: SeedCandidate | VariantCandidate,
        parent_id: str | None,
        expired: bool,
        effect_observed: bool,
    ) -> tuple[BaseRootEnvelope, FrozenScenarioInput]:
        ttl = self._ttl(expired=expired)
        root_id = _deterministic_id(
            "root" if parent_id is None else "variant", candidate_token
        )
        leakage_group = _deterministic_id("leakage", pack_name, root_index)
        definition = self.oracle_registry.definition("expected_result_digest", "1.0")
        output_contract_digest = canonical_sha256({"contract": "stage1_candidate_v1"})
        tool_catalog_digest = canonical_sha256(
            {"pack": pack_name, "catalog": "mock-v1"}
        )
        schema_digest = canonical_sha256({"schema": SCHEMA_VERSION})
        prompt_template_digest = canonical_sha256(
            {"role": "candidate", "template": "mock-v1"}
        )
        expected_result_digest = canonical_sha256(
            DeterministicMockRoleExecutor._expected_result(pack_name, root_index)
        )
        envelope_fields: dict[str, Any] = {
            "pack_name": pack_name,
            "output_contract_name": "stage1_candidate",
            "output_contract_version": "1.0",
            "output_contract_digest": output_contract_digest,
            "tool_catalog_digest": tool_catalog_digest,
            "schema_digest": schema_digest,
            "prompt_template_digest": prompt_template_digest,
            "generator_config_digest": self.config_digest,
            "objective": raw_candidate.objective,
            "observations": raw_candidate.observations,
            "capabilities": [f"select_{pack_name}"],
            "forbidden_effects": ["unverified_external_write"],
            "issued_at": ttl.issued_at,
            "expires_at": ttl.expires_at,
            "evaluated_at": ttl.evaluated_at,
            "declared_ttl_state": ttl.declared_ttl_state,
            "stable_effect_id": "effect-pending",
            "result": raw_candidate.result,
            "mechanical_oracle": OracleRegistryContract(
                name=definition.name,
                version=definition.version,
                code_digest=definition.code_digest,
                declared_inputs=list(definition.declared_inputs),
                deterministic_parameters={},
            ),
            "proposal_verdict": ProposalVerdict.UNVERIFIED,
            "episode_outcome": EpisodeOutcome.UNVERIFIED,
            "required_receipt_specs": ["effect_observation"],
            "observed_receipts": [],
            "root_id": root_id,
            "parent_id": parent_id,
            "ancestor_ids": [] if parent_id is None else [parent_id],
            "leakage_group": leakage_group,
            "provenance": {
                "campaign": CAMPAIGN_ID,
                "mode": "mock_only",
                "candidate_token": candidate_token,
            },
            "decision_summary": raw_candidate.decision_summary,
            "plan_state": raw_candidate.plan_state,
        }
        if pack_name == "gemma_plan_state":
            envelope_fields["long_chain_transitions"] = 8
        envelope = _PACK_TYPES[pack_name].model_validate(envelope_fields)
        envelope.stable_effect_id = envelope.compute_stable_effect_id()
        trusted_scenario = FrozenScenarioInput.mint(
            pack_name=pack_name,
            root_reference=root_id,
            ttl=ttl,
            expected_result_digest=expected_result_digest,
            expected_effect_observed=effect_observed,
            output_contract_digest=output_contract_digest,
            tool_catalog_digest=tool_catalog_digest,
            schema_digest=schema_digest,
            prompt_template_digest=prompt_template_digest,
            generator_config_digest=self.config_digest,
        )
        self.oracle_registry.attest(
            envelope,
            name="expected_result_digest",
            version="1.0",
            deterministic_parameters={"expected_result_digest": expected_result_digest},
        )
        envelope.observed_receipts = [attest_effect_receipt(envelope, trusted_scenario)]
        envelope.proposal_verdict = MechanicalValidators.derive_proposal_verdict(
            envelope
        )
        envelope.episode_outcome = MechanicalValidators.derive_episode_outcome(envelope)
        envelope.finalize_integrity()
        return envelope, trusted_scenario

    def _evaluate_candidate(
        self,
        *,
        envelope: BaseRootEnvelope,
        trusted_scenario: FrozenScenarioInput,
        candidate_token: str,
        pack_artifact_digests: set[str],
        pack_semantic_signatures: set[str],
        trajectory_effect_ids: set[str],
    ) -> CandidateEvaluation:
        passed, reasons = MechanicalValidators.strict_gate(
            envelope,
            trusted_scenario=trusted_scenario,
            artifact_digests=pack_artifact_digests,
            semantic_signatures=pack_semantic_signatures,
            trajectory_effect_ids=trajectory_effect_ids,
            executable_oracle_verifier=self.oracle_registry.verify,
            executable_receipt_verifier=verify_effect_receipt,
        )
        disposition = "accepted" if passed else "quarantine"
        artifact = self.artifacts.write_content_addressed(
            ("runs", self.run_id, "candidates", envelope.pack_name, disposition),
            "candidate",
            {
                "artifact_kind": "stage1_candidate",
                "candidate_token": candidate_token,
                "envelope": envelope.model_dump(mode="json", exclude_none=False),
                "gate_passed": passed,
                "gate_reasons": list(reasons),
                "run_id": self.run_id,
                "trusted_scenario": trusted_scenario.model_dump(
                    mode="json", exclude_none=False
                ),
            },
        )
        if passed:
            assert envelope.immutable_artifact_digest is not None
            assert envelope.semantic_signature is not None
            pack_artifact_digests.add(envelope.immutable_artifact_digest)
            pack_semantic_signatures.add(envelope.semantic_signature)
            if envelope.result.type.value == "action":
                trajectory_effect_ids.add(envelope.stable_effect_id)
        return CandidateEvaluation(
            candidate_token=candidate_token,
            envelope=envelope,
            trusted_scenario=trusted_scenario,
            artifact=artifact,
            gate_passed=passed,
            gate_reasons=reasons,
        )

    @staticmethod
    def _review_projection(
        evaluations: list[CandidateEvaluation],
        *,
        critic_disagreements: list[CriticVerdict] | None = None,
    ) -> dict[str, Any]:
        review = BlindedReviewInput(
            candidates=[
                BlindedCandidate(
                    candidate_token=item.candidate_token,
                    scenario=item.trusted_scenario.blinded_view(),
                    capabilities=item.envelope.capabilities,
                    decision_summary=item.envelope.decision_summary,
                    forbidden_effects=item.envelope.forbidden_effects,
                    objective=item.envelope.objective,
                    observations=item.envelope.observations,
                    plan_state=item.envelope.plan_state,
                    result=item.envelope.result,
                )
                for item in evaluations
            ],
            rubric=[
                "Judge only the visible proposal and observation evidence.",
                "Return one advisory verdict for each blinded token.",
                "Do not infer mechanical success or completion authority.",
            ],
        )
        if critic_disagreements is None:
            return review.model_dump(mode="json")
        return BlindedAdjudicationInput(
            review=review, disagreements=critic_disagreements
        ).model_dump(mode="json")

    def _canonical_report(
        self,
        *,
        candidates: list[CandidateEvaluation],
        disagreement_count: int,
        run_contract_artifact: ArtifactRef,
    ) -> dict[str, Any]:
        attempts = self.ledger.list_attempts(self.run_id)
        attempt_outputs = [
            self.artifacts.read(self.ledger.get_output_artifact(item["work_item_id"]))
            for item in attempts
        ]
        role_counts = Counter(str(item["role"]) for item in attempts)
        status_counts = Counter(str(item["status"]) for item in attempts)
        outcome_counts = Counter(
            item.envelope.episode_outcome.value for item in candidates
        )
        proposal_counts = Counter(
            item.envelope.proposal_verdict.value for item in candidates
        )
        pack_counts = Counter(item.envelope.pack_name for item in candidates)
        ttl_counts = Counter(
            item.envelope.declared_ttl_state.value for item in candidates
        )
        oracle_counts = Counter(
            "passed" if item.envelope.mechanical_oracle.pass_fail_result else "failed"
            for item in candidates
        )
        effect_observation_counts = Counter(
            "observed"
            if all(
                receipt.verification_result
                for receipt in item.envelope.observed_receipts
            )
            else "not_observed"
            for item in candidates
        )
        confusion: Counter[str] = Counter()
        confusion_by_pack: dict[str, Counter[str]] = {
            pack: Counter() for pack in PACK_ORDER
        }
        adjudicator_resolution: Counter[str] = Counter()
        adjudicator_resolution_by_pack: dict[str, Counter[str]] = {
            pack: Counter() for pack in PACK_ORDER
        }
        for output in attempt_outputs:
            role = output.get("role")
            evidence = output.get("terminal_evidence")
            raw = output.get("raw_role_output")
            if not isinstance(evidence, dict) or not isinstance(raw, dict):
                raise Stage1RunIncomplete(
                    "Terminal attempt evidence is not reconstructable."
                )
            pack = evidence.get("pack_name")
            mechanical = evidence.get("mechanical_proposals")
            if role not in ("critic", "adjudicator"):
                continue
            if pack not in PACK_ORDER or not isinstance(mechanical, dict):
                raise Stage1RunIncomplete("Review feedback evidence is incomplete.")
            verdicts = raw.get("verdicts")
            if not isinstance(verdicts, list):
                raise Stage1RunIncomplete("Review verdict artifact is incomplete.")
            for verdict in verdicts:
                token = verdict.get("candidate_token")
                advisory = verdict.get("advisory_verdict")
                mechanical_verdict = mechanical.get(token)
                if not isinstance(advisory, str) or not isinstance(
                    mechanical_verdict, str
                ):
                    raise Stage1RunIncomplete("Review feedback labels are incomplete.")
                if role == "critic":
                    cell = f"mechanical={mechanical_verdict}|critic={advisory}"
                    confusion[cell] += 1
                    confusion_by_pack[pack][cell] += 1
                else:
                    resolution = (
                        "matches_mechanical"
                        if advisory == mechanical_verdict
                        else "differs_from_mechanical"
                    )
                    adjudicator_resolution[resolution] += 1
                    adjudicator_resolution_by_pack[pack][resolution] += 1
        stable_attempt_projection = [
            {
                "logical_work_key": item["logical_work_key"],
                "output_digest": item["output_digest"],
                "role": item["role"],
                "status": item["status"],
                "terminal_code": item["terminal_code"],
                "work_item_id": item["work_item_id"],
            }
            for item in attempts
        ]
        return {
            "artifact_kind": "stage1_mock_safety_report",
            "campaign_id": CAMPAIGN_ID,
            "config_digest": self.config_digest,
            "manifest_digest": self.manifest_digest,
            "run_id": self.run_id,
            "run_contract_artifact": run_contract_artifact.as_dict(),
            "schema_version": SCHEMA_VERSION,
            "counts": {
                "accepted_candidate_artifacts": sum(
                    item.gate_passed for item in candidates
                ),
                "adjudications": role_counts["adjudicator"],
                "candidate_artifacts": len(candidates),
                "critic_disagreements": disagreement_count,
                "executor_transport_calls_observed": self._executor.transport_calls,
                "mock_role_calls": len(attempts),
                "oracle_evaluations": len(candidates),
                "provider_transport_calls": sum(
                    int(output.get("transport_calls", -1)) for output in attempt_outputs
                ),
                "quarantined_candidate_artifacts": sum(
                    not item.gate_passed for item in candidates
                ),
                "roots": sum(
                    1 for item in candidates if item.envelope.parent_id is None
                ),
                "training_writes": 0,
                "variants": sum(
                    1 for item in candidates if item.envelope.parent_id is not None
                ),
            },
            "candidate_artifacts": [
                {
                    "candidate_token": item.candidate_token,
                    "gate_passed": item.gate_passed,
                    "reference": item.artifact.as_dict(),
                }
                for item in candidates
            ],
            "ledger": {
                "attempts": stable_attempt_projection,
                "role_counts": {role: role_counts[role] for role in ROLE_ORDER},
                "status_counts": {
                    status.value: status_counts[status.value]
                    for status in AttemptStatus
                },
                "total_attempts": len(attempts),
            },
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "oracle_counts": dict(sorted(oracle_counts.items())),
            "effect_observation_counts": dict(
                sorted(effect_observation_counts.items())
            ),
            "pack_candidate_counts": {pack: pack_counts[pack] for pack in PACK_ORDER},
            "proposal_counts": dict(sorted(proposal_counts.items())),
            "ttl_counts": dict(sorted(ttl_counts.items())),
            "review_feedback": {
                "adjudicator_resolution_counts": dict(
                    sorted(adjudicator_resolution.items())
                ),
                "adjudicator_resolution_counts_by_pack": {
                    pack: dict(sorted(adjudicator_resolution_by_pack[pack].items()))
                    for pack in PACK_ORDER
                },
                "critic_confusion_matrix": dict(sorted(confusion.items())),
                "critic_confusion_matrix_by_pack": {
                    pack: dict(sorted(confusion_by_pack[pack].items()))
                    for pack in PACK_ORDER
                },
            },
            "safety": {
                "fixed_scenario_clock": self.scenario_evaluated_at.isoformat(),
                "live_transports_enabled": False,
                "sealed_evaluation_enabled": False,
                "training_enabled": False,
            },
        }

    def run(self) -> Stage1RunResult:
        """Execute or deterministically reconstruct the exact reviewed topology."""

        self._acquire_lease()
        self._assert_executor_contract()
        run_contract_artifact = self.artifacts.write_content_addressed(
            ("runs", self.run_id, "contracts"),
            "run-contract",
            {
                "artifact_kind": "stage1_mock_run_contract",
                "campaign_id": CAMPAIGN_ID,
                "config_digest": self.config_digest,
                "executor_contract_digest": self.executor_contract_digest,
                "fixture_contract": self.fixture_contract,
                "fixture_contract_digest": self.fixture_contract_digest,
                "implementation_digests": self.implementation_digests,
                "manifest": self.manifest,
                "manifest_digest": self.manifest_digest,
                "run_id": self.run_id,
                "scenario_evaluated_at": self.scenario_evaluated_at.isoformat(),
                "schema_version": SCHEMA_VERSION,
            },
        )
        candidates: list[CandidateEvaluation] = []
        disagreement_count = 0
        artifact_digests: dict[str, set[str]] = defaultdict(set)
        semantic_signatures: dict[str, set[str]] = defaultdict(set)

        for pack_name in PACK_ORDER:
            for root_index in range(5):
                root_reference = _deterministic_id(
                    "rootref", self.run_id, pack_name, root_index
                )
                seed_request = {
                    "mode": "mock_only",
                    "role": "seed_author",
                    "scenario": BlindedScenarioInput(
                        pack_name=pack_name,
                        ttl=self._ttl(expired=False),
                    ).model_dump(mode="json"),
                    "scenario_index": root_index,
                }
                seed_execution = self._perform_role(
                    role="seed_author",
                    pack_name=pack_name,
                    root_index=root_index,
                    root_reference=root_reference,
                    request_payload=seed_request,
                    execute=lambda p=pack_name, index=root_index, request=seed_request: (
                        self._executor.seed(p, index, request)
                    ),
                    parse=parse_seed_candidate,
                )
                seed = seed_execution.parsed
                assert isinstance(seed, SeedCandidate)
                root_token = _deterministic_id(
                    "candidate", self.run_id, pack_name, root_index, "seed", length=32
                )
                root_envelope, root_scenario = self._make_envelope(
                    pack_name=pack_name,
                    root_index=root_index,
                    candidate_token=root_token,
                    raw_candidate=seed,
                    parent_id=None,
                    expired=False,
                    effect_observed=True,
                )
                root_evaluation = self._evaluate_candidate(
                    envelope=root_envelope,
                    trusted_scenario=root_scenario,
                    candidate_token=root_token,
                    pack_artifact_digests=artifact_digests[pack_name],
                    pack_semantic_signatures=semantic_signatures[pack_name],
                    trajectory_effect_ids=set(),
                )
                candidates.append(root_evaluation)
                seed_contract_matches = seed.capabilities == [
                    f"select_{pack_name}"
                ] and seed.forbidden_effects == ["unverified_external_write"]
                root_profile_matches = all(
                    (
                        root_envelope.mechanical_oracle.pass_fail_result is True,
                        root_envelope.proposal_verdict
                        is ProposalVerdict.VALID_PROPOSAL,
                        root_envelope.episode_outcome
                        is EpisodeOutcome.VERIFIED_SUCCESS,
                        root_envelope.declared_ttl_state is TTLState.FRESH,
                        all(
                            receipt.verification_result
                            for receipt in root_envelope.observed_receipts
                        ),
                    )
                )
                seed_evidence = {
                    "candidate_artifact": root_evaluation.artifact.as_dict(),
                    "fixture_capabilities_match": seed_contract_matches,
                    "fixture_profile_match": root_profile_matches,
                    "gate_passed": root_evaluation.gate_passed,
                    "gate_reasons": list(root_evaluation.gate_reasons),
                    "scenario_digest": root_scenario.scenario_digest,
                }
                if not all(
                    (
                        root_evaluation.gate_passed,
                        seed_contract_matches,
                        root_profile_matches,
                    )
                ):
                    self._fail_role(
                        seed_execution,
                        terminal_code="seed_mechanical_failure",
                        terminal_evidence=seed_evidence,
                    )
                    raise Stage1RunIncomplete(
                        "Seed failed trusted fixture or mechanical validation."
                    )
                self._complete_role(seed_execution, terminal_evidence=seed_evidence)

                mutation_request = {
                    "mode": "mock_only",
                    "role": "mutator",
                    "seed": seed.model_dump(mode="json"),
                    "seed_digest": canonical_sha256(seed.model_dump(mode="json")),
                    "variant_contexts": [
                        {
                            "position": variant_index,
                            "scenario": BlindedScenarioInput(
                                pack_name=pack_name,
                                ttl=self._ttl(expired=bool(profile["expired"])),
                            ).model_dump(mode="json"),
                        }
                        for variant_index, profile in enumerate(VARIANT_PROFILES)
                    ],
                }
                mutation_execution = self._perform_role(
                    role="mutator",
                    pack_name=pack_name,
                    root_index=root_index,
                    root_reference=root_reference,
                    request_payload=mutation_request,
                    execute=lambda p=pack_name, index=root_index, s=seed, request=mutation_request: (
                        self._executor.mutate(p, index, s, request)
                    ),
                    parse=parse_variant_batch,
                )
                batch = mutation_execution.parsed
                assert isinstance(batch, VariantBatch)
                variant_evaluations: list[CandidateEvaluation] = []
                for variant_index, variant in enumerate(batch.variants):
                    candidate_token = _deterministic_id(
                        "candidate",
                        self.run_id,
                        pack_name,
                        root_index,
                        variant_index,
                        length=32,
                    )
                    profile = VARIANT_PROFILES[variant_index]
                    envelope, trusted_scenario = self._make_envelope(
                        pack_name=pack_name,
                        root_index=root_index,
                        candidate_token=candidate_token,
                        raw_candidate=variant,
                        parent_id=root_envelope.root_id,
                        expired=bool(profile["expired"]),
                        effect_observed=bool(profile["effect_observed"]),
                    )
                    evaluation = self._evaluate_candidate(
                        envelope=envelope,
                        trusted_scenario=trusted_scenario,
                        candidate_token=candidate_token,
                        pack_artifact_digests=artifact_digests[pack_name],
                        pack_semantic_signatures=semantic_signatures[pack_name],
                        trajectory_effect_ids=set(),
                    )
                    candidates.append(evaluation)
                    variant_evaluations.append(evaluation)
                mutation_evidence = {
                    "candidate_artifacts": [
                        item.artifact.as_dict() for item in variant_evaluations
                    ],
                    "gate_passed": all(
                        item.gate_passed for item in variant_evaluations
                    ),
                    "gate_reasons": {
                        item.candidate_token: list(item.gate_reasons)
                        for item in variant_evaluations
                    },
                    "scenario_digests": [
                        item.trusted_scenario.scenario_digest
                        for item in variant_evaluations
                    ],
                    "fixture_profile_matches": [
                        all(
                            (
                                item.envelope.mechanical_oracle.pass_fail_result
                                is profile["oracle_passed"],
                                item.envelope.proposal_verdict.value
                                == profile["proposal_verdict"],
                                item.envelope.episode_outcome.value
                                == profile["episode_outcome"],
                                item.envelope.declared_ttl_state
                                is (
                                    TTLState.EXPIRED
                                    if profile["expired"]
                                    else TTLState.FRESH
                                ),
                                all(
                                    receipt.verification_result
                                    is profile["effect_observed"]
                                    for receipt in item.envelope.observed_receipts
                                ),
                            )
                        )
                        for item, profile in zip(
                            variant_evaluations, VARIANT_PROFILES, strict=True
                        )
                    ],
                }
                if not mutation_evidence["gate_passed"] or not all(
                    mutation_evidence["fixture_profile_matches"]
                ):
                    self._fail_role(
                        mutation_execution,
                        terminal_code="mutation_mechanical_failure",
                        terminal_evidence=mutation_evidence,
                    )
                    raise Stage1RunIncomplete(
                        "Mutation batch failed downstream mechanical validation."
                    )
                self._complete_role(
                    mutation_execution, terminal_evidence=mutation_evidence
                )

                review_input = self._review_projection(variant_evaluations)
                critic_execution = self._perform_role(
                    role="critic",
                    pack_name=pack_name,
                    root_index=root_index,
                    root_reference=root_reference,
                    request_payload=review_input,
                    execute=lambda p=pack_name, index=root_index, review=review_input: (
                        self._executor.critic(p, index, review)
                    ),
                    parse=parse_critic_batch,
                )
                critic = critic_execution.parsed
                assert isinstance(critic, CriticVerdictBatch)
                expected_tokens = [item.candidate_token for item in variant_evaluations]
                mechanic_by_token = {
                    item.candidate_token: item.envelope.proposal_verdict
                    for item in variant_evaluations
                }
                try:
                    critic.assert_expected_tokens(expected_tokens)
                except ValueError as exc:
                    self._fail_role(
                        critic_execution,
                        terminal_code="critic_contract_failure",
                        terminal_evidence={"expected_tokens": expected_tokens},
                    )
                    raise Stage1RunIncomplete(
                        "Critic did not return the exact blinded token set."
                    ) from exc
                disagreements = [
                    verdict
                    for verdict in critic.verdicts
                    if verdict.advisory_verdict
                    != mechanic_by_token[verdict.candidate_token]
                ]
                if not disagreements:
                    self._fail_role(
                        critic_execution,
                        terminal_code="missing_disagreement",
                        terminal_evidence={
                            "critic_digest": canonical_sha256(critic),
                            "expected_tokens": expected_tokens,
                        },
                    )
                    raise Stage1RunIncomplete(
                        "Adjudicator cannot be budgeted without measured disagreement."
                    )
                disagreement_count += len(disagreements)
                critic_evidence = {
                    "critic_digest": canonical_sha256(critic),
                    "disagreement_tokens": [
                        verdict.candidate_token for verdict in disagreements
                    ],
                    "mechanical_proposals": {
                        token: verdict.value
                        for token, verdict in mechanic_by_token.items()
                    },
                    "pack_name": pack_name,
                    "review_input_digest": canonical_sha256(review_input),
                }
                self._complete_role(critic_execution, terminal_evidence=critic_evidence)
                adjudication_input = self._review_projection(
                    variant_evaluations, critic_disagreements=disagreements
                )
                adjudication_execution = self._perform_role(
                    role="adjudicator",
                    pack_name=pack_name,
                    root_index=root_index,
                    root_reference=root_reference,
                    request_payload=adjudication_input,
                    execute=lambda p=pack_name, index=root_index, review=adjudication_input: (
                        self._executor.adjudicate(p, index, review)
                    ),
                    parse=parse_adjudication_batch,
                )
                adjudication = adjudication_execution.parsed
                assert isinstance(adjudication, AdjudicationVerdictBatch)
                disagreement_tokens = [
                    verdict.candidate_token for verdict in disagreements
                ]
                try:
                    adjudication.assert_expected_tokens(disagreement_tokens)
                except ValueError as exc:
                    self._fail_role(
                        adjudication_execution,
                        terminal_code="adjudicator_contract_failure",
                        terminal_evidence={"disagreement_tokens": disagreement_tokens},
                    )
                    raise Stage1RunIncomplete(
                        "Adjudicator did not return only measured disagreement tokens."
                    ) from exc
                self._complete_role(
                    adjudication_execution,
                    terminal_evidence={
                        "adjudication_digest": canonical_sha256(adjudication),
                        "disagreement_tokens": disagreement_tokens,
                        "mechanical_proposals": {
                            token: mechanic_by_token[token].value
                            for token in disagreement_tokens
                        },
                        "pack_name": pack_name,
                        "review_input_digest": canonical_sha256(adjudication_input),
                    },
                )

        if any(not item.gate_passed for item in candidates):
            raise Stage1RunIncomplete("One or more candidate mechanical gates failed.")
        report = self._canonical_report(
            candidates=candidates,
            disagreement_count=disagreement_count,
            run_contract_artifact=run_contract_artifact,
        )
        exact_topology = all(
            (
                report["ledger"]["total_attempts"] == 120,
                report["ledger"]["role_counts"] == {role: 30 for role in ROLE_ORDER},
                report["ledger"]["status_counts"]["completed"] == 120,
                report["ledger"]["status_counts"]["failed"] == 0,
                report["ledger"]["status_counts"]["indeterminate"] == 0,
                report["ledger"]["status_counts"]["started"] == 0,
                report["counts"]["roots"] == 30,
                report["counts"]["variants"] == 120,
                report["counts"]["candidate_artifacts"] == 150,
                report["counts"]["oracle_evaluations"] == 150,
                report["counts"]["adjudications"] == 30,
                report["counts"]["critic_disagreements"] == 30,
                report["counts"]["provider_transport_calls"] == 0,
                report["counts"]["executor_transport_calls_observed"] == 0,
                report["counts"]["training_writes"] == 0,
                report["pack_candidate_counts"] == {pack: 25 for pack in PACK_ORDER},
                report["outcome_counts"]
                == {"verified_failure": 90, "verified_success": 60},
                report["proposal_counts"]
                == {"invalid_proposal": 60, "valid_proposal": 90},
                report["ttl_counts"] == {"expired": 30, "fresh": 120},
                report["oracle_counts"] == {"failed": 30, "passed": 120},
                report["effect_observation_counts"]
                == {"not_observed": 30, "observed": 120},
                sum(report["review_feedback"]["critic_confusion_matrix"].values())
                == 120,
                sum(report["review_feedback"]["adjudicator_resolution_counts"].values())
                == 30,
            )
        )
        if not exact_topology:
            raise Stage1RunIncomplete("Stage 1 attempt topology is incomplete.")
        report_artifact = self.artifacts.write_content_addressed(
            ("runs", self.run_id, "reports"), "stage1-report", report
        )
        return Stage1RunResult(report=report, report_artifact=report_artifact)


# Compatibility name for callers that referenced the rejected mock harness.
Stage1MockHarness = Stage1SafetyHarness
