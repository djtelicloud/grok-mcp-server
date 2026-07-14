"""Typed contracts for adaptive harvesting.

Every provider-facing unit of work is a :class:`HarvestRequest`. Its fields
are strict (``extra="forbid"``): unknown fields — including any hidden
chain-of-thought side channel — are rejected at parse depth. Attempt and
effect identifiers are deterministic functions of request content, so a
resumed harvest reuses the same work keys instead of duplicating provider
calls or effects.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_SCHEMA = "needle-harvest-request/v1"
MANIFEST_SCHEMA = "needle-harvest-manifest/v1"

# The only effects a harvest may ever be authorized to perform. Training and
# sealed evaluation are structurally impossible to authorize here.
ALLOWED_EFFECTS = frozenset({"provider_call", "ledger_append", "shard_write"})

# Model-visible response surfaces. Deliberate, visible, redacted planning
# fields only — no raw chain-of-thought aliases exist in this vocabulary.
ResponseType = Literal["answer", "decision_summary", "plan_state"]


def deterministic_id(prefix: str, *parts: str) -> str:
    """Stable content-derived identifier (never a random UUID)."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:40]}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ceilings(StrictModel):
    """Hard budgets. Retries and adjudication count against these."""

    max_provider_calls: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_seconds: float = Field(gt=0)
    max_cost_usd: float = Field(gt=0)
    max_roots: int = Field(gt=0)


class GenerationRecipe(StrictModel):
    """How candidates are sampled for one request."""

    recipe_id: str
    donor_allocation: dict[str, float]  # provider-neutral donor key -> weight
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    samples_per_donor: int = Field(gt=0, le=16)
    prompt_surface: str = "default"
    ttl_pressure: float = Field(ge=0.0, le=1.0, default=0.0)
    observation_history_variant: str = "none"
    recovery_arc: bool = False
    tool_catalog_drift: bool = False

    @model_validator(mode="after")
    def _donors_normalized(self) -> GenerationRecipe:
        if not self.donor_allocation:
            raise ValueError("recipe needs at least one donor")
        if any(w <= 0 for w in self.donor_allocation.values()):
            raise ValueError("donor weights must be positive")
        return self


class HarvestRequest(StrictModel):
    """One semantic root's worth of authorized harvesting work."""

    schema_id: Literal["needle-harvest-request/v1"] = CONTRACT_SCHEMA
    campaign_id: str
    source_dataset_id: str
    target_dataset_id: str
    function_pack_id: str
    semantic_root_id: str
    leakage_group_id: str
    function_contract_digest: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    model_visible_objective: str
    ttl_seconds: float = Field(gt=0)
    issued_at: float = Field(ge=0)
    expires_at: float = Field(gt=0)
    authorized_effects: tuple[str, ...]
    tool_catalog_digest: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    recipe: GenerationRecipe
    seed: int = Field(ge=0)
    response_type: ResponseType = "answer"
    ceilings: Ceilings

    @model_validator(mode="after")
    def _validate(self) -> HarvestRequest:
        if self.source_dataset_id == self.target_dataset_id:
            raise ValueError(
                "target dataset must differ from source dataset "
                "(harvesting never modifies the dataset being trained)"
            )
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        illegal = set(self.authorized_effects) - ALLOWED_EFFECTS
        if illegal:
            raise ValueError(
                f"effects {sorted(illegal)} can never be authorized for harvesting "
                f"(allowed: {sorted(ALLOWED_EFFECTS)})"
            )
        if not self.authorized_effects:
            raise ValueError("request authorizes no effects")
        return self

    def work_key(self, donor: str, sample_index: int) -> str:
        """Stable key for one provider call — identical across resumes."""
        return deterministic_id(
            "work",
            self.campaign_id,
            self.target_dataset_id,
            self.function_pack_id,
            self.semantic_root_id,
            self.function_contract_digest,
            self.tool_catalog_digest,
            self.recipe.recipe_id,
            str(self.seed),
            donor,
            str(sample_index),
        )

    def attempt_id(self, donor: str, sample_index: int) -> str:
        return deterministic_id("attempt", self.work_key(donor, sample_index))

    def effect_id(self, donor: str, sample_index: int) -> str:
        return deterministic_id("effect", self.work_key(donor, sample_index))

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


class HarvestManifest(StrictModel):
    """Codex-approved authorization to run a live harvest.

    Live mode refuses to start unless this manifest exists, names the
    reviewed head, is approved by Codex, and enables harvesting. Mock mode
    never requires one (and never performs provider calls).
    """

    schema_id: Literal["needle-harvest-manifest/v1"] = MANIFEST_SCHEMA
    campaign_id: str
    approved_by: str
    approved_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    harvesting_enabled: bool
    source_dataset_id: str
    target_dataset_id: str
    active_training_dataset_id: str
    approved_dataset_ids: tuple[str, ...]
    provider_allowlist: tuple[str, ...]  # provider-neutral donor keys
    plane_allowlist: tuple[str, ...]
    ceilings: Ceilings

    def authorizes_live(self) -> bool:
        return self.harvesting_enabled and self.approved_by.strip().lower() == "codex"
