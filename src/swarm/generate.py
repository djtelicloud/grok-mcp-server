"""Mutation generation seam — the one place the swarm calls a model.

Generation is pinned to the $0 CLI subscription plane with same-plane failure
semantics.  A swarm must never discover that it used the metered API plane only
after the charge occurred. This function is the single injection point tests
mock — the engine never imports run_agent_turn directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerationResult:
    text: str
    plane: str
    cost_usd: float
    finish_reason: str


class BudgetExceeded(RuntimeError):
    """Raised defensively if a swarm generation is non-CLI or charged."""


async def generate_mutation(
    prompt: str,
    system_prompt: str,
    *,
    remaining_budget_usd: float,
    session: Optional[str] = None,
    model_provider: Optional[str] = None,
) -> GenerationResult:
    """One toolless completion, supporting cross-model routing if model_provider is set."""
    _ = remaining_budget_usd  # retained in the injectable engine contract
    from ..utils import run_agent_turn
    
    # If a specific provider is requested, use auto plane routing. Otherwise enforce free CLI.
    requested_plane = "auto" if model_provider else "cli"
    fallback = "cross_plane" if model_provider else "same_plane"

    layer = await run_agent_turn(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model_provider,
        mode="fast" if not model_provider else "auto",
        enable_agentic=False,
        plane=requested_plane,
        fallback_policy=fallback,
        session=session,
        caller="swarm",
        cli_no_plan=True,
        cli_verbatim=True,
        cli_allowed_tools="",
        cli_isolated=True,
    )
    plane = str(getattr(layer, "plane", "") or "unknown")
    cost = float(getattr(layer, "cost_usd", 0.0) or 0.0)
    
    # If no provider was specified, strictly enforce $0 CLI contract
    if not model_provider:
        if plane not in {"CLI", "CLI-Fallback"} or not math.isfinite(cost) or cost != 0.0:
            raise BudgetExceeded(
                "swarm generation refused a non-CLI or charged result "
                f"(plane={plane!r}, cost=${cost:.4f})"
            )
    else:
        if cost > remaining_budget_usd:
            raise BudgetExceeded(
                f"swarm generation exceeded budget (cost=${cost:.4f}, remaining=${remaining_budget_usd:.4f})"
            )
    if str(getattr(layer, "finish_reason", "") or "") not in {
        "final_answer", "fallback"
    }:
        raise RuntimeError("CLI swarm generation did not produce a usable answer")
    return GenerationResult(
        text=str(getattr(layer, "generation", "") or ""),
        plane=plane,
        cost_usd=cost,
        finish_reason=str(getattr(layer, "finish_reason", "") or "unknown"),
    )
