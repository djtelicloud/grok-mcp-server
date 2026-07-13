class MechanicalMutator:
    @staticmethod
    def shift_ttl(root: dict, state: str) -> dict:
        """Shifts TTL boundaries: fresh, soft-stale, expired, revoked, post-TTL."""
        mutated = root.copy()
        mutated["ttl_state"] = state
        return mutated
        
    @staticmethod
    def inject_catalog_drift(root: dict) -> dict:
        """Injects tool/catalog/schema drift and confusable-tool cases."""
        pass
        
    @staticmethod
    def force_duplicate_effect(root: dict) -> dict:
        """Creates an exact semantic effect duplicate for testing hard vetoes."""
        pass
        
    @staticmethod
    def strip_receipts(root: dict) -> dict:
        """Simulates missing-receipt traps."""
        pass
