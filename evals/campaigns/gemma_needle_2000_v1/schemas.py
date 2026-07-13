from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from enum import Enum
from hmac import compare_digest
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


MAX_JSON_DEPTH = 6
MAX_JSON_ITEMS = 64
MAX_JSON_STRING = 4_096
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
LongText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096)
]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
DigestText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
Sha256Text = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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


PackName: TypeAlias = Literal[
    "tool_selection",
    "gemma_plan_state",
    "recovery_selection",
    "resource_selection",
    "memory_selection",
    "observation_typing",
]
PACK_NAMES = frozenset(
    {
        "tool_selection",
        "gemma_plan_state",
        "recovery_selection",
        "resource_selection",
        "memory_selection",
        "observation_typing",
    }
)


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="json", exclude_none=False))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    value = _canonical_json_value(value)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_sha256(value: str | None) -> bool:
    return bool(value and SHA256_RE.fullmatch(value))


def validate_bounded_json(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    max_depth: int = MAX_JSON_DEPTH,
) -> Any:
    """Validate a JSON-compatible tree without silently accepting huge payloads."""
    if depth > max_depth:
        raise ValueError(f"{path} exceeds maximum JSON depth {max_depth}")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING:
            raise ValueError(f"{path} contains an oversized string")
        return value
    if isinstance(value, list):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError(f"{path} contains too many list items")
        for index, item in enumerate(value):
            validate_bounded_json(item, path=f"{path}[{index}]", depth=depth + 1)
        return value
    if isinstance(value, dict):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError(f"{path} contains too many object properties")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError(f"{path} contains an invalid object key")
            validate_bounded_json(item, path=f"{path}.{key}", depth=depth + 1)
        return value
    raise ValueError(f"{path} is not JSON-compatible")


