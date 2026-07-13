import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator

class Verdict(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNVERIFIED = "unverified"

class TTLState(str, Enum):
    FRESH = "fresh"
    SOFT_STALE = "soft_stale"
    EXPIRED = "expired"
    REVOKED = "revoked"
    POST_TTL = "post_ttl"

class PackDiscriminator(str, Enum):
    TOOL_SELECTION = "tool_selection"
    GEMMA_PLAN_STATE = "gemma_plan_state"
    RECOVERY_SELECTION = "recovery_selection"
    RESOURCE_SELECTION = "resource_selection"
    MEMORY_SELECTION = "memory_selection"
    OBSERVATION_TYPING = "observation_typing"

class BaseRootEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    pack_name: PackDiscriminator
    output_contract_name: str
    output_contract_version: str
    output_contract_digest: str
    tool_catalog_digest: str
    schema_digest: str
    
    objective: str
    observations: List[Dict[str, Any]]
    capabilities: List[str]
    forbidden_effects: List[str]
    
    issued_at: datetime
    expires_at: datetime
    evaluated_at: Optional[datetime] = None
    ttl_state: TTLState
    structural_invalidators: List[str]
    
    stable_effect_id: str
    
    result: Dict[str, Any] # Must contain Proposal/abstention/clarification/wait/request-verification
    mechanical_oracle_spec: str
    verdict: Verdict
    required_receipts: List[str]
    
    root_id: str
    parent_id: Optional[str]
    ancestor_ids: List[str]
    leakage_group: str
    
    provenance: Dict[str, str] # Generator, reviewer, run info
    decision_summary: Optional[str] = None
    plan_state: Optional[str] = None
    
    canonical_digest_algorithm: str = "sha256"
    canonical_digest_version: str = "v1"

    @field_validator('decision_summary', 'plan_state', mode='before')
    @classmethod
    def check_no_hidden_cot(cls, v: Any, info) -> Any:
        if v and "chain_of_thought" in str(v).lower():
             raise ValueError("Hidden chain-of-thought is forbidden.")
        return v
        
    def compute_canonical_digest(self) -> str:
        # Canonicalization: strip previous digest and provenance, hash the semantic fields
        data = self.model_dump(exclude={'provenance'})
        # Serialize deterministically
        canonical_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical_str.encode()).hexdigest()

def validate_payload(data: dict) -> BaseRootEnvelope:
    # Ensure extra prohibited fields are caught by Extra.forbid
    return BaseRootEnvelope(**data)
