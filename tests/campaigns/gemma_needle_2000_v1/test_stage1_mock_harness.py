import pytest
from datetime import datetime, timezone, timedelta
import uuid

from evals.campaigns.gemma_needle_2000_v1.stage1_harness import Stage1MockHarness, CeilingExceededError
from evals.campaigns.gemma_needle_2000_v1.role_schemas import SeedCandidate, VariantBatch
from evals.campaigns.gemma_needle_2000_v1.schemas import ProposalVerdict, EpisodeOutcome
from pydantic import ValidationError

@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "stage1" / "attempts.db"

def test_hard_ceiling_enforced(ledger_path):
    harness = Stage1MockHarness(ledger_path)
    # Simulate 120 attempts
    for _ in range(120):
        harness.ledger.log_started(role="seed_author")
    
    with pytest.raises(CeilingExceededError):
        harness.mock_seed_generation()

def test_indeterminate_attempts_count(ledger_path):
    harness = Stage1MockHarness(ledger_path)
    work_item_id = harness.ledger.log_started(role="seed_author")
    # Do not log completed or failed
    
    assert harness.ledger.get_total_attempts() == 1
    assert work_item_id in harness.ledger.get_indeterminate_attempts()

def test_caller_supplied_authority_fields_rejected():
    # Model should not be able to generate ID, TTL, receipts
    with pytest.raises(ValidationError):
        SeedCandidate(
            pack_name="tool_selection",
            objective="test",
            observations=[],
            capabilities=[],
            forbidden_effects=[],
            stable_effect_id=str(uuid.uuid4()),
            result={"type": "action", "tool_name": "mock", "tool_arguments": {}},
            # These fields are extra and forbidden by extra='forbid'
            proposal_verdict=ProposalVerdict.VALID_PROPOSAL, 
            episode_outcome=EpisodeOutcome.VERIFIED_SUCCESS
        )

def test_partial_batches_rejected():
    # A batch must have exactly 4 variants
    with pytest.raises(ValidationError):
        VariantBatch(
            root_reference=str(uuid.uuid4()),
            variants=[
                {
                    "variant_key": "v0",
                    "objective": "test",
                    "observations": [],
                    "stable_effect_id": str(uuid.uuid4()),
                    "result": {"type": "action", "tool_name": "mock", "tool_arguments": {}}
                }
            ] # Only 1 instead of 4
        )

def test_adjudication_budget_accounting(ledger_path):
    harness = Stage1MockHarness(ledger_path)
    # Seed, mutate takes budget
    harness.mock_seed_generation()
    assert harness.ledger.get_total_attempts() == 1
    
    # Exceed budget
    for _ in range(119):
        harness.ledger.log_started(role="filler")
        
    with pytest.raises(CeilingExceededError):
        harness.mock_seed_generation()

def test_fixed_clock_ttl_behavior(ledger_path):
    harness = Stage1MockHarness(ledger_path)
    # Test wall-clock
    harness.run_lease_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(RuntimeError, match="Run lease deadline exceeded"):
        harness.mock_seed_generation()
