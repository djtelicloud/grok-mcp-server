from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from evals.campaigns.gemma_needle_2000_v1.mechanical_mutators import (
    MechanicalMutators,
)
from evals.campaigns.gemma_needle_2000_v1.role_schemas import (
    AdjudicationVerdictBatch,
    BlindedAdjudicationInput,
    BlindedReviewInput,
    CriticVerdictBatch,
    FrozenScenarioInput,
    parse_adjudication_batch,
    parse_critic_batch,
    parse_seed_candidate,
    parse_variant_batch,
)
from evals.campaigns.gemma_needle_2000_v1.schemas import (
    EpisodeOutcome,
    ProposalVerdict,
    TTLFacts,
    TTLState,
    ToolSelectionPack,
    canonical_sha256,
)
from evals.campaigns.gemma_needle_2000_v1.stage1_oracles import (
    attest_effect_receipt,
    default_oracle_registry,
    verify_effect_receipt,
)
from evals.campaigns.gemma_needle_2000_v1.validators import MechanicalValidators


FIXED_TIME = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def seed_payload() -> dict:
    return {
        "objective": "Select the bounded read-only inspection tool.",
        "observations": [{"type": "workspace", "status": "ready"}],
        "capabilities": ["workspace.read"],
        "forbidden_effects": ["workspace mutation"],
        "result": {
            "type": "action",
            "tool_name": "workspace.read",
            "tool_arguments": {"path": "src"},
        },
        "decision_summary": "Use the available read-only capability.",
    }


def variant_payload() -> dict:
    return {
        "variants": [
            {
                "objective": f"Inspect bounded target {index}.",
                "observations": [{"ordinal": index}],
                "result": {
                    "type": "action",
                    "tool_name": "workspace.read",
                    "tool_arguments": {"path": f"src/target_{index}.py"},
                },
            }
            for index in range(4)
        ]
    }


def candidate_tokens() -> list[str]:
    return [f"candidate-{index:016x}" for index in range(4)]


def verdict_payload() -> dict:
    return {
        "verdicts": [
            {
                "candidate_token": token,
                "advisory_verdict": "valid_proposal",
                "reason_code": "oracle_match",
                "summary": "The blinded proposal matches the supplied contract.",
            }
            for token in candidate_tokens()
        ]
    }


