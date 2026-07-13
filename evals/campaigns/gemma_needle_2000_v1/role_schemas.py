from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated

from .schemas import (
    Identifier,
    LongText,
    PackName,
    ProposalResultType,
    ProposalVerdict,
    Sha256Text,
    ShortText,
    StrictModel,
    TTLFacts,
    canonical_sha256,
    validate_bounded_json,
)


MAX_RAW_ROLE_PAYLOAD_BYTES = 262_144
EXACT_VARIANT_COUNT = 4

CandidateToken = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=16,
        max_length=64,
        pattern=r"^candidate-[0-9a-f]{16,54}$",
    ),
]
ReasonCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]

# These fields are minted or derived by the harness. They are rejected at any
# depth before Pydantic parsing so they cannot hide in observations/tool args.
AUTHORITY_FIELD_NAMES = frozenset(
    {
        "pack_name",
        "output_contract_name",
        "output_contract_version",
        "output_contract_digest",
        "root_reference",
        "variant_key",
        "stable_effect_id",
        "effect_id",
        "root_id",
        "parent_id",
        "ancestor_ids",
        "leakage_group",
        "issued_at",
        "expires_at",
        "soft_stale_at",
        "evaluated_at",
        "scenario_evaluated_at",
        "scenario_digest",
        "expected_result_digest",
        "expected_effect_observed",
        "renewal_facts",
        "revocation_facts",
        "post_ttl_facts",
        "structural_invalidators",
        "declared_ttl_state",
        "proposal_verdict",
        "episode_outcome",
        "mechanical_oracle",
        "oracle",
        "oracle_name",
        "oracle_version",
        "oracle_code_digest",
        "oracle_output",
        "pass_fail_result",
        "receipt",
        "receipts",
        "receipt_spec_id",
        "receipt_spec_version",
        "required_receipt_specs",
        "observed_receipts",
        "issuer_identity",
        "verifier_identity",
        "verifier_code_digest",
        "observation_timestamp",
        "verification_result",
        "content_digest",
        "output_digest",
        "declared_input_digest",
        "code_digest",
        "immutable_artifact_digest",
        "semantic_signature",
        "schema_digest",
        "tool_catalog_digest",
        "prompt_template_digest",
        "generator_config_digest",
        "provenance",
    }
)
HIDDEN_REASONING_FIELD_NAMES = frozenset(
    {
        "chain_of_thought",
        "hidden_chain_of_thought",
        "hidden_cot",
        "reasoning_trace",
        "scratchpad",
        "thinking",
    }
)

_AUTHORITY_FIELD_KEYS = frozenset(
    re.sub(r"[^a-z0-9]", "", name) for name in AUTHORITY_FIELD_NAMES
)
_HIDDEN_REASONING_FIELD_KEYS = frozenset(
    re.sub(r"[^a-z0-9]", "", name) for name in HIDDEN_REASONING_FIELD_NAMES
)


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _normalize_field_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]", "", normalized)


