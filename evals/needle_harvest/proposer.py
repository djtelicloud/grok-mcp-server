"""Shadow-mode swarm recipe proposer.

Adapted (architecture only) from the donor system's swarm evolver: adaptive
donor/recipe/temperature/sampling allocation driven by observed outcomes.
It is a *proposer*, never a correctness authority — its output is a list of
bounded recipe deltas for the next harvesting wave, and the truth hierarchy
in :mod:`evals.needle_harvest.truth` remains the only judge of candidates.

Signals it may react to: underperforming confusion cells, retention
variants of strong cells, donor/judge diversity, vocabulary and
prompt-surface variation, TTL pressure, observation-history variation,
recovery/repair arcs, and tool-catalog drift.

Stopping: accepted-data yield plateau, validation-improvement plateau, or
any expired budget — whichever comes first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evals.needle_harvest.contracts import GenerationRecipe

PROMPT_SURFACES = (
    "baseline",
    "vocab-shift",
    "observation-history-long",
    "observation-history-short",
    "recovery-arc",
)


@dataclass(frozen=True)
class WaveObservation:
    """What one completed harvesting wave looked like to the proposer."""

    wave_index: int
    accepted: int
    attempted: int
    validation_score: float
    weak_confusion_cells: tuple[str, ...] = ()
    retention_cells: tuple[str, ...] = ()
    donor_acceptance: dict[str, float] = field(default_factory=dict)

    @property
    def yield_rate(self) -> float:
        return self.accepted / self.attempted if self.attempted else 0.0


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str = ""


class ShadowProposer:
    """Bounded, deterministic recipe proposer (mock/shadow mode only)."""

    def __init__(
        self,
        *,
        base_recipe: GenerationRecipe,
        max_waves: int,
        yield_plateau_epsilon: float = 0.02,
        validation_plateau_epsilon: float = 0.005,
        plateau_patience: int = 2,
        max_temperature_delta: float = 0.15,
    ) -> None:
        self.base_recipe = base_recipe
        self.max_waves = max_waves
        self.yield_plateau_epsilon = yield_plateau_epsilon
        self.validation_plateau_epsilon = validation_plateau_epsilon
        self.plateau_patience = plateau_patience
        self.max_temperature_delta = max_temperature_delta
        self._history: list[WaveObservation] = []

    def observe(self, observation: WaveObservation) -> None:
        self._history.append(observation)

    # ------------------------------------------------------------- stopping

    def should_stop(self) -> StopDecision:
        if len(self._history) >= self.max_waves:
            return StopDecision(True, "wave budget expired")
        if len(self._history) > self.plateau_patience:
            recent = self._history[-(self.plateau_patience + 1) :]
            yield_gains = [
                b.yield_rate - a.yield_rate for a, b in zip(recent, recent[1:])
            ]
            if all(g <= self.yield_plateau_epsilon for g in yield_gains):
                return StopDecision(True, "accepted-data yield plateaued")
            validation_gains = [
                b.validation_score - a.validation_score
                for a, b in zip(recent, recent[1:])
            ]
            if all(g <= self.validation_plateau_epsilon for g in validation_gains):
                return StopDecision(True, "validation improvement plateaued")
        return StopDecision(False)

    # ------------------------------------------------------------ proposals

    def propose(self) -> list[GenerationRecipe]:
        """Bounded recipe deltas for the next wave (advisory only)."""
        if not self._history:
            return [self.base_recipe]
        last = self._history[-1]
        proposals: list[GenerationRecipe] = []

        # Weak confusion cells get focused variants under TTL pressure and a
        # slightly hotter temperature (bounded).
        for cell in sorted(last.weak_confusion_cells):
            proposals.append(
                self._delta(
                    recipe_id=f"{self.base_recipe.recipe_id}-confusion-{cell}",
                    temperature_delta=min(0.1, self.max_temperature_delta),
                    prompt_surface="vocab-shift",
                    ttl_pressure=0.8,
                )
            )

        # Retention cells get controlled variants of successes (cooler).
        for cell in sorted(last.retention_cells):
            proposals.append(
                self._delta(
                    recipe_id=f"{self.base_recipe.recipe_id}-retention-{cell}",
                    temperature_delta=-min(0.1, self.max_temperature_delta),
                    prompt_surface="observation-history-long",
                    ttl_pressure=0.0,
                )
            )

        # Donor diversity: shift allocation toward accepted donors while
        # keeping every donor sampled (floor share), deterministically.
        if last.donor_acceptance:
            floor = 0.1
            keys = sorted(last.donor_acceptance)
            raw = {k: max(last.donor_acceptance[k], floor) for k in keys}
            total = sum(raw.values())
            allocation = {k: round(raw[k] / total, 4) for k in keys}
            proposals.append(
                self.base_recipe.model_copy(
                    update={
                        "recipe_id": f"{self.base_recipe.recipe_id}-donor-mix",
                        "donor_allocation": allocation,
                    }
                )
            )

        # Recovery/repair arc + tool-catalog drift probes (fixed, bounded).
        proposals.append(
            self._delta(
                recipe_id=f"{self.base_recipe.recipe_id}-recovery-arc",
                temperature_delta=0.0,
                prompt_surface="recovery-arc",
                ttl_pressure=0.0,
                recovery_arc=True,
            )
        )
        proposals.append(
            self._delta(
                recipe_id=f"{self.base_recipe.recipe_id}-catalog-drift",
                temperature_delta=0.0,
                prompt_surface="baseline",
                ttl_pressure=0.0,
                tool_catalog_drift=True,
            )
        )
        return proposals

    def _delta(
        self,
        *,
        recipe_id: str,
        temperature_delta: float,
        prompt_surface: str,
        ttl_pressure: float,
        recovery_arc: bool = False,
        tool_catalog_drift: bool = False,
    ) -> GenerationRecipe:
        if abs(temperature_delta) > self.max_temperature_delta:
            raise ValueError("proposer attempted an unbounded temperature change")
        if prompt_surface not in PROMPT_SURFACES:
            raise ValueError(f"unknown prompt surface {prompt_surface!r}")
        temperature = min(1.5, max(0.0, self.base_recipe.temperature + temperature_delta))
        return self.base_recipe.model_copy(
            update={
                "recipe_id": recipe_id,
                "temperature": round(temperature, 3),
                "prompt_surface": prompt_surface,
                "ttl_pressure": ttl_pressure,
                "recovery_arc": recovery_arc,
                "tool_catalog_drift": tool_catalog_drift,
            }
        )