def valid_envelope() -> ToolSelectionPack:
    oracle_output = {"passed": True, "checks": ["edited", "tested"]}
    oracle_output_digest = canonical_sha256(oracle_output)
    receipt_content = {"edited": True, "tested": True}
    receipt_content_digest = canonical_sha256(receipt_content)
    envelope = ToolSelectionPack(
        pack_name="tool_selection",
        output_contract_name="tool_action",
        output_contract_version="1.0",
        output_contract_digest=SHA_A,
        tool_catalog_digest=SHA_B,
        schema_digest=SHA_C,
        prompt_template_digest=SHA_A,
        generator_config_digest=SHA_B,
        objective="Read the requested workspace target.",
        observations=[{"type": "workspace", "status": "ready"}],
        capabilities=["workspace.read"],
        forbidden_effects=["workspace mutation"],
        issued_at=FIXED_TIME - timedelta(hours=2),
        expires_at=FIXED_TIME + timedelta(hours=2),
        evaluated_at=FIXED_TIME,
        declared_ttl_state=TTLState.FRESH,
        stable_effect_id="effect-placeholder",
        result={
            "type": "action",
            "tool_name": "workspace.read",
            "tool_arguments": {"path": "src"},
        },
        mechanical_oracle={
            "name": "workspace_verifier",
            "version": "1.0",
            "code_digest": SHA_A,
            "declared_inputs": ["stable_effect_id", "result"],
            "deterministic_parameters": {"require": ["edited", "tested"]},
            "declared_input_digest": SHA_B,
            "oracle_output": oracle_output,
            "execution_receipt": {
                "receipt_spec_id": "oracle_execution",
                "receipt_spec_version": "1.0",
                "issuer_identity": "mechanical_oracle",
                "verifier_identity": "campaign_harness",
                "verifier_code_digest": SHA_C,
                "effect_id": "effect-placeholder",
                "observation_timestamp": FIXED_TIME,
                "verification_result": True,
                "content_digest": oracle_output_digest,
                "declared_input_digest": SHA_B,
                "oracle_name": "workspace_verifier",
                "oracle_version": "1.0",
                "oracle_code_digest": SHA_A,
                "observed_content": oracle_output,
            },
            "pass_fail_result": True,
            "output_digest": oracle_output_digest,
        },
        proposal_verdict=ProposalVerdict.UNVERIFIED,
        episode_outcome=EpisodeOutcome.UNVERIFIED,
        required_receipt_specs=["workspace_effect"],
        observed_receipts=[
            {
                "receipt_spec_id": "workspace_effect",
                "receipt_spec_version": "1.0",
                "issuer_identity": "workspace_executor",
                "verifier_identity": "campaign_harness",
                "verifier_code_digest": SHA_C,
                "effect_id": "effect-placeholder",
                "observation_timestamp": FIXED_TIME,
                "verification_result": True,
                "content_digest": receipt_content_digest,
                "declared_input_digest": SHA_B,
                "observed_content": receipt_content,
            }
        ],
        root_id="root-0001",
        leakage_group="leakage-0001",
        provenance={"generator": "mock", "run": "stage1"},
    )
    envelope.stable_effect_id = envelope.compute_stable_effect_id()
    registry = default_oracle_registry()
    oracle = registry.attest(
        envelope,
        name="expected_result_digest",
        version="1.0",
        deterministic_parameters={
            "expected_result_digest": canonical_sha256(
                envelope.result.model_dump(mode="json")
            )
        },
    )
    assert oracle.execution_receipt is not None
    oracle.execution_receipt.effect_id = envelope.stable_effect_id
    oracle.execution_receipt.declared_input_digest = oracle.declared_input_digest
    envelope.required_receipt_specs = ["effect_observation"]
    envelope.observed_receipts = [
        attest_effect_receipt(envelope, trusted_scenario(envelope))
    ]
    envelope.proposal_verdict = MechanicalValidators.derive_proposal_verdict(envelope)
    envelope.episode_outcome = MechanicalValidators.derive_episode_outcome(envelope)
    envelope.finalize_integrity()
    return envelope


def trusted_scenario(
    envelope: ToolSelectionPack, *, expected_effect_observed: bool = True
) -> FrozenScenarioInput:
    return FrozenScenarioInput.mint(
        pack_name=envelope.pack_name,
        root_reference=envelope.root_id,
        ttl=envelope.ttl_facts(),
        expected_result_digest=canonical_sha256(
            envelope.result.model_dump(mode="json")
        ),
        expected_effect_observed=expected_effect_observed,
        output_contract_digest=envelope.output_contract_digest,
        tool_catalog_digest=envelope.tool_catalog_digest,
        schema_digest=envelope.schema_digest,
        prompt_template_digest=envelope.prompt_template_digest,
        generator_config_digest=envelope.generator_config_digest,
    )


@pytest.mark.parametrize(
    "nested",
    [
        {"stable_effect_id": "provider-controlled"},
        {"wrapper": {"episode_outcome": "verified_success"}},
        {"wrapper": [{"content_digest": SHA_A}]},
        {"wrapper": {"episodeOutcome": "verified_success"}},
        {"wrapper": {"receipt spec id": "provider-controlled"}},
        {"wrapper": {"Verification-Result": True}},
        {"wrapper": {"passFailResult": True}},
        {"wrapper": {"expectedResultDigest": SHA_A}},
        {"wrapper": {"expected effect observed": True}},
    ],
)
def test_raw_boundary_rejects_nested_authority(nested: dict) -> None:
    payload = seed_payload()
    payload["observations"] = [nested]
    with pytest.raises(ValueError, match="authority field"):
        parse_seed_candidate(payload)


