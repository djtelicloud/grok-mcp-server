"""Adaptive data harvester for the Needle campaign (H000n generations).

Companion to :mod:`evals.needle_gates`: evaluation (E000n) emits a typed,
request-only ``next_harvest_request``; this package turns an *authorized*
harvest into the next candidate dataset (D000n+1) and then stops for Codex
review. It never trains, never touches sealed evaluation data, and never
modifies the dataset currently being trained.

Architecture donor: the GenFuncAgentixAI swarm_evolver/lora_forge tournament
harvester, studied read-only. Reused ideas: same exact root fanned out to
multiple donors, mechanical evaluation before model judging, failure
aggregation into targeted variants, adaptive allocation, bounded concurrent
lanes, plateau-based early stopping, explicit success and failure samples.
Deliberately not copied: git-note concurrent writes, random UUID dataset
splitting, positional pairing of unrelated rows, winner-versus-worst as the
default, provider/transport failures as negative answers, raw hidden
chain-of-thought, regex/AST scoring as semantic authority, workflow-owned
API keys, weak execution isolation, unbounded loops.

Provider boundary: everything flows through the provider-neutral transport
contract in :mod:`evals.needle_harvest.transport`. This package reads no
environment variables, no IDE configuration, and no credential files — the
UniGrok MCP server owns credentials. Until that provider-neutral interface
lands, only the typed interface and the deterministic mock transport exist.
"""

from evals.needle_harvest.contracts import (
    Ceilings,
    GenerationRecipe,
    HarvestManifest,
    HarvestRequest,
)
from evals.needle_harvest.harvester import HarvestSession
from evals.needle_harvest.ledger import AttemptLedger
from evals.needle_harvest.transport import (
    MockTransport,
    ProviderModel,
    TransportRequest,
    TransportResult,
)
from evals.needle_harvest.truth import (
    EpisodeOutcome,
    ProposalVerdict,
    TransportStatus,
)

__all__ = [
    "AttemptLedger",
    "Ceilings",
    "EpisodeOutcome",
    "GenerationRecipe",
    "HarvestManifest",
    "HarvestRequest",
    "HarvestSession",
    "MockTransport",
    "ProposalVerdict",
    "ProviderModel",
    "TransportRequest",
    "TransportResult",
    "TransportStatus",
]