def _unique_text(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")
    return values


def derive_ttl_state(
    *,
    issued_at: datetime,
    expires_at: datetime,
    evaluated_at: datetime,
    soft_stale_at: datetime | None = None,
    revocation_facts: list[str] | None = None,
    post_ttl_facts: list[str] | None = None,
    structural_invalidators: list[str] | None = None,
) -> TTLState:
    """Single-valued TTL truth at a frozen evaluation timestamp."""
    if structural_invalidators or evaluated_at < issued_at:
        return TTLState.STRUCTURALLY_INVALID
    if revocation_facts:
        return TTLState.REVOKED
    if evaluated_at >= expires_at:
        if post_ttl_facts:
            return TTLState.POST_TTL
        return TTLState.EXPIRED
    if soft_stale_at is not None and evaluated_at >= soft_stale_at:
        return TTLState.SOFT_STALE
    return TTLState.FRESH


class TTLFacts(StrictModel):
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    evaluated_at: AwareDatetime
    soft_stale_at: AwareDatetime | None = None
    renewal_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    revocation_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    post_ttl_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    structural_invalidators: list[ShortText] = Field(
        default_factory=list, max_length=16
    )
    declared_ttl_state: TTLState

    @field_validator(
        "renewal_facts",
        "revocation_facts",
        "post_ttl_facts",
        "structural_invalidators",
    )
    @classmethod
    def facts_are_unique(cls, values: list[str], info: Any) -> list[str]:
        return _unique_text(values, info.field_name)

    @model_validator(mode="after")
    def timestamps_and_state_are_consistent(self) -> TTLFacts:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        if self.soft_stale_at is not None and not (
            self.issued_at <= self.soft_stale_at < self.expires_at
        ):
            raise ValueError("soft_stale_at must be within the issuance window")
        derived = self.derive_state()
        if self.declared_ttl_state != derived:
            raise ValueError(
                f"declared_ttl_state {self.declared_ttl_state.value!r} does not "
                f"match derived state {derived.value!r}"
            )
        return self

    def derive_state(self) -> TTLState:
        return derive_ttl_state(
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            evaluated_at=self.evaluated_at,
            soft_stale_at=self.soft_stale_at,
            revocation_facts=self.revocation_facts,
            post_ttl_facts=self.post_ttl_facts,
            structural_invalidators=self.structural_invalidators,
        )


class Receipt(StrictModel):
    receipt_spec_id: Identifier
    issuer_identity: Identifier
    verifier_identity: Identifier
    effect_id: Identifier
    observation_timestamp: AwareDatetime
    verification_result: bool
    content_digest: Sha256Text

    # Required by the strict authority verifier. Optional defaults retain the
    # Stage 0 research fixture as a parseable, explicitly legacy artifact.
    receipt_spec_version: Identifier | None = None
    verifier_code_digest: Sha256Text | None = None
    declared_input_digest: Sha256Text | None = None
    oracle_name: Identifier | None = None
    oracle_version: Identifier | None = None
    oracle_code_digest: Sha256Text | None = None
    observed_content: Any | None = None

    @field_validator("observed_content")
    @classmethod
    def observed_content_is_bounded(cls, value: Any | None) -> Any | None:
        if value is not None:
            validate_bounded_json(value, path="observed_content")
        return value

    def compute_content_digest(self) -> str | None:
        if self.observed_content is None:
            return None
        return canonical_sha256(self.observed_content)


class OracleRegistryContract(StrictModel):
    name: Identifier
    version: Identifier
    code_digest: Sha256Text
    declared_inputs: list[Identifier] = Field(max_length=32)
    deterministic_parameters: dict[str, Any] = Field(max_length=32)
    declared_input_digest: Sha256Text | None = None
    oracle_output: Any | None = None
    execution_receipt: Receipt | None = None
    pass_fail_result: bool | None = None
    output_digest: Sha256Text | None = None

    @field_validator("declared_inputs")
    @classmethod
    def inputs_are_unique(cls, values: list[str]) -> list[str]:
        return _unique_text(values, "declared_inputs")

    @field_validator("deterministic_parameters", "oracle_output")
    @classmethod
    def oracle_json_is_bounded(cls, value: Any, info: Any) -> Any:
        if value is not None:
            validate_bounded_json(value, path=info.field_name)
        return value


class ResultType(str, Enum):
    ACTION = "action"
    ABSTENTION = "abstention"
    CLARIFICATION = "clarification"
    DURABLE_WAIT = "durable_wait"
    REQUEST_VERIFICATION = "request_verification"


class BaseResult(StrictModel):
    type: ResultType


class ActionProposal(BaseResult):
    type: Literal[ResultType.ACTION] = ResultType.ACTION
    tool_name: Identifier
    tool_arguments: dict[str, Any] = Field(max_length=32)

    @field_validator("tool_arguments")
    @classmethod
    def arguments_are_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_bounded_json(value, path="tool_arguments")
        return value


class AbstentionProposal(BaseResult):
    type: Literal[ResultType.ABSTENTION] = ResultType.ABSTENTION
    reason: LongText


class ClarificationProposal(BaseResult):
    type: Literal[ResultType.CLARIFICATION] = ResultType.CLARIFICATION
    question: LongText


class DurableWaitProposal(BaseResult):
    type: Literal[ResultType.DURABLE_WAIT] = ResultType.DURABLE_WAIT
    condition: LongText


class RequestVerificationProposal(BaseResult):
    type: Literal[ResultType.REQUEST_VERIFICATION] = ResultType.REQUEST_VERIFICATION
    target_effect_id: Identifier


ProposalResultType: TypeAlias = Annotated[
    Union[
        ActionProposal,
        AbstentionProposal,
        ClarificationProposal,
        DurableWaitProposal,
        RequestVerificationProposal,
    ],
    Field(discriminator="type"),
]


class BaseRootEnvelope(StrictModel):
    pack_name: PackName
    output_contract_name: Identifier
    output_contract_version: Identifier
    output_contract_digest: Sha256Text
    tool_catalog_digest: Sha256Text
    schema_digest: Sha256Text
    prompt_template_digest: Sha256Text
    generator_config_digest: Sha256Text

    objective: LongText
    observations: list[dict[str, Any]] = Field(max_length=32)
    capabilities: list[Identifier] = Field(max_length=32)
    forbidden_effects: list[ShortText] = Field(max_length=32)

    issued_at: AwareDatetime
    expires_at: AwareDatetime
    evaluated_at: AwareDatetime
    soft_stale_at: AwareDatetime | None = None
    renewal_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    revocation_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    post_ttl_facts: list[ShortText] = Field(default_factory=list, max_length=16)
    structural_invalidators: list[ShortText] = Field(
        default_factory=list, max_length=16
    )
    declared_ttl_state: TTLState

    stable_effect_id: Identifier
    result: ProposalResultType
    mechanical_oracle: OracleRegistryContract
    proposal_verdict: ProposalVerdict
    episode_outcome: EpisodeOutcome
    required_receipt_specs: list[Identifier] = Field(
        default_factory=list, max_length=16
    )
    observed_receipts: list[Receipt] = Field(default_factory=list, max_length=16)

    root_id: Identifier
    parent_id: Identifier | None = None
    ancestor_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    leakage_group: Identifier
    provenance: dict[Identifier, ShortText] = Field(max_length=32)
    decision_summary: LongText | None = None
    plan_state: LongText | None = None
    immutable_artifact_digest: Sha256Text | None = None
    semantic_signature: Sha256Text | None = None

    @field_validator(
        "issued_at", "expires_at", "evaluated_at", "soft_stale_at", mode="before"
    )
    @classmethod
    def timestamps_are_timezone_aware(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware (UTC).")
        return value

    @field_validator("observations")
    @classmethod
    def observations_are_bounded_json(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        validate_bounded_json(value, path="observations")
        return value

    @field_validator(
        "capabilities",
        "forbidden_effects",
        "renewal_facts",
        "revocation_facts",
        "post_ttl_facts",
        "structural_invalidators",
        "required_receipt_specs",
        "ancestor_ids",
    )
    @classmethod
    def bounded_lists_are_unique(cls, values: list[str], info: Any) -> list[str]:
        return _unique_text(values, info.field_name)

    @field_validator("decision_summary", "plan_state", mode="before")
    @classmethod
    def check_no_hidden_cot(cls, value: Any) -> Any:
        compact = re.sub(
            r"[^a-z0-9]",
            "",
            unicodedata.normalize("NFKC", str(value or "")).casefold(),
        )
        if any(
            marker in compact
            for marker in (
                "chainofthought",
                "hiddencot",
                "reasoningtrace",
                "scratchpad",
            )
        ):
            raise ValueError("Hidden chain-of-thought is forbidden.")
        return value

    @model_validator(mode="after")
    def ttl_contract_is_consistent(self) -> BaseRootEnvelope:
        facts = self.ttl_facts()
        if facts.declared_ttl_state != self.declared_ttl_state:
            raise ValueError("TTL fact projection mismatch")
        return self

    def ttl_facts(self) -> TTLFacts:
        return TTLFacts(
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            evaluated_at=self.evaluated_at,
            soft_stale_at=self.soft_stale_at,
            renewal_facts=self.renewal_facts,
            revocation_facts=self.revocation_facts,
            post_ttl_facts=self.post_ttl_facts,
            structural_invalidators=self.structural_invalidators,
            declared_ttl_state=self.declared_ttl_state,
        )

    def compute_stable_effect_id(self) -> str:
        payload = {
            "scenario_scope": self.leakage_group,
            "pack_name": self.pack_name,
            "result": self.result.model_dump(mode="json"),
        }
        return f"effect-{canonical_sha256(payload)[:40]}"

    def compute_semantic_signature(self) -> str:
        # Deliberately excludes harness IDs, lineage, timestamps, receipts,
        # outcomes, provider/runtime digests, and provenance.
        payload = {
            "pack_name": self.pack_name,
            "objective": self.objective,
            "observations": self.observations,
            "capabilities": sorted(self.capabilities),
            "forbidden_effects": sorted(self.forbidden_effects),
            "result": self.result.model_dump(mode="json"),
            "ttl_state": self.declared_ttl_state,
            "renewal_facts": sorted(self.renewal_facts),
            "revocation_facts": sorted(self.revocation_facts),
            "post_ttl_facts": sorted(self.post_ttl_facts),
            "structural_invalidators": sorted(self.structural_invalidators),
            "decision_summary": self.decision_summary,
            "plan_state": self.plan_state,
        }
        return canonical_sha256(payload)

    def compute_immutable_artifact_digest(self) -> str:
        data = self.model_dump(
            mode="json",
            exclude={"immutable_artifact_digest"},
            exclude_none=False,
        )
        # Normalize the integrity field so computation never trusts a supplied
        # (possibly forged) semantic signature.
        data["semantic_signature"] = self.compute_semantic_signature()
        return canonical_sha256(data)

    def finalize_integrity(self) -> None:
        self.semantic_signature = self.compute_semantic_signature()
        self.immutable_artifact_digest = self.compute_immutable_artifact_digest()

    def integrity_matches(self) -> bool:
        if not self.semantic_signature or not self.immutable_artifact_digest:
            return False
        return compare_digest(
            self.semantic_signature, self.compute_semantic_signature()
        ) and compare_digest(
            self.immutable_artifact_digest,
            self.compute_immutable_artifact_digest(),
        )


class ToolSelectionPack(BaseRootEnvelope):
    pack_name: Literal["tool_selection"] = "tool_selection"


class GemmaPlanStatePack(BaseRootEnvelope):
    pack_name: Literal["gemma_plan_state"] = "gemma_plan_state"
    long_chain_transitions: int = Field(ge=1, le=256)


class RecoverySelectionPack(BaseRootEnvelope):
    pack_name: Literal["recovery_selection"] = "recovery_selection"


class ResourceSelectionPack(BaseRootEnvelope):
    pack_name: Literal["resource_selection"] = "resource_selection"


class MemorySelectionPack(BaseRootEnvelope):
    pack_name: Literal["memory_selection"] = "memory_selection"


class ObservationTypingPack(BaseRootEnvelope):
    pack_name: Literal["observation_typing"] = "observation_typing"