def test_unknown_pack_and_frozen_context_digest_rejected() -> None:
    ttl = TTLFacts(
        issued_at=FIXED_TIME - timedelta(hours=1),
        expires_at=FIXED_TIME + timedelta(hours=1),
        evaluated_at=FIXED_TIME,
        declared_ttl_state=TTLState.FRESH,
    )
    with pytest.raises(ValidationError):
        FrozenScenarioInput.mint(
            pack_name="unknown_pack",  # type: ignore[arg-type]
            root_reference="root-1",
            ttl=ttl,
            expected_result_digest=SHA_A,
            expected_effect_observed=True,
            output_contract_digest=SHA_A,
            tool_catalog_digest=SHA_B,
            schema_digest=SHA_C,
            prompt_template_digest=SHA_A,
            generator_config_digest=SHA_B,
        )
    context = FrozenScenarioInput.mint(
        pack_name="tool_selection",
        root_reference="root-1",
        ttl=ttl,
        expected_result_digest=SHA_A,
        expected_effect_observed=True,
        output_contract_digest=SHA_A,
        tool_catalog_digest=SHA_B,
        schema_digest=SHA_C,
        prompt_template_digest=SHA_A,
        generator_config_digest=SHA_B,
    )
    forged = context.model_dump()
    forged["scenario_digest"] = SHA_A
    with pytest.raises(ValidationError, match="scenario_digest"):
        FrozenScenarioInput.model_validate(forged)


def test_nested_result_extras_and_cot_fields_are_rejected() -> None:
    payload = seed_payload()
    payload["result"]["episode_outcome"] = "verified_success"
    with pytest.raises(ValueError):
        parse_seed_candidate(payload)

    payload = seed_payload()
    payload["observations"] = [{"scratchpad": "private reasoning"}]
    with pytest.raises(ValueError, match="hidden reasoning"):
        parse_seed_candidate(payload)

    payload = seed_payload()
    payload["decision_summary"] = "Here is my chain_of_thought"
    with pytest.raises(ValidationError, match="chain-of-thought"):
        parse_seed_candidate(payload)

    payload = seed_payload()
    payload["decision_summary"] = "Here is my chain of thought"
    with pytest.raises(ValidationError, match="chain-of-thought"):
        parse_seed_candidate(payload)


def test_variant_batch_is_exact_bounded_and_unique() -> None:
    assert len(parse_variant_batch(variant_payload()).variants) == 4
    too_short = variant_payload()
    too_short["variants"].pop()
    with pytest.raises(ValidationError):
        parse_variant_batch(too_short)
    duplicate = variant_payload()
    duplicate["variants"][3] = duplicate["variants"][0]
    with pytest.raises(ValidationError, match="unique variants"):
        parse_variant_batch(duplicate)


def test_text_and_collection_bounds_fail_closed() -> None:
    payload = seed_payload()
    payload["objective"] = "x" * 4_097
    with pytest.raises(ValueError):
        parse_seed_candidate(payload)
    payload = seed_payload()
    payload["capabilities"] = [f"cap-{index}" for index in range(33)]
    with pytest.raises(ValidationError):
        parse_seed_candidate(payload)


def test_mapping_payloads_obey_the_same_raw_byte_limit_as_json_text() -> None:
    payload = seed_payload()
    payload["observations"] = [{"blob": "x" * 262_000}]

    with pytest.raises(ValueError, match="byte limit"):
        parse_seed_candidate(payload)

    with pytest.raises(ValueError, match="byte limit"):
        parse_seed_candidate(json.dumps(payload))


