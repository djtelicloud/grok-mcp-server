import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from evals.campaigns.gemma_needle_2000_v1.schemas import (
    EpisodeOutcome,
    GemmaPlanStatePack,
    ToolSelectionPack,
    TTLState,
)
from evals.campaigns.gemma_needle_2000_v1.validators import MechanicalValidators
from evals.campaigns.gemma_needle_2000_v1.mechanical_mutators import MechanicalMutators
from evals.campaigns.gemma_needle_2000_v1.provider_adapters import ProviderAdapter, RunMode

def get_valid_base_fixture() -> dict:
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)
    
    return {
        "pack_name": "tool_selection",
        "output_contract_name": "basic_tool",
        "output_contract_version": "v1",
        "output_contract_digest": "sha256_mock_contract",
        "tool_catalog_digest": "sha256_mock_catalog",
        "schema_digest": "sha256_mock_schema",
        "prompt_template_digest": "sha256_mock_prompt",
        "generator_config_digest": "sha256_mock_gen_config",
        "objective": "test objective",
        "observations": [{"type": "env", "data": "started"}],
        "capabilities": ["fs_read"],
        "forbidden_effects": ["fs_write"],
        "issued_at": now,
        "expires_at": future,
        "declared_ttl_state": "fresh",
        "stable_effect_id": "effect_123",
        "result": {"type": "action", "tool_name": "test", "tool_arguments": {}},
        "mechanical_oracle": {
            "name": "test_oracle",
            "version": "1.0",
            "code_digest": "hash",
            "declared_inputs": [],
            "deterministic_parameters": {},
            "execution_receipt": {
                "receipt_spec_id": "oracle_exec",
                "issuer_identity": "test",
                "verifier_identity": "test",
                "effect_id": "effect_123",
                "observation_timestamp": now,
                "verification_result": True,
                "content_digest": "a"*32
            },
            "pass_fail_result": True,
            "output_digest": "hash"
        },
        "proposal_verdict": "valid_proposal",
        "episode_outcome": "verified_success",
        "required_receipt_specs": ["receipt_1"],
        "observed_receipts": [
            {
                "receipt_spec_id": "receipt_1",
                "issuer_identity": "sys",
                "verifier_identity": "sys",
                "effect_id": "effect_123",
                "observation_timestamp": now,
                "verification_result": True,
                "content_digest": "b"*32
            }
        ],
        "root_id": "root_1",
        "leakage_group": "group_1",
        "provenance": {"generator": "grok", "run": "run_1"}
    }

def test_discriminated_pack_contracts():
    data = get_valid_base_fixture()
    # Should work for ToolSelectionPack
    env = ToolSelectionPack(**data)
    assert env.pack_name == "tool_selection"
    
    # Should fail if passed to GemmaPlanStatePack
    with pytest.raises(ValidationError):
        GemmaPlanStatePack(**data)
        
    data["pack_name"] = "gemma_plan_state"
    data["long_chain_transitions"] = 10
    env2 = GemmaPlanStatePack(**data)
    assert env2.pack_name == "gemma_plan_state"

def test_missing_required_field():
    data = get_valid_base_fixture()
    del data["objective"]
    with pytest.raises(ValidationError):
        ToolSelectionPack(**data)

def test_unknown_extra_field():
    data = get_valid_base_fixture()
    data["extra_malicious_field"] = "bad"
    with pytest.raises(ValidationError):
        ToolSelectionPack(**data)

def test_hidden_cot_rejection():
    data = get_valid_base_fixture()
    data["decision_summary"] = "This is my chain_of_thought."
    with pytest.raises(ValidationError, match="Hidden chain-of-thought is forbidden"):
        ToolSelectionPack(**data)

def test_secret_pii_unsafe_path():
    data = get_valid_base_fixture()
    env = ToolSelectionPack(**data)
    
    # Legit vocabulary (should pass)
    env.objective = "Parse the user password and security token."
    assert MechanicalValidators.scan_unsafe_credentials(env)
    
    # Real credentials (should fail)
    env.objective = "My secret is AIzaSyB_mockKey123456789012345678901234"
    assert not MechanicalValidators.scan_unsafe_credentials(env)

def test_ttl_validation_timezone_aware():
    data = get_valid_base_fixture()
    # Strip timezone
    data["issued_at"] = data["issued_at"].replace(tzinfo=None)
    with pytest.raises(ValidationError, match="Timestamps must be timezone-aware"):
        ToolSelectionPack(**data)

def test_evaluate_episode_outcome():
    data = get_valid_base_fixture()
    env = ToolSelectionPack(**data)
    now = datetime.now(timezone.utc)
    
    # Valid setup is VERIFIED_SUCCESS
    assert MechanicalValidators.evaluate_episode_outcome(env, now) == EpisodeOutcome.VERIFIED_SUCCESS
    
    # Missing required receipt makes it VERIFIED_FAILURE
    env.observed_receipts = []
    assert MechanicalValidators.evaluate_episode_outcome(env, now) == EpisodeOutcome.VERIFIED_FAILURE

def test_provider_adapter_strict_validation(tmp_path):
    adapter = ProviderAdapter(
        "test",
        "test",
        "test",
        "test",
        RunMode.MOCK,
        cache_root=tmp_path / "provider-cache",
    )
    
    # Reject empty
    assert not adapter.validate_response({"content": ""})
    # Reject promise-only
    assert not adapter.validate_response({"content": "I'll do that."})
    # Reject promise-preface even when it includes a JSON artifact
    assert not adapter.validate_response(
        {"content": "I'll do that.\n{ \"test\": \"valid\" }"}
    )
    # Accept exactly one pure JSON artifact
    assert adapter.validate_response({"content": "{ \"test\": \"valid\" }"})
    # Reject malformed
    assert not adapter.validate_response(
        {"content": "I'll do that.\n{ \"test\": \"valid\" "}
    )

def test_mechanical_mutator_lineage():
    data = get_valid_base_fixture()
    env = ToolSelectionPack(**data)
    
    mutated = MechanicalMutators.shift_ttl_boundary(env, TTLState.REVOKED)
    assert mutated.parent_id == env.root_id
    assert mutated.root_id != env.root_id
    assert mutated.declared_ttl_state == TTLState.REVOKED
    assert mutated.provenance["mutation_type"] == "ttl_shift_revoked"
    assert mutated.immutable_artifact_digest != env.immutable_artifact_digest
    assert mutated.semantic_signature != env.semantic_signature
    assert mutated.episode_outcome == EpisodeOutcome.VERIFIED_FAILURE