def reject_untrusted_authority(value: Any, *, path: str = "payload") -> None:
    """Reject harness authority and hidden reasoning keys recursively."""
    validate_bounded_json(value, path=path)
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalize_field_name(key)
            if normalized in _AUTHORITY_FIELD_KEYS:
                raise ValueError(
                    f"provider payload contains authority field at {path}.{key}"
                )
            if normalized in _HIDDEN_REASONING_FIELD_KEYS:
                raise ValueError(
                    f"provider payload contains hidden reasoning field at {path}.{key}"
                )
            reject_untrusted_authority(item, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            reject_untrusted_authority(item, path=f"{path}[{index}]")


def _reject_hidden_reasoning_text(value: Any) -> Any:
    if value is not None:
        compact = _normalize_field_name(str(value))
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


class FrozenScenarioInput(StrictModel):
    """Trusted, persisted context supplied to roles; never parsed from output."""

    pack_name: PackName
    root_reference: Identifier
    ttl: TTLFacts
    expected_result_digest: Sha256Text
    expected_effect_observed: bool
    output_contract_digest: Sha256Text
    tool_catalog_digest: Sha256Text
    schema_digest: Sha256Text
    prompt_template_digest: Sha256Text
    generator_config_digest: Sha256Text
    scenario_digest: Sha256Text

    @model_validator(mode="after")
    def digest_matches_context(self) -> FrozenScenarioInput:
        expected = self.compute_scenario_digest()
        if self.scenario_digest != expected:
            raise ValueError("scenario_digest does not match frozen scenario context")
        return self

    def compute_scenario_digest(self) -> str:
        return canonical_sha256(
            {
                "pack_name": self.pack_name,
                "root_reference": self.root_reference,
                "ttl": self.ttl,
                "expected_result_digest": self.expected_result_digest,
                "expected_effect_observed": self.expected_effect_observed,
                "output_contract_digest": self.output_contract_digest,
                "tool_catalog_digest": self.tool_catalog_digest,
                "schema_digest": self.schema_digest,
                "prompt_template_digest": self.prompt_template_digest,
                "generator_config_digest": self.generator_config_digest,
            }
        )

    @classmethod
    def mint(
        cls,
        *,
        pack_name: PackName,
        root_reference: str,
        ttl: TTLFacts,
        expected_result_digest: str,
        expected_effect_observed: bool,
        output_contract_digest: str,
        tool_catalog_digest: str,
        schema_digest: str,
        prompt_template_digest: str,
        generator_config_digest: str,
    ) -> FrozenScenarioInput:
        values = {
            "pack_name": pack_name,
            "root_reference": root_reference,
            "ttl": ttl,
            "expected_result_digest": expected_result_digest,
            "expected_effect_observed": expected_effect_observed,
            "output_contract_digest": output_contract_digest,
            "tool_catalog_digest": tool_catalog_digest,
            "schema_digest": schema_digest,
            "prompt_template_digest": prompt_template_digest,
            "generator_config_digest": generator_config_digest,
        }
        return cls(**values, scenario_digest=canonical_sha256(values))

    def blinded_view(self) -> BlindedScenarioInput:
        """Project only model-visible pack and TTL facts, never oracle truth."""

        return BlindedScenarioInput(pack_name=self.pack_name, ttl=self.ttl)


class BlindedScenarioInput(StrictModel):
    """Scenario facts visible to a reviewer, excluding IDs, truth, and digests."""

    pack_name: PackName
    ttl: TTLFacts


class BlindedCandidate(StrictModel):
    """Candidate content visible to reviewers, stripped of authority and lineage."""

    candidate_token: CandidateToken
    scenario: BlindedScenarioInput
    objective: LongText
    observations: list[dict[str, Any]] = Field(max_length=32)
    capabilities: list[Identifier] = Field(max_length=32)
    forbidden_effects: list[ShortText] = Field(max_length=32)
    result: ProposalResultType
    decision_summary: LongText | None = None
    plan_state: LongText | None = None

    @field_validator("observations")
    @classmethod
    def observations_are_bounded(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        validate_bounded_json(value, path="review_observations")
        return value

    @field_validator("capabilities", "forbidden_effects")
    @classmethod
    def text_lists_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError(
                "review capability/effect lists must contain unique values"
            )
        return values

    @field_validator("decision_summary", "plan_state", mode="before")
    @classmethod
    def no_hidden_reasoning(cls, value: Any) -> Any:
        return _reject_hidden_reasoning_text(value)


class BlindedReviewInput(StrictModel):
    """Trusted prompt projection for critic/adjudicator roles."""

    candidates: list[BlindedCandidate] = Field(
        min_length=EXACT_VARIANT_COUNT, max_length=EXACT_VARIANT_COUNT
    )
    rubric: list[ShortText] = Field(min_length=1, max_length=16)

    @field_validator("candidates")
    @classmethod
    def tokens_are_unique(
        cls, values: list[BlindedCandidate]
    ) -> list[BlindedCandidate]:
        if len({value.candidate_token for value in values}) != EXACT_VARIANT_COUNT:
            raise ValueError("candidate_tokens must contain four unique tokens")
        if len({value.scenario.pack_name for value in values}) != 1:
            raise ValueError("blinded candidates must belong to one pack")
        return values

    @property
    def candidate_tokens(self) -> list[str]:
        return [candidate.candidate_token for candidate in self.candidates]


class SeedCandidate(StrictModel):
    """Raw untrusted Grok seed output; intentionally contains no authority."""

    objective: LongText
    observations: list[dict[str, Any]] = Field(max_length=32)
    capabilities: list[Identifier] = Field(max_length=32)
    forbidden_effects: list[ShortText] = Field(max_length=32)
    result: ProposalResultType
    decision_summary: LongText | None = None
    plan_state: LongText | None = None

    @field_validator("observations")
    @classmethod
    def observations_are_bounded(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        validate_bounded_json(value, path="observations")
        return value

    @field_validator("capabilities", "forbidden_effects")
    @classmethod
    def text_lists_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("capability/effect lists must contain unique values")
        return values

    @field_validator("decision_summary", "plan_state", mode="before")
    @classmethod
    def no_hidden_reasoning(cls, value: Any) -> Any:
        return _reject_hidden_reasoning_text(value)


class VariantCandidate(StrictModel):
    """One positional raw mutation; the harness assigns its token after parse."""

    objective: LongText
    observations: list[dict[str, Any]] = Field(max_length=32)
    result: ProposalResultType
    decision_summary: LongText | None = None
    plan_state: LongText | None = None

    @field_validator("observations")
    @classmethod
    def observations_are_bounded(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        validate_bounded_json(value, path="observations")
        return value

    @field_validator("decision_summary", "plan_state", mode="before")
    @classmethod
    def no_hidden_reasoning(cls, value: Any) -> Any:
        return _reject_hidden_reasoning_text(value)


class VariantBatch(StrictModel):
    """Exactly four positional variants, with no provider-controlled IDs."""

    variants: list[VariantCandidate] = Field(
        min_length=EXACT_VARIANT_COUNT, max_length=EXACT_VARIANT_COUNT
    )

    @model_validator(mode="after")
    def variants_are_semantically_unique(self) -> VariantBatch:
        signatures = [
            canonical_sha256(variant.model_dump(mode="json"))
            for variant in self.variants
        ]
        if len(set(signatures)) != EXACT_VARIANT_COUNT:
            raise ValueError("variant batch must contain four unique variants")
        return self


class CriticVerdict(StrictModel):
    candidate_token: CandidateToken
    advisory_verdict: ProposalVerdict
    reason_code: ReasonCode
    summary: ShortText

    @field_validator("summary", mode="before")
    @classmethod
    def no_hidden_reasoning(cls, value: Any) -> Any:
        return _reject_hidden_reasoning_text(value)


class CriticVerdictBatch(StrictModel):
    verdicts: list[CriticVerdict] = Field(
        min_length=EXACT_VARIANT_COUNT, max_length=EXACT_VARIANT_COUNT
    )

    @model_validator(mode="after")
    def tokens_are_unique(self) -> CriticVerdictBatch:
        _assert_unique_tokens([item.candidate_token for item in self.verdicts])
        return self

    def assert_expected_tokens(self, expected_tokens: Sequence[str]) -> None:
        _assert_expected_tokens(
            [item.candidate_token for item in self.verdicts], expected_tokens
        )


class BlindedAdjudicationInput(StrictModel):
    """Only blinded candidates and measured critic disagreements reach adjudication."""

    review: BlindedReviewInput
    disagreements: list[CriticVerdict] = Field(
        min_length=1, max_length=EXACT_VARIANT_COUNT
    )

    @model_validator(mode="after")
    def disagreements_are_unique_review_tokens(self) -> BlindedAdjudicationInput:
        tokens = [item.candidate_token for item in self.disagreements]
        _assert_bounded_unique_tokens(tokens)
        if not set(tokens).issubset(self.review.candidate_tokens):
            raise ValueError("disagreement tokens must reference blinded candidates")
        return self


class AdjudicationVerdict(StrictModel):
    candidate_token: CandidateToken
    advisory_verdict: ProposalVerdict
    reason_code: ReasonCode
    summary: ShortText

    @field_validator("summary", mode="before")
    @classmethod
    def no_hidden_reasoning(cls, value: Any) -> Any:
        return _reject_hidden_reasoning_text(value)


class AdjudicationVerdictBatch(StrictModel):
    verdicts: list[AdjudicationVerdict] = Field(
        min_length=1, max_length=EXACT_VARIANT_COUNT
    )

    @model_validator(mode="after")
    def tokens_are_unique(self) -> AdjudicationVerdictBatch:
        _assert_bounded_unique_tokens([item.candidate_token for item in self.verdicts])
        return self

    def assert_expected_tokens(self, expected_tokens: Sequence[str]) -> None:
        actual = [item.candidate_token for item in self.verdicts]
        _assert_bounded_unique_tokens(actual)
        _assert_bounded_unique_tokens(expected_tokens)
        if set(actual) != set(expected_tokens):
            raise ValueError(
                "verdict candidate tokens do not match measured disagreements"
            )


def _assert_unique_tokens(tokens: Sequence[str]) -> None:
    if len(tokens) != EXACT_VARIANT_COUNT or len(set(tokens)) != EXACT_VARIANT_COUNT:
        raise ValueError(
            "verdict batch must contain exactly four unique candidate tokens"
        )


def _assert_bounded_unique_tokens(tokens: Sequence[str]) -> None:
    if not 1 <= len(tokens) <= EXACT_VARIANT_COUNT or len(set(tokens)) != len(tokens):
        raise ValueError(
            "verdict batch must contain one to four unique candidate tokens"
        )


def _assert_expected_tokens(actual: Sequence[str], expected: Sequence[str]) -> None:
    _assert_unique_tokens(actual)
    _assert_unique_tokens(expected)
    if set(actual) != set(expected):
        raise ValueError("verdict candidate tokens do not match the blinded prompt")


RoleModelT = TypeVar("RoleModelT", bound=BaseModel)


def parse_untrusted_role_payload(
    model: type[RoleModelT], raw: str | bytes | bytearray | Mapping[str, Any]
) -> RoleModelT:
    """The only supported provider-output boundary for campaign roles."""
    if isinstance(raw, Mapping):
        payload = dict(raw)
    elif isinstance(raw, (str, bytes, bytearray)):
        encoded = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        if not encoded or len(encoded) > MAX_RAW_ROLE_PAYLOAD_BYTES:
            raise ValueError("provider payload is empty or exceeds the byte limit")
        try:
            payload = json.loads(
                encoded,
                parse_constant=_reject_non_finite_json,
                object_pairs_hook=_reject_duplicate_object_pairs,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("provider payload must be exactly one JSON value") from exc
    else:
        raise TypeError("provider payload must be JSON text or an object mapping")
    if not isinstance(payload, dict):
        raise ValueError("provider payload must be a JSON object")
    try:
        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "provider payload must contain deterministic JSON values"
        ) from exc
    if not encoded_payload or len(encoded_payload) > MAX_RAW_ROLE_PAYLOAD_BYTES:
        raise ValueError("provider payload is empty or exceeds the byte limit")
    reject_untrusted_authority(payload)
    return model.model_validate(payload)


def parse_seed_candidate(raw: str | bytes | Mapping[str, Any]) -> SeedCandidate:
    return parse_untrusted_role_payload(SeedCandidate, raw)


def parse_variant_batch(raw: str | bytes | Mapping[str, Any]) -> VariantBatch:
    return parse_untrusted_role_payload(VariantBatch, raw)


def parse_critic_batch(raw: str | bytes | Mapping[str, Any]) -> CriticVerdictBatch:
    return parse_untrusted_role_payload(CriticVerdictBatch, raw)


def parse_adjudication_batch(
    raw: str | bytes | Mapping[str, Any],
) -> AdjudicationVerdictBatch:
    return parse_untrusted_role_payload(AdjudicationVerdictBatch, raw)
