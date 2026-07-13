from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import ProposalResultType

class AdvisoryLabel(str, Enum):
    AGREE = "agree"
    DISAGREE = "disagree"
    UNSURE = "unsure"

class SeedCandidate(BaseModel):
    """Untrusted payload from Grok Seed Author."""
    model_config = ConfigDict(extra='forbid')
    
    pack_name: str
    objective: str
    observations: List[Dict[str, Any]]
    capabilities: List[str]
    forbidden_effects: List[str]
    result: ProposalResultType
    decision_summary: Optional[str] = None
    plan_state: Optional[str] = None

    @field_validator('decision_summary', 'plan_state', mode='before')
    @classmethod
    def check_no_hidden_cot(cls, v: Any) -> Any:
        if v and "chain_of_thought" in str(v).lower():
             raise ValueError("Hidden chain-of-thought is forbidden.")
        return v

class VariantCandidate(BaseModel):
    """Untrusted payload for a single variant."""
    model_config = ConfigDict(extra='forbid')
    
    variant_key: str
    objective: str
    observations: List[Dict[str, Any]]
    result: ProposalResultType
    decision_summary: Optional[str] = None
    plan_state: Optional[str] = None

class VariantBatch(BaseModel):
    """Untrusted payload from Gemini Mutator."""
    model_config = ConfigDict(extra='forbid')
    
    root_reference: str
    variants: List[VariantCandidate] = Field(..., min_length=4, max_length=4)

class CriticVerdict(BaseModel):
    """Untrusted payload for a single critic verdict."""
    model_config = ConfigDict(extra='forbid')
    
    variant_key: str
    advisory_label: AdvisoryLabel
    reasoning: str

class CriticVerdictBatch(BaseModel):
    """Untrusted payload from Grok Critic."""
    model_config = ConfigDict(extra='forbid')
    
    root_reference: str
    verdicts: List[CriticVerdict] = Field(..., min_length=4, max_length=4)

class AdjudicationVerdict(BaseModel):
    """Untrusted payload for a single adjudication verdict."""
    model_config = ConfigDict(extra='forbid')
    
    variant_key: str
    advisory_label: AdvisoryLabel
    reasoning: str

class AdjudicationVerdictBatch(BaseModel):
    """Untrusted payload from Gemini Adjudicator."""
    model_config = ConfigDict(extra='forbid')
    
    root_reference: str
    verdicts: List[AdjudicationVerdict]
