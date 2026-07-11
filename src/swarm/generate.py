"""Mutation generation seam — the one place the swarm calls a model.

Generation prefers the $0 CLI subscription plane (cli_first), so a swarm on an
authenticated CLI plane costs nothing; an API-plane fallback is metered and
therefore reserves against the task budget BEFORE the call (the semantic-evals
pattern) and settles the actual cost after. This function is the single
injection point tests mock — the engine never imports run_agent_turn directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerationResult:
    text: str
    plane: str
    cost_usd: float
    finish_reason: str


class BudgetExceeded(RuntimeError):
    """Raised when a metered (API-plane) generation would exceed the task's
    remaining budget. CLI-plane generation is $0 and never raises."""


async def generate_mutation(
    prompt: str,
    system_prompt: str,
    *,
    remaining_budget_usd: float,
    per_call_reserve_usd: float = 0.02,
    session: Optional[str] = None,
) -> GenerationResult:
    """One toolless completion. Rides cli_first routing; if it lands on the
    metered API plane it must fit the remaining budget."""
    from ..utils import run_agent_turn

    layer = await run_agent_turn(
        prompt=prompt,
        system_prompt=system_prompt,
        mode="fast",
        enable_agentic=False,
        plane="auto",
        session=session,
        caller="swarm",
    )
    plane = str(getattr(layer, "plane", "") or "unknown")
    cost = float(getattr(layer, "cost_usd", 0.0) or 0.0)
    # Metered plane must respect the task budget; the CLI plane is free.
    if cost > 0 and cost > remaining_budget_usd:
        raise BudgetExceeded(
            f"API-plane generation cost ${cost:.4f} exceeds remaining "
            f"budget ${remaining_budget_usd:.4f}"
        )
    return GenerationResult(
        text=str(getattr(layer, "generation", "") or ""),
        plane=plane,
        cost_usd=cost,
        finish_reason=str(getattr(layer, "finish_reason", "") or "unknown"),
    )
