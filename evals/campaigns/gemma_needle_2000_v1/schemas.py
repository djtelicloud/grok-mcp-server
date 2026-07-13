import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional, Literal, Any, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator

class EpisodeOutcome(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNVERIFIED = "unverified"

class ProposalVerdict(str, Enum):
    VALID_PROPOSAL = "valid_proposal"
    INVALID_PROPOSAL = "invalid_proposal"
    UNVERIFIED = "unverified"

class TTLState(str, Enum):
    FRESH = "fresh"
    SOFT_STALE = "soft_stale"
    EXPIRED = "expired"
    REVOKED = "revoked"
    POST_TTL = "post_ttl"
    STRUCTURALLY_INVALID = "structurally_invalid"

class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    receipt_spec_id: str
    issuer_identity: str
    verifier_identity: str
    effect_id: str
    observation_timestamp: datetime
    verification_result: bool
    content_digest: str

class OracleRegistryContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    name: str
    version: str
    code_digest: str
    declared_inputs: List[str]
    deterministic_parameters: Dict[str, Any]
    execution_receipt: Optional[Receipt] = None
    pass_fail_result: Optional[bool] = None
    output_digest: Optional[str] = None

class ResultType(str, Enum):
    ACTION = "action"
    ABSTENTION = "abstention"
    CLARIFICATION = "clarification"
    DURABLE_WAIT = "durable_wait"
    REQUEST_VERIFICATION = "request_verification"

class BaseResult(BaseModel):
    type: ResultType

class ActionProposal(BaseResult):
    type: Literal[ResultType.ACTION] = ResultType.ACTION
    tool_name: str
    tool_arguments: Dict[str, Any]

class AbstentionProposal(BaseResult):
    type: Literal[ResultType.ABSTENTION] = ResultType.ABSTENTION
    reason: str

class ClarificationProposal(BaseResult):
    type: Literal[ResultType.CLARIFICATION] = ResultType.CLARIFICATION
    question: str

class DurableWaitProposal(BaseResult):
    type: Literal[ResultType.DURABLE_WAIT] = ResultType.DURABLE_WAIT
    condition: str

class RequestVerificationProposal(BaseResult):
    type: Literal[ResultType.REQUEST_VERIFICATION] = ResultType.REQUEST_VERIFICATION
    target_effect_id: str

ProposalResultType = Union[
    ActionProposal,
    AbstentionProposal,
    ClarificationProposal,
    DurableWaitProposal,
    RequestVerificationProposal
]

class BaseRootEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    pack_name: str
    output_contract_name: str
    output_contract_version: str
    output_contract_digest: str
    tool_catalog_digest: str
    schema_digest: str
    prompt_template_digest: str
    generator_config_digest: str
    
    objective: str
    observations: List[Dict[str, Any]]
    capabilities: List[str]
    forbidden_effects: List[str]
    
    issued_at: datetime
    expires_at: datetime
    evaluated_at: Optional[datetime] = None
    renewal_facts: List[str] = Field(default_factory=list)
    revocation_facts: List[str] = Field(default_factory=list)
    structural_invalidators: List[str] = Field(default_factory=list)
    declared_ttl_state: TTLState
    
    stable_effect_id: str
    
    result: ProposalResultType
    mechanical_oracle: OracleRegistryContract
    
    proposal_verdict: ProposalVerdict
    episode_outcome: EpisodeOutcome
    
    required_receipt_specs: List[str] = Field(default_factory=list)
    observed_receipts: List[Receipt] = Field(default_factory=list)
    
    root_id: str
    parent_id: Optional[str] = None
    ancestor_ids: List[str] = Field(default_factory=list)
    leakage_group: str
    
    provenance: Dict[str, str]
    decision_summary: Optional[str] = None
    plan_state: Optional[str] = None

    immutable_artifact_digest: Optional[str] = None
    semantic_signature: Optional[str] = None

    @field_validator('decision_summary', 'plan_state', mode='before')
    @classmethod
    def check_no_hidden_cot(cls, v: Any) -> Any:
        if v and "chain_of_thought" in str(v).lower():
             raise ValueError("Hidden chain-of-thought is forbidden.")
        return v
        
    @field_validator('issued_at', 'expires_at', 'evaluated_at', mode='after')
    @classmethod
    def ensure_timezone_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware (UTC).")
        return v

    def compute_semantic_signature(self) -> str:
        # Exclude mutable metadata like timestamps, receipt details, and digests.
        data = {
            "pack_name": self.pack_name,
            "objective": self.objective,
            "result": self.result.model_dump(),
            "stable_effect_id": self.stable_effect_id,
            "decision_summary": self.decision_summary,
            "plan_state": self.plan_state
        }
        canonical_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical_str.encode()).hexdigest()
        
    def compute_immutable_artifact_digest(self) -> str:
        # Includes everything except the immutable_artifact_digest itself
        data = self.model_dump(exclude={'immutable_artifact_digest'})
        canonical_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical_str.encode()).hexdigest()

class ToolSelectionPack(BaseRootEnvelope):
    pack_name: Literal["tool_selection"] = "tool_selection"

class GemmaPlanStatePack(BaseRootEnvelope):
    pack_name: Literal["gemma_plan_state"] = "gemma_plan_state"
    long_chain_transitions: int

class RecoverySelectionPack(BaseRootEnvelope):
    pack_name: Literal["recovery_selection"] = "recovery_selection"

class ResourceSelectionPack(BaseRootEnvelope):
    pack_name: Literal["resource_selection"] = "resource_selection"

class MemorySelectionPack(BaseRootEnvelope):
    pack_name: Literal["memory_selection"] = "memory_selection"

class ObservationTypingPack(BaseRootEnvelope):
    pack_name: Literal["observation_typing"] = "observation_typing"
