"""Wave planning: consume the typed ``next_harvest_request`` from E000n.

The evaluation gate (``evals.needle_gates.harvest_request``) emits a typed,
request-only document naming the weak confusion cells and retention cells,
each bound to the exact semantic root IDs whose evaluations produced it.
This module converts that document into concrete :class:`HarvestRequest`
objects for the next wave:

- every weak cell yields variants generated *from the failing root* (never
  a clone of some unrelated first request), and
- every retention cell with roots yields controlled variants of the
  successful root, so retained behavior keeps fresh coverage.

Planning is pure: it performs no provider calls, no ledger writes, and no
shard writes. The resulting requests still require a Codex-approved
manifest before a live harvester may execute them.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from evals.needle_harvest.contracts import (
    Ceilings,
    GenerationRecipe,
    HarvestRequest,
    ResponseType,
    StrictModel,
)

ACCEPTED_REQUEST_SCHEMA = "needle-next-harvest-request/v1"


class WavePlanError(ValueError):
    """The harvest-request document or template cannot produce a wave."""


class WaveTemplate(StrictModel):
    """Wave-constant facts the evaluation document does not carry.

    One template covers exactly one function pack, contract, and tool
    catalog — matching the one-pack-per-wave rule. Per-root facts (the
    exact model-visible objective and the leakage group binding the root
    with all its siblings) come from committed, reviewed maps.
    """

    function_pack_id: str
    function_contract_digest: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    tool_catalog_digest: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    response_type: ResponseType = "answer"
    base_recipe: GenerationRecipe
    seed: int = Field(ge=0)
    ttl_seconds: float = Field(gt=0)
    issued_at: float = Field(ge=0)
    ceilings: Ceilings
    authorized_effects: tuple[str, ...] = (
        "provider_call",
        "ledger_append",
        "shard_write",
    )
    root_objectives: dict[str, str]
    root_leakage_groups: dict[str, str]


def _cell_entries(doc: dict[str, Any], key: str) -> list[dict[str, Any]]:
    entries = doc.get(key, [])
    if not isinstance(entries, list):
        raise WavePlanError(f"{key} must be a list of cell entries")
    return sorted(
        (dict(e) for e in entries if isinstance(e, dict)),
        key=lambda e: (str(e.get("cell", "")), str(e.get("arm", ""))),
    )


def plan_wave_from_harvest_request(
    doc: dict[str, Any], template: WaveTemplate
) -> list[HarvestRequest]:
    """Typed requests for the next wave, derived from evaluation evidence.

    Fails closed on anything that is not a genuine request-only harvest
    request document. Weak-cell variants target the failing root; retention
    variants target the retained root under a controlled recipe. A root
    referenced by the document but absent from the template's objective or
    leakage-group maps is an error, never a silent skip.
    """
    if doc.get("schema") != ACCEPTED_REQUEST_SCHEMA:
        raise WavePlanError(
            f"unrecognized harvest-request schema {doc.get('schema')!r}"
        )
    if doc.get("request_only") is not True or doc.get("authorizes_generation"):
        raise WavePlanError(
            "document is not request-only — refusing to plan from it"
        )
    campaign_id = str(doc.get("campaign_id", ""))
    source_dataset_id = str(doc.get("source_dataset_id", ""))
    target_dataset_id = str(doc.get("target_dataset_id", ""))
    if not campaign_id or not source_dataset_id or not target_dataset_id:
        raise WavePlanError("document lacks campaign/source/target identifiers")

    plans: list[tuple[str, str, str]] = []  # (root_id, cell, kind)
    for entry in _cell_entries(doc, "weak_confusion_cells"):
        for root_id in entry.get("root_ids", []):
            plans.append((str(root_id), str(entry.get("cell", "")), "weak"))
    for entry in _cell_entries(doc, "retention_cells"):
        for root_id in entry.get("root_ids", []):
            plans.append((str(root_id), str(entry.get("cell", "")), "retention"))

    requests: list[HarvestRequest] = []
    seen: set[tuple[str, str]] = set()
    for root_id, cell, kind in plans:
        objective = template.root_objectives.get(root_id)
        leakage_group = template.root_leakage_groups.get(root_id)
        if objective is None or leakage_group is None:
            raise WavePlanError(
                f"root {root_id!r} from the harvest request has no committed "
                "objective/leakage-group binding — fail closed"
            )
        if kind == "weak":
            recipe_id = f"{template.base_recipe.recipe_id}-weak-{cell}-{root_id}"
            prompt_surface = "confusion-targeted"
        else:
            recipe_id = f"{template.base_recipe.recipe_id}-retain-{cell}-{root_id}"
            prompt_surface = "retention-controlled"
        if (root_id, recipe_id) in seen:
            continue
        seen.add((root_id, recipe_id))
        recipe = template.base_recipe.model_copy(
            update={"recipe_id": recipe_id, "prompt_surface": prompt_surface}
        )
        requests.append(
            HarvestRequest(
                campaign_id=campaign_id,
                source_dataset_id=source_dataset_id,
                target_dataset_id=target_dataset_id,
                function_pack_id=template.function_pack_id,
                semantic_root_id=root_id,
                leakage_group_id=leakage_group,
                function_contract_digest=template.function_contract_digest,
                model_visible_objective=objective,
                ttl_seconds=template.ttl_seconds,
                issued_at=template.issued_at,
                expires_at=template.issued_at + template.ttl_seconds,
                authorized_effects=template.authorized_effects,
                tool_catalog_digest=template.tool_catalog_digest,
                recipe=recipe,
                seed=template.seed,
                response_type=template.response_type,
                ceilings=template.ceilings,
            )
        )
    if not requests:
        raise WavePlanError(
            "harvest request document yielded no plannable roots"
        )
    return requests
