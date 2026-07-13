import copy
import uuid
from .schemas import BaseRootEnvelope, TTLState, ProposalVerdict, EpisodeOutcome, AbstentionProposal

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
        
        # Recompute digests
        mutated.semantic_signature = mutated.compute_semantic_signature()
        mutated.immutable_artifact_digest = mutated.compute_immutable_artifact_digest()
        
        return mutated

    @staticmethod
    def shift_ttl_boundary(envelope: BaseRootEnvelope, target_state: TTLState) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, f"ttl_shift_{target_state.value}")
        mutated.declared_ttl_state = target_state
        if target_state != TTLState.FRESH:
            mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        # Update digests again since fields changed
        mutated.semantic_signature = mutated.compute_semantic_signature()
        mutated.immutable_artifact_digest = mutated.compute_immutable_artifact_digest()
        return mutated

    @staticmethod
    def inject_catalog_drift(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "catalog_drift")
        mutated.tool_catalog_digest = f"drift_{uuid.uuid4().hex[:8]}"
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        mutated.semantic_signature = mutated.compute_semantic_signature()
        mutated.immutable_artifact_digest = mutated.compute_immutable_artifact_digest()
        return mutated

    @staticmethod
    def inject_missing_receipt(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "missing_receipt")
        mutated.required_receipt_specs.append("missing_mock_spec_1")
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        mutated.semantic_signature = mutated.compute_semantic_signature()
        mutated.immutable_artifact_digest = mutated.compute_immutable_artifact_digest()
        return mutated

    @staticmethod
    def inject_false_completion(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "false_completion")
        # Turn it into an abstention with a "done" reason (since "done" is forbidden as a terminal type)
        mutated.result = AbstentionProposal(reason="I have completed the task.")
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        mutated.proposal_verdict = ProposalVerdict.INVALID_PROPOSAL
        mutated.semantic_signature = mutated.compute_semantic_signature()
        mutated.immutable_artifact_digest = mutated.compute_immutable_artifact_digest()
        return mutated