def test_mapping_payloads_reject_non_json_values() -> None:
    payload = seed_payload()
    payload["observations"] = [{"value": {1, 2, 3}}]

    with pytest.raises(ValueError, match="deterministic JSON"):
        parse_seed_candidate(payload)


def test_json_role_payloads_reject_duplicate_object_keys() -> None:
    raw = json.dumps(seed_payload())
    duplicated = raw.replace('"objective":', '"objective":"shadowed","objective":', 1)

    with pytest.raises(ValueError, match="exactly one JSON value"):
        parse_seed_candidate(duplicated)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_role_payloads_reject_non_finite_numbers(non_finite: float) -> None:
    payload = seed_payload()
    payload["observations"] = [{"value": non_finite}]

    with pytest.raises(ValueError, match="deterministic JSON"):
        parse_seed_candidate(payload)
    with pytest.raises(ValueError, match="exactly one JSON value"):
        parse_seed_candidate(json.dumps(payload))


def test_review_batches_require_exact_expected_token_set() -> None:
    critic = parse_critic_batch(verdict_payload())
    assert isinstance(critic, CriticVerdictBatch)
    critic.assert_expected_tokens(candidate_tokens())
    wrong = [*candidate_tokens()]
    wrong[-1] = "candidate-ffffffffffffffff"
    with pytest.raises(ValueError, match="do not match"):
        critic.assert_expected_tokens(wrong)

    duplicate = verdict_payload()
    duplicate["verdicts"][3]["candidate_token"] = candidate_tokens()[0]
    with pytest.raises(ValidationError, match="unique candidate tokens"):
        parse_critic_batch(duplicate)

    empty = {"verdicts": []}
    with pytest.raises(ValidationError):
        parse_adjudication_batch(empty)
    assert isinstance(
        parse_adjudication_batch(verdict_payload()), AdjudicationVerdictBatch
    )

    one_disagreement = {"verdicts": [verdict_payload()["verdicts"][0]]}
    adjudication = parse_adjudication_batch(one_disagreement)
    adjudication.assert_expected_tokens([candidate_tokens()[0]])


def test_blinded_review_contains_candidate_content_but_no_authority() -> None:
    envelope = valid_envelope()
    scenario = trusted_scenario(envelope)
    candidates = []
    for token, candidate in zip(candidate_tokens(), variant_payload()["variants"]):
        candidates.append(
            {
                "candidate_token": token,
                "scenario": scenario.blinded_view(),
                "capabilities": envelope.capabilities,
                "forbidden_effects": envelope.forbidden_effects,
                **candidate,
            }
        )
    review = BlindedReviewInput(
        candidates=candidates,
        rubric=["Judge the proposed result against the visible scenario."],
    )

    assert review.candidate_tokens == candidate_tokens()
    serialized = review.model_dump(mode="json")
    forbidden = (
        "episode_outcome",
        "proposal_verdict",
        "mechanical_oracle",
        "observed_receipts",
        "immutable_artifact_digest",
        "semantic_signature",
        "provenance",
        "expected_result_digest",
        "expected_effect_observed",
        "scenario_digest",
        "root_reference",
        "output_contract_digest",
        "tool_catalog_digest",
        "schema_digest",
        "prompt_template_digest",
        "generator_config_digest",
    )
    serialized_text = json.dumps(serialized, sort_keys=True)
    for field in forbidden:
        assert f'"{field}"' not in serialized_text

    critic = parse_critic_batch(verdict_payload())
    disagreement = BlindedAdjudicationInput(
        review=review,
        disagreements=[critic.verdicts[0]],
    )
    assert len(disagreement.disagreements) == 1


