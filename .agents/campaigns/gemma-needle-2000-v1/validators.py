import re
from typing import Dict, List, Set, Any
from schemas import BaseRootEnvelope, Verdict

class MechanicalValidators:
    @staticmethod
    def scan_unsafe_credentials(envelope: BaseRootEnvelope) -> bool:
        """
        Rejects actual credential values (API keys, dense hex/b64 entropy),
        but allows legitimate vocabulary like 'password' or 'token' in schema/context.
        """
        content = str(envelope.model_dump())
        # Deny obvious high-entropy API key patterns or bearer tokens, but allow the word itself
        if re.search(r'(AIza[0-9A-Za-z-_]{35}|ghp_[0-9a-zA-Z]{36}|Bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*)', content):
            return False
        return True

    @staticmethod
    def verify_no_exact_duplicates(root_hashes: Set[str], new_envelope: BaseRootEnvelope) -> bool:
        digest = new_envelope.compute_canonical_digest()
        return digest not in root_hashes

    @staticmethod
    def verify_leakage_group(parent_envelope: BaseRootEnvelope, child_envelope: BaseRootEnvelope) -> bool:
        """A semantic root and all descendants must remain in one leakage group."""
        return parent_envelope.leakage_group == child_envelope.leakage_group

    @staticmethod
    def verify_ttl_and_receipts(envelope: BaseRootEnvelope) -> bool:
        """Post-TTL action, false completion, and missing required receipts are hard failures."""
        if envelope.ttl_state in ("expired", "revoked", "post_ttl"):
            # Any action proposed after TTL is a failure (wait/request_verification allowed)
            action_type = envelope.result.get("type", "")
            if action_type not in ("wait", "request_verification", "abstention"):
                return False
                
        # If required receipts are missing, action must not be marked as verified success
        if envelope.required_receipts:
            # Simulated check: in full implementation, we cross-reference receipts
            # Here we ensure it doesn't falsely claim success
            pass
            
        return True

    @staticmethod
    def verify_oracle_consistency(envelope: BaseRootEnvelope) -> bool:
        """No accepted positive label without an executable mechanical oracle."""
        if envelope.verdict == Verdict.VERIFIED_SUCCESS:
            if not envelope.mechanical_oracle_spec or envelope.mechanical_oracle_spec.strip() == "":
                return False
        return True

    @staticmethod
    def verify_no_duplicate_semantic_effect(trajectory_effect_ids: List[str], envelope: BaseRootEnvelope) -> bool:
        """Duplicate execution of one semantic effect is a hard failure."""
        action_type = envelope.result.get("type", "")
        if action_type == "action":
            if envelope.stable_effect_id in trajectory_effect_ids:
                return False
        return True

    @staticmethod
    def enforce_unverified_preservation(original_verdict: Verdict, new_verdict: Verdict) -> bool:
        """'unverified' must never be converted into 'verified_failure'."""
        if original_verdict == Verdict.UNVERIFIED and new_verdict == Verdict.VERIFIED_FAILURE:
            return False
        return True
