import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
from pydantic import ValidationError

from .attempt_ledger import AttemptLedger
from .role_schemas import SeedCandidate, VariantBatch, CriticVerdictBatch, AdjudicationVerdictBatch
from .schemas import BaseRootEnvelope, ToolSelectionPack

class CeilingExceededError(RuntimeError):
    pass

class Stage1MockHarness:
    def __init__(self, ledger_path: Path):
        self.ledger = AttemptLedger(ledger_path)
        self.max_attempts = 120
        self.scenario_evaluated_at = datetime.now(timezone.utc)
        self.run_lease_deadline = datetime.now(timezone.utc) + timedelta(minutes=60)
        
    def _check_lease_and_ceiling(self):
        if datetime.now(timezone.utc) > self.run_lease_deadline:
            raise RuntimeError("Run lease deadline exceeded.")
        if self.ledger.get_total_attempts() >= self.max_attempts:
            raise CeilingExceededError("Maximum provider attempts (120) exceeded.")

    def mock_seed_generation(self) -> Optional[SeedCandidate]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="seed_author")
        
        # Mock provider logic
        try:
            candidate = SeedCandidate(
                pack_name="tool_selection",
                objective="Mock objective",
                observations=[{"type": "text", "content": "mock"}],
                capabilities=["mock_tool"],
                forbidden_effects=[],
                stable_effect_id=str(uuid.uuid4()),
                result={"type": "action", "tool_name": "mock_tool", "tool_arguments": {}}
            )
            self.ledger.log_completed(work_item_id)
            return candidate
        except Exception:
            self.ledger.log_failed(work_item_id)
            return None

    def mock_mutation(self, root_reference: str) -> Optional[VariantBatch]:
        self._check_lease_and_ceiling()
        work_item_id = self.ledger.log_started(role="mutator", root_reference=root_reference)
        
        try:
            batch = VariantBatch(
                root_reference=root_reference,
                variants=[
                    {
                        "variant_key": f"v{i}",
                        "objective": f"Variant {i}",
                        "observations": [],
                        "stable_effect_id": str(uuid.uuid4()),
                        "result": {"type": "action", "tool_name": "mock_tool", "tool_arguments": {}}
                    } for i in range(4)
                ]
            )
            self.ledger.log_completed(work_item_id)
            return batch
        except Exception:
            self.ledger.log_failed(work_item_id)
            return None

    def mechanical_validation_gate(self, candidate: Any) -> bool:
        # Mock mechanical oracle verification
        # Recompute TTL, check secrets, lineage
        # In mock, we just say it passes unless we want to simulate a failure
        return True

    def run_stage1_mock(self):
        # 1. Seed Generation
        seeds = []
        for _ in range(30):
            try:
                candidate = self.mock_seed_generation()
                if candidate and self.mechanical_validation_gate(candidate):
                    seeds.append(candidate)
            except CeilingExceededError:
                break
                
        # 2. Mutation
        variants = []
        for seed in seeds:
            try:
                batch = self.mock_mutation(seed.stable_effect_id)
                if batch and self.mechanical_validation_gate(batch):
                    variants.append(batch)
            except CeilingExceededError:
                break
                
        # 3. Critic & 4. Adjudicator would go here in a full run
        return seeds, variants
