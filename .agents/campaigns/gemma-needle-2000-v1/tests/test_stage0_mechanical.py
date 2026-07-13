import pytest
from datetime import datetime
import json
from pydantic import ValidationError
import sys
import os

# Add parent dir to path for tests to run locally
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from schemas import BaseRootEnvelope, Verdict, TTLState, PackDiscriminator
from validators import MechanicalValidators
from mechanical_mutators import MechanicalMutators
from provider_adapters import ProviderAdapter, RunMode

def get_valid_base_fixture() -> dict:
    return {
        "pack_name": "tool_selection",
        "output_contract_name": "basic_tool",
        "output_contract_version": "v1",
        "output_contract_digest": "sha256_mock_contract",
        "tool_catalog_digest": "sha256_mock_catalog",
        "schema_digest": "sha256_mock_schema",
        "objective": "test objective",
        "observations": [{"type": "env", "data": "started"}],
        "capabilities": ["fs_read"],
        "forbidden_effects": ["fs_write"],
        "issued_at": datetime.now(),
        "expires_at": datetime.now(),
        "ttl_state": "fresh",
        "structural_invalidators": [],
        "stable_effect_id": "effect_123",
        "result": {"type": "action", "content": "mock"},
        "mechanical_oracle_spec": "mock_oracle",
        "verdict": "verified_success",
        "required_receipts": ["receipt_1"],
        "root_id": "root_1",
        "parent_id": None,
        "ancestor_ids": [],
        "leakage_group": "group_1",
        "provenance": {"generator": "grok", "run": "run_1"},
        "decision_summary": "mock decision",
        "plan_state": "mock plan"
    }

def test_valid_root_per_pack():
    for pack in PackDiscriminator:
        data = get_valid_base_fixture()
        data["pack_name"] = pack.value
        env = BaseRootEnvelope(**data)
        assert env.pack_name == pack

def test_missing_required_field():
    data = get_valid_base_fixture()
    del data["objective"]
    with pytest.raises(ValidationError):
        BaseRootEnvelope(**data)

def test_unknown_extra_field():
    data = get_valid_base_fixture()
    data["extra_malicious_field"] = "bad"
    with pytest.raises(ValidationError):
        BaseRootEnvelope(**data)

def test_hidden_cot_rejection():
    data = get_valid_base_fixture()
    data["decision_summary"] = "This is my chain_of_thought."
    with pytest.raises(ValidationError, match="Hidden chain-of-thought is forbidden"):
        BaseRootEnvelope(**data)

def test_secret_pii_unsafe_path():
    data = get_valid_base_fixture()
    env = BaseRootEnvelope(**data)
    
    # Legit vocabulary (should pass)
    env.objective = "Parse the user password and security token."
    assert MechanicalValidators.scan_unsafe_credentials(env) == True
    
    # Real credentials (should fail)
    env.objective = "My secret is AIzaSyB_mockKey123456789012345678901234"
    assert MechanicalValidators.scan_unsafe_credentials(env) == False

def test_ttl_and_receipt_validators():
    data = get_valid_base_fixture()
    env = BaseRootEnvelope(**data)
    
    # Fresh TTL with Action passes
    assert MechanicalValidators.verify_ttl_and_receipts(env) == True
    
    # Expired TTL with Action fails
    mutated_expired = MechanicalMutators.shift_ttl_boundary(env, TTLState.EXPIRED)
    assert MechanicalValidators.verify_ttl_and_receipts(mutated_expired) == False
    
    # Expired TTL with Request Verification passes
    mutated_expired.result = {"type": "request_verification"}
    assert MechanicalValidators.verify_ttl_and_receipts(mutated_expired) == True

def test_duplicate_semantic_effect():
    data = get_valid_base_fixture()
    env = BaseRootEnvelope(**data)
    
    # First time effect_123 is executed, trajectory doesn't have it yet
    trajectory = []
    assert MechanicalValidators.verify_no_duplicate_semantic_effect(trajectory, env) == True
    
    # Second time effect_123 is proposed in an action, it fails
    trajectory = ["effect_123"]
    assert MechanicalValidators.verify_no_duplicate_semantic_effect(trajectory, env) == False

def test_oracle_consistency():
    data = get_valid_base_fixture()
    env = BaseRootEnvelope(**data)
    
    # Has oracle and is verified_success
    assert MechanicalValidators.verify_oracle_consistency(env) == True
    
    # Is verified_success but missing oracle
    env.mechanical_oracle_spec = ""
    assert MechanicalValidators.verify_oracle_consistency(env) == False

def test_unverified_preservation():
    assert MechanicalValidators.enforce_unverified_preservation(Verdict.UNVERIFIED, Verdict.VERIFIED_SUCCESS) == True
    assert MechanicalValidators.enforce_unverified_preservation(Verdict.UNVERIFIED, Verdict.VERIFIED_FAILURE) == False

def test_provider_adapter_promise_rejection():
    adapter = ProviderAdapter("test", "test", "test", "test", RunMode.MOCK)
    
    # Empty
    assert adapter.validate_response({"content": ""}) == False
    # Promise only
    assert adapter.validate_response({"content": "I'll do that right away."}) == False
    # Promise preface + valid artifact
    assert adapter.validate_response({"content": "I'll do that right away.\n{ 'schema': 'valid' }"}) == True
    # Malformed artifact missing content
    assert adapter.validate_response({"malformed": True}) == False

def test_mechanical_mutator_lineage():
    data = get_valid_base_fixture()
    env = BaseRootEnvelope(**data)
    
    mutated = MechanicalMutators.shift_ttl_boundary(env, TTLState.REVOKED)
    assert mutated.parent_id == env.root_id
    assert mutated.root_id != env.root_id
    assert mutated.ttl_state == TTLState.REVOKED
    assert mutated.provenance["mutation_type"] == "ttl_shift_revoked"
    assert mutated.provenance["mutation_source"] == env.root_id
