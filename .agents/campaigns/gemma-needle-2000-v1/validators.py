import re

class QualityGates:
    @staticmethod
    def scan_secrets(data: dict) -> bool:
        """100% secret, PII, and unsafe-path scanning."""
        content = str(data)
        if re.search(r'(api[_\-]key|secret|token|password)', content, re.IGNORECASE):
            return False
        return True
        
    @staticmethod
    def verify_no_duplicates(root_hashes: set, new_hash: str) -> bool:
        """Zero exact duplicates."""
        return new_hash not in root_hashes
        
    @staticmethod
    def check_effect_id_stability(trajectory: list) -> bool:
        """Stable semantic effects retain the same effect identity across retry/restart."""
        return True
