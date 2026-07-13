import copy
import uuid
from typing import Dict, Any
from schemas import BaseRootEnvelope, TTLState

class MechanicalMutators:
    @staticmethod
    def _prepare_mutation(envelope: BaseRootEnvelope, mutation_type: str) -> BaseRootEnvelope:
        mutated = copy.deepcopy(envelope)
        source_id = mutated.root_id
        
        # Lineage update
        mutated.parent_id = source_id
        mutated.ancestor_ids.append(source_id)
        mutated.root_id = f"mut-{uuid.uuid4().hex[:8]}"
        
        # Provenance tracking
        mutated.provenance["mutation_type"] = mutation_type
        mutated.provenance["mutation_source"] = source_id
        
        return mutated

    @staticmethod
    def shift_ttl_boundary(envelope: BaseRootEnvelope, target_state: TTLState) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, f"ttl_shift_{target_state.value}")
        mutated.ttl_state = target_state
        return mutated

    @staticmethod
    def inject_catalog_drift(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "catalog_drift")
        mutated.tool_catalog_digest = f"drift_{uuid.uuid4().hex[:8]}"
        # Ensure mechanical oracle marks this as unverified or failure if tools are missing
        return mutated

    @staticmethod
    def force_duplicate_effect(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "duplicate_effect")
        # Identical stable_effect_id retained by _prepare_mutation deepcopy
        # We explicitly tag it to simulate a repeated semantic execution
        mutated.result["type"] = "action"
        return mutated

    @staticmethod
    def inject_missing_receipt(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "missing_receipt")
        mutated.required_receipts.append("mock_receipt_12345")
        # Ensure verdict is not VERIFIED_SUCCESS since receipt is missing
        if mutated.verdict == "verified_success":
            mutated.verdict = "unverified"
        return mutated

    @staticmethod
    def inject_false_completion(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "false_completion")
        mutated.result["type"] = "done" # False completion trap
        mutated.result["content"] = "I have completed the task."
        return mutated
