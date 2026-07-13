import re
from datetime import datetime, timezone
from typing import Dict, List, Set, Any
from .schemas import BaseRootEnvelope, EpisodeOutcome, TTLState

class MechanicalValidators:
    @staticmethod
    def scan_unsafe_credentials(envelope: BaseRootEnvelope) -> bool:
        """
        Rejects actual credential values (API keys, dense hex/b64 entropy),
        but allows legitimate vocabulary like 'password' or 'token' in schema/context.
        """
        content = str(envelope.model_dump())
        
        # Deny obvious high-entropy API key patterns, bearer tokens, AWS/GitHub secrets
        patterns = [
            r'AIza[0-9A-Za-z\-_]{35}', # Google API
            r'ghp_[0-9a-zA-Z]{36}', # GitHub PAT
            r'AKIA[0-9A-Z]{16}', # AWS Access Key
            r'Bearer\s+[a-zA-Z0-9\-\._~\+\/]{20,}=*', # Bearer tokens
            r'(?:[a-zA-Z0-9+_.-]+@[a-zA-Z0-9.-]+)', # Emails (Basic PII)
            r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', # Phones (Basic PII)
            r'(/Users/[a-zA-Z0-9_]+|/home/[a-zA-Z0-9_]+)', # Developer home paths
        ]
        
        for pattern in patterns:
            if re.search(pattern, content):
                return False
        return True

    @staticmethod
    def verify_no_exact_duplicates(artifact_digests: Set[str], new_envelope: BaseRootEnvelope) -> bool:
        digest = new_envelope.immutable_artifact_digest or new_envelope.compute_immutable_artifact_digest()
        return digest not in artifact_digests
        
    @staticmethod
    def verify_no_semantic_duplicate(semantic_signatures: Set[str], new_envelope: BaseRootEnvelope) -> bool:
        sig = new_envelope.semantic_signature or new_envelope.compute_semantic_signature()
        return sig not in semantic_signatures

    @staticmethod
    def verify_leakage_group(parent_envelope: BaseRootEnvelope, child_envelope: BaseRootEnvelope) -> bool:
        """A semantic root and all descendants must remain in one leakage group."""
        return parent_envelope.leakage_group == child_envelope.leakage_group

    @staticmethod
    def verify_ttl_state(envelope: BaseRootEnvelope, current_utc_time: datetime) -> bool:
        """Validates that declared_ttl_state matches reality based on timestamps and facts."""
        if envelope.structural_invalidators:
            return envelope.declared_ttl_state == TTLState.STRUCTURALLY_INVALID
        
        if envelope.revocation_facts:
            return envelope.declared_ttl_state == TTLState.REVOKED
            
        if envelope.expires_at < current_utc_time:
            return envelope.declared_ttl_state in (TTLState.EXPIRED, TTLState.POST_TTL)
            
        return envelope.declared_ttl_state == TTLState.FRESH

    @staticmethod
    def verify_receipts_complete(envelope: BaseRootEnvelope) -> bool:
        """Fail closed when a required receipt is absent, malformed, or digest-inconsistent."""
        required = set(envelope.required_receipt_specs)
        observed = set(r.receipt_spec_id for r in envelope.observed_receipts if r.verification_result)
        
        # Every required receipt must have a valid observed counterpart
        if not required.issubset(observed):
            return False
            
        for receipt in envelope.observed_receipts:
            if not receipt.content_digest or len(receipt.content_digest) < 32:
                return False
        return True

    @staticmethod
    def evaluate_episode_outcome(envelope: BaseRootEnvelope, current_utc_time: datetime) -> EpisodeOutcome:
        """Determines if an episode can be considered verified success based on hard mechanical gates."""
        if envelope.episode_outcome == EpisodeOutcome.UNVERIFIED:
            return EpisodeOutcome.UNVERIFIED
            
        if envelope.episode_outcome == EpisodeOutcome.VERIFIED_SUCCESS:
            # Must have an executable registered oracle
            if not envelope.mechanical_oracle or not envelope.mechanical_oracle.execution_receipt:
                return EpisodeOutcome.VERIFIED_FAILURE
                
            if not envelope.mechanical_oracle.pass_fail_result:
                return EpisodeOutcome.VERIFIED_FAILURE
                
            if not MechanicalValidators.verify_receipts_complete(envelope):
                return EpisodeOutcome.VERIFIED_FAILURE
                
            if not MechanicalValidators.verify_ttl_state(envelope, current_utc_time):
                return EpisodeOutcome.VERIFIED_FAILURE
                
            if envelope.declared_ttl_state != TTLState.FRESH:
                return EpisodeOutcome.VERIFIED_FAILURE
                
            # If the proposal itself is a request for verification or wait, it's not a successful terminal episode completion
            if envelope.result.type in ("durable_wait", "request_verification"):
                return EpisodeOutcome.VERIFIED_FAILURE
                
        return envelope.episode_outcome

    @staticmethod
    def verify_no_duplicate_semantic_effect(trajectory_effect_ids: List[str], envelope: BaseRootEnvelope) -> bool:
        """Duplicate execution of one semantic effect is a hard failure."""
        if envelope.result.type == "action":
            if envelope.stable_effect_id in trajectory_effect_ids:
                return False
        return True