@pytest.mark.parametrize(
    ("state", "expires_delta", "soft_delta", "revoked", "post", "invalid"),
    [
        (TTLState.FRESH, 1, None, [], [], []),
        (TTLState.SOFT_STALE, 1, -1, [], [], []),
        (TTLState.EXPIRED, 0, None, [], [], []),
        (TTLState.REVOKED, 1, None, ["revoked"], [], []),
        (TTLState.POST_TTL, 0, None, [], ["post TTL observation"], []),
        (
            TTLState.STRUCTURALLY_INVALID,
            1,
            None,
            [],
            [],
            ["invalid authority envelope"],
        ),
    ],
)
def test_frozen_ttl_derivation_is_single_valued(
    state: TTLState,
    expires_delta: int,
    soft_delta: int | None,
    revoked: list[str],
    post: list[str],
    invalid: list[str],
) -> None:
    facts = TTLFacts(
        issued_at=FIXED_TIME - timedelta(hours=2),
        expires_at=FIXED_TIME + timedelta(hours=expires_delta),
        evaluated_at=FIXED_TIME,
        soft_stale_at=(
            FIXED_TIME + timedelta(hours=soft_delta) if soft_delta is not None else None
        ),
        revocation_facts=revoked,
        post_ttl_facts=post,
        structural_invalidators=invalid,
        declared_ttl_state=state,
    )
    assert facts.derive_state() == state
    forged = facts.model_dump()
    forged["declared_ttl_state"] = TTLState.FRESH
    if state != TTLState.FRESH:
        with pytest.raises(ValidationError, match="does not match derived state"):
            TTLFacts.model_validate(forged)


def test_digest_forgery_and_duplicates_recompute_truth() -> None:
    envelope = valid_envelope()
    assert MechanicalValidators.verify_no_exact_duplicates(set(), envelope)
    assert MechanicalValidators.verify_no_semantic_duplicate(set(), envelope)
    assert not MechanicalValidators.verify_no_exact_duplicates(
        {envelope.immutable_artifact_digest or ""}, envelope
    )
    assert not MechanicalValidators.verify_no_semantic_duplicate(
        {envelope.semantic_signature or ""}, envelope
    )

    forged = envelope.model_copy(deep=True)
    forged.immutable_artifact_digest = SHA_A
    forged.semantic_signature = SHA_B
    assert not MechanicalValidators.verify_no_exact_duplicates(set(), forged)
    assert not MechanicalValidators.verify_no_semantic_duplicate(set(), forged)


def test_semantic_identity_ignores_harness_ids_timestamps_and_receipts() -> None:
    envelope = valid_envelope()
    projection = envelope.model_copy(deep=True)
    projection.root_id = "root-other"
    projection.parent_id = "root-parent"
    projection.leakage_group = "leakage-other"
    projection.stable_effect_id = "effect-other"
    projection.issued_at += timedelta(minutes=1)
    projection.expires_at += timedelta(minutes=1)
    assert projection.evaluated_at is not None
    projection.evaluated_at += timedelta(minutes=1)
    projection.observed_receipts = []
    projection.provenance = {"generator": "other", "run": "other"}
    assert (
        projection.compute_semantic_signature() == envelope.compute_semantic_signature()
    )
    assert (
        projection.compute_immutable_artifact_digest()
        != envelope.compute_immutable_artifact_digest()
    )


