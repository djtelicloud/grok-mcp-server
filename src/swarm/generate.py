"""Mutation generation seam — the one place the swarm calls a model.

Generation is pinned to the $0 CLI subscription plane with same-plane failure
semantics.  A swarm must never discover that it used the metered API plane only
after the charge occurred. This function is the single injection point tests
mock — the engine never imports run_agent_turn directly.
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
    """Raised defensively if a swarm generation is non-CLI or charged."""


async def generate_mutation(
    prompt: str,
    system_prompt: str,
    *,
    remaining_budget_usd: float,
    session: Optional[str] = None,
) -> GenerationResult:
    """One toolless completion, strictly on the CLI subscription plane."""
    _ = remaining_budget_usd  # retained in the injectable engine contract
    from ..utils import run_agent_turn

    layer = await run_agent_turn(
        prompt=prompt,
        system_prompt=system_prompt,
        mode="fast",
        enable_agentic=False,
        plane="cli",
        fallback_policy="same_plane",
        session=session,
        caller="swarm",
    )
    plane = str(getattr(layer, "plane", "") or "unknown")
    cost = float(getattr(layer, "cost_usd", 0.0) or 0.0)
    if plane not in {"CLI", "CLI-Fallback"} or cost > 0:
        raise BudgetExceeded(
            "swarm generation refused a non-CLI or charged result "
            f"(plane={plane!r}, cost=${cost:.4f})"
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
