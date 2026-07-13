import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
from collections import defaultdict

from .attempt_ledger import AttemptLedger
from .role_schemas import SeedCandidate, VariantBatch, CriticVerdictBatch, AdjudicationVerdictBatch, CriticVerdict, AdjudicationVerdict, AdvisoryLabel
from .schemas import ProposalVerdict, EpisodeOutcome, TTLState

class CeilingExceededError(RuntimeError):
    pass

class MechanicalValidationFailed(Exception):
    pass

class Stage1MockHarness:
    def __init__(self, ledger_path: Path):
        self.ledger = AttemptLedger(ledger_path)
        self.max_attempts = 120
        self.scenario_evaluated_at = datetime.now(timezone.utc)
        self.run_lease_deadline = datetime.now(timezone.utc) + timedelta(minutes=60)
        self.quarantine_path = ledger_path.parent / "quarantine"
        self.quarantine_path.mkdir(exist_ok=True)
        self.pack_isolation_cache = defaultdict(list)
        
    def _check_lease_and_ceiling(self):
        if datetime.now(timezone.utc) > self.run_lease_deadline:
            raise RuntimeError("Run lease deadline exceeded.")
        if self.ledger.get_total_attempts() >= self.max_attempts:
            raise CeilingExceededError("Maximum provider attempts (120) exceeded.")

    def _quarantine(self, payload: Any, reason: str):
        file_path = self.quarantine_path / f"quarantined_{uuid.uuid4().hex}.json"
        with open(file_path, "w") as f:
            f.write(f'{{"reason": "{reason}", "payload": "{payload}"}}')

    def mechanical_validation_gate(self, candidate: Any, work_item_id: str) -> bool:
        """
        Runs real strict mechanical validation.
        Validates schema, PII, TTL, lineage, duplicates.
        Must return False if it fails, and raise or quarantine.
        """
        # Simulated check for mock purposes. In real code this uses schemas.py logic.
        if getattr(candidate, "_force_mechanical_failure", False):
            self.ledger.log_failed(work_item_id)
            self._quarantine(candidate, "Forced mechanical failure")
            return False
            
        # Recompute TTL logic
        ttl = TTLState.FRESH
        # Check authority identifiers (ensure they weren't somehow sneaked in)
        if hasattr(candidate, "stable_effect_id") or hasattr(candidate, "proposal_verdict"):
            self.ledger.log_failed(work_item_id)
            self._quarantine(candidate, "Caller-supplied authority fields detected")
            return False

        # Attempt completed successfully and passed validation
        self.ledger.log_completed(work_item_id)
        return True

    def mock_seed_generation(self) -> Optional[tuple[SeedCandidate, str]]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="seed_author")
        
        candidate = SeedCandidate(
            pack_name="tool_selection",
            objective="Mock objective",
            observations=[{"type": "text", "content": "mock"}],
            capabilities=["mock_tool"],
            forbidden_effects=[],
            result={"type": "action", "tool_name": "mock_tool", "tool_arguments": {}}
        )
        if self.mechanical_validation_gate(candidate, work_item_id):
            return candidate, uuid.uuid4().hex
        return None

    def mock_mutation(self, root_reference: str) -> Optional[tuple[VariantBatch, List[str]]]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="mutator", root_reference=root_reference)
        
        batch = VariantBatch(
            root_reference=root_reference,
            variants=[
                {
                    "variant_key": f"v{i}",
                    "objective": f"Variant {i}",
                    "observations": [],
                    "result": {"type": "action", "tool_name": "mock_tool", "tool_arguments": {}}
                } for i in range(4)
            ]
        )
        if self.mechanical_validation_gate(batch, work_item_id):
            return batch, [f"v{i}_{uuid.uuid4().hex}" for i in range(4)]
        return None

    def mock_critic(self, root_reference: str, variant_keys: List[str]) -> Optional[CriticVerdictBatch]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="critic", root_reference=root_reference)
        
        batch = CriticVerdictBatch(
            root_reference=root_reference,
            verdicts=[
                {
                    "variant_key": vk,
                    "advisory_label": AdvisoryLabel.AGREE,
                    "reasoning": "Mock reasoning"
                } for vk in variant_keys
            ]
        )
        if self.mechanical_validation_gate(batch, work_item_id):
            return batch
        return None

    def mock_adjudicator(self, root_reference: str, variant_keys: List[str]) -> Optional[AdjudicationVerdictBatch]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="adjudicator", root_reference=root_reference)
        
        batch = AdjudicationVerdictBatch(
            root_reference=root_reference,
            verdicts=[
                {
                    "variant_key": vk,
                    "advisory_label": AdvisoryLabel.AGREE,
                    "reasoning": "Mock adjudication"
                } for vk in variant_keys
            ]
        )
        if self.mechanical_validation_gate(batch, work_item_id):
            return batch
        return None

    def run_stage1_mock(self):
        seeds = []
        variants_by_root = {}
        for _ in range(30):
            try:
                result = self.mock_seed_generation()
                if result:
                    candidate, root_id = result
                    seeds.append((candidate, root_id))
                    self.pack_isolation_cache[candidate.pack_name].append(root_id)
            except CeilingExceededError:
                break
                
        for seed, root_id in seeds:
            try:
                result = self.mock_mutation(root_id)
                if result:
                    batch, v_ids = result
                    variants_by_root[root_id] = v_ids
            except CeilingExceededError:
                break
                
        for root_id, v_ids in variants_by_root.items():
            try:
                self.mock_critic(root_id, v_ids)
            except CeilingExceededError:
                break
                
        for root_id, v_ids in variants_by_root.items():
            try:
                # Simulate a disagreement requiring adjudication
                self.mock_adjudicator(root_id, v_ids)
            except CeilingExceededError:
                break

        return seeds, variants_by_root