def test_oracle_receipt_and_authority_are_mechanically_derived() -> None:
    envelope = valid_envelope()
    assert MechanicalValidators.verify_oracle_contract(envelope)
    assert MechanicalValidators.verify_receipts_bound(envelope)
    assert MechanicalValidators.verify_declared_authority(envelope)
    accepted, reasons = MechanicalValidators.strict_gate(
        envelope,
        trusted_scenario=trusted_scenario(envelope),
        artifact_digests=set(),
        semantic_signatures=set(),
        trajectory_effect_ids=set(),
        executable_oracle_verifier=default_oracle_registry().verify,
        executable_receipt_verifier=verify_effect_receipt,
    )
    assert accepted
    assert reasons == ()

    forged_scenario = FrozenScenarioInput.mint(
        pack_name=envelope.pack_name,
        root_reference=envelope.root_id,
        ttl=envelope.ttl_facts(),
        expected_result_digest=SHA_C,
        expected_effect_observed=True,
        output_contract_digest=envelope.output_contract_digest,
        tool_catalog_digest=envelope.tool_catalog_digest,
        schema_digest=envelope.schema_digest,
        prompt_template_digest=envelope.prompt_template_digest,
        generator_config_digest=envelope.generator_config_digest,
    )
    accepted, reasons = MechanicalValidators.strict_gate(
        envelope,
        trusted_scenario=forged_scenario,
        artifact_digests=set(),
        semantic_signatures=set(),
        trajectory_effect_ids=set(),
        executable_oracle_verifier=default_oracle_registry().verify,
        executable_receipt_verifier=verify_effect_receipt,
    )
    assert not accepted
    assert "scenario_binding" in reasons

    fake_receipt = envelope.model_copy(deep=True)
    fake_content = dict(fake_receipt.observed_receipts[0].observed_content)
    fake_content["effect_observed"] = False
    fake_receipt.observed_receipts[0].observed_content = fake_content
    fake_receipt.observed_receipts[0].verification_result = False
    fake_receipt.observed_receipts[0].content_digest = canonical_sha256(fake_content)
    fake_receipt.episode_outcome = MechanicalValidators.derive_episode_outcome(
        fake_receipt
    )
    fake_receipt.finalize_integrity()
    accepted, reasons = MechanicalValidators.strict_gate(
        fake_receipt,
        trusted_scenario=trusted_scenario(envelope),
        artifact_digests=set(),
        semantic_signatures=set(),
        trajectory_effect_ids=set(),
        executable_oracle_verifier=default_oracle_registry().verify,
        executable_receipt_verifier=verify_effect_receipt,
    )
    assert not accepted
    assert "receipt_binding" in reasons

    verified_absence = envelope.model_copy(deep=True)
    absence_scenario = trusted_scenario(
        verified_absence, expected_effect_observed=False
    )
    verified_absence.observed_receipts = [
        attest_effect_receipt(verified_absence, absence_scenario)
    ]
    verified_absence.episode_outcome = MechanicalValidators.derive_episode_outcome(
        verified_absence
    )
    verified_absence.finalize_integrity()
    accepted, reasons = MechanicalValidators.strict_gate(
        verified_absence,
        trusted_scenario=absence_scenario,
        artifact_digests=set(),
        semantic_signatures=set(),
        trajectory_effect_ids=set(),
        executable_oracle_verifier=default_oracle_registry().verify,
        executable_receipt_verifier=verify_effect_receipt,
    )
    assert accepted
    assert reasons == ()
    assert verified_absence.episode_outcome == EpisodeOutcome.VERIFIED_FAILURE

    forged_receipt = envelope.model_copy(deep=True)
    forged_receipt.observed_receipts[0].observed_content = {"edited": False}
    assert not MechanicalValidators.verify_receipts_bound(forged_receipt)
    assert (
        MechanicalValidators.derive_episode_outcome(forged_receipt)
        == EpisodeOutcome.VERIFIED_FAILURE
    )

    forged_oracle = envelope.model_copy(deep=True)
    forged_oracle.mechanical_oracle.declared_input_digest = SHA_B
    assert not MechanicalValidators.verify_oracle_contract(forged_oracle)
    assert (
        MechanicalValidators.derive_proposal_verdict(forged_oracle)
        == ProposalVerdict.UNVERIFIED
    )

    forged_claim = envelope.model_copy(deep=True)
    forged_claim.episode_outcome = EpisodeOutcome.UNVERIFIED
    assert (
        MechanicalValidators.derive_episode_outcome(forged_claim)
        == EpisodeOutcome.VERIFIED_SUCCESS
    )
    assert not MechanicalValidators.verify_declared_authority(forged_claim)


