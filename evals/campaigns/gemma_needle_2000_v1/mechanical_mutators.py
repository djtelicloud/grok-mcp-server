from __future__ import annotations

from datetime import timedelta

from .schemas import (
    AbstentionProposal,
    BaseRootEnvelope,
    EpisodeOutcome,
    ProposalVerdict,
    TTLState,
    canonical_sha256,
)


class MechanicalMutators:
    @staticmethod
    def _prepare_mutation(
        envelope: BaseRootEnvelope, mutation_type: str
    ) -> BaseRootEnvelope:
        mutated = envelope.model_copy(deep=True)
        source_id = mutated.root_id
        mutated.parent_id = source_id
        mutated.ancestor_ids = [*mutated.ancestor_ids, source_id]
        mutated.root_id = (
            "mut-"
            + canonical_sha256(
                {
                    "source_id": source_id,
                    "mutation_type": mutation_type,
                    "ancestor_ids": mutated.ancestor_ids,
                }
            )[:16]
        )
        mutated.provenance = {
            **mutated.provenance,
            "mutation_type": mutation_type,
            "mutation_source": source_id,
        }
        # Do not compute integrity here. Every public mutator finalizes only
        # after all semantic and authority fields have changed.
        mutated.semantic_signature = None
        mutated.immutable_artifact_digest = None
        return mutated

    @staticmethod
    def _finalize(mutated: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated.stable_effect_id = mutated.compute_stable_effect_id()
        # Revalidate all cross-field TTL constraints after coordinated edits.
        validated = type(mutated).model_validate(mutated.model_dump())
        validated.finalize_integrity()
        return validated

    @staticmethod
    def shift_ttl_boundary(
        envelope: BaseRootEnvelope, target_state: TTLState
    ) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(
            envelope, f"ttl_shift_{target_state.value}"
        )
        assert mutated.evaluated_at is not None
        evaluated_at = mutated.evaluated_at

        # Start from a clean, valid TTL fact set, then create exactly one state.
        mutated.issued_at = evaluated_at - timedelta(hours=2)
        mutated.expires_at = evaluated_at + timedelta(hours=2)
        mutated.soft_stale_at = None
        mutated.renewal_facts = []
        mutated.revocation_facts = []
        mutated.post_ttl_facts = []
        mutated.structural_invalidators = []

        if target_state == TTLState.SOFT_STALE:
            mutated.soft_stale_at = evaluated_at
        elif target_state == TTLState.EXPIRED:
            # Boundary equality is single-valued and means expired.
            mutated.expires_at = evaluated_at
        elif target_state == TTLState.REVOKED:
            mutated.revocation_facts = ["authority revoked before evaluation"]
        elif target_state == TTLState.POST_TTL:
            mutated.expires_at = evaluated_at
            mutated.post_ttl_facts = ["effect observed after TTL boundary"]
        elif target_state == TTLState.STRUCTURALLY_INVALID:
            mutated.structural_invalidators = ["malformed TTL authority envelope"]
        elif target_state != TTLState.FRESH:
            raise ValueError(f"unsupported TTL target: {target_state}")

        mutated.declared_ttl_state = target_state
        if target_state != TTLState.FRESH:
            mutated.proposal_verdict = ProposalVerdict.INVALID_PROPOSAL
            mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        return MechanicalMutators._finalize(mutated)

    @staticmethod
    def inject_catalog_drift(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "catalog_drift")
        mutated.tool_catalog_digest = canonical_sha256(
            {
                "prior": envelope.tool_catalog_digest,
                "mutation": "catalog_drift",
                "root": mutated.root_id,
            }
        )
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        return MechanicalMutators._finalize(mutated)

    @staticmethod
    def inject_missing_receipt(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "missing_receipt")
        missing_spec = "missing_mock_spec_1"
        if missing_spec not in mutated.required_receipt_specs:
            mutated.required_receipt_specs = [
                *mutated.required_receipt_specs,
                missing_spec,
            ]
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        return MechanicalMutators._finalize(mutated)

    @staticmethod
    def inject_false_completion(envelope: BaseRootEnvelope) -> BaseRootEnvelope:
        mutated = MechanicalMutators._prepare_mutation(envelope, "false_completion")
        mutated.result = AbstentionProposal(reason="I have completed the task.")
        mutated.episode_outcome = EpisodeOutcome.VERIFIED_FAILURE
        mutated.proposal_verdict = ProposalVerdict.INVALID_PROPOSAL
        return MechanicalMutators._finalize(mutated)