def test_content_scan_ignores_harness_hashes_ids_and_receipts() -> None:
    envelope = valid_envelope()
    envelope.output_contract_digest = "1" * 64
    envelope.tool_catalog_digest = "2" * 64
    envelope.schema_digest = "3" * 64
    envelope.root_id = "root-1234567890"
    envelope.parent_id = "parent-1234567890"
    envelope.ancestor_ids = ["ancestor-1234567890"]
    envelope.leakage_group = "group-123e4567-e89b-12d3-a456-426614174000"
    envelope.provenance = {
        "generator": "mock-1234567890",
        "run": "123e4567-e89b-12d3-a456-426614174000",
    }
    envelope.observed_receipts[0].observed_content = {
        "opaque_harness_id": "1234567890",
        "trusted_contact_receipt": "receipt-owner@example.test",
    }

    assert MechanicalValidators.scan_unsafe_credentials(envelope)


def test_content_scan_allows_numeric_provider_ids_and_safe_vocabulary() -> None:
    envelope = valid_envelope()
    envelope.objective = "Parse password and token field names without their values."
    envelope.observations = [{"record_id": 1234567890}]
    envelope.result.tool_arguments["numeric_id"] = 1234567890  # type: ignore[union-attr]

    assert MechanicalValidators.scan_unsafe_credentials(envelope)


def test_provider_payload_scan_covers_reviewer_text_before_persistence() -> None:
    safe = verdict_payload()
    assert MechanicalValidators.scan_provider_payload(safe)

    unsafe = verdict_payload()
    unsafe["verdicts"][0]["summary"] = "Contact reviewer@example.test"
    assert not MechanicalValidators.scan_provider_payload(unsafe)


@pytest.mark.parametrize(
    ("location", "unsafe_value"),
    [
        ("objective", "xai-ABCDEFGHIJKLMNOPQRSTUVWX"),
        ("observation", "person@example.test"),
        ("tool_argument", "/Users/example/private/campaign.json"),
        ("decision_summary", "+1 212-555-0198"),
        ("plan_state", "Bearer ABCDEFGHIJKLMNOPQRSTUVWX"),
    ],
)
def test_content_scan_rejects_nested_provider_leaks(
    location: str, unsafe_value: str
) -> None:
    envelope = valid_envelope()
    if location == "objective":
        envelope.objective = unsafe_value
    elif location == "observation":
        envelope.observations = [{"nested": {"value": unsafe_value}}]
    elif location == "tool_argument":
        envelope.result.tool_arguments["path"] = unsafe_value  # type: ignore[union-attr]
    elif location == "decision_summary":
        envelope.decision_summary = unsafe_value
    else:
        envelope.plan_state = unsafe_value

    assert not MechanicalValidators.scan_unsafe_credentials(envelope)


def test_semantic_effect_id_is_recomputed_and_deduplicated() -> None:
    envelope = valid_envelope()
    expected = envelope.compute_stable_effect_id()
    assert envelope.stable_effect_id == expected
    assert MechanicalValidators.verify_no_duplicate_semantic_effect([], envelope)
    assert not MechanicalValidators.verify_no_duplicate_semantic_effect(
        [expected], envelope
    )
    forged = envelope.model_copy(deep=True)
    forged.stable_effect_id = "effect-provider-selected"
    assert not MechanicalValidators.verify_no_duplicate_semantic_effect([], forged)


@pytest.mark.parametrize("state", list(TTLState))
def test_ttl_mutator_changes_all_facts_before_final_digests(state: TTLState) -> None:
    envelope = valid_envelope()
    mutated = MechanicalMutators.shift_ttl_boundary(envelope, state)
    assert MechanicalValidators.derive_frozen_ttl_state(mutated) == state
    assert mutated.declared_ttl_state == state
    assert mutated.integrity_matches()
    if state != TTLState.FRESH:
        assert mutated.episode_outcome == EpisodeOutcome.VERIFIED_FAILURE
