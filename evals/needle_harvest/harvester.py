"""Harvest session orchestrator.

Runs one authorized harvesting wave: fans the same exact root out to
multiple donors through the provider-neutral transport, applies mechanical
evaluation before any model judging, records every attempt in the
append-only ledger, aggregates failures into targeted variant *requests*
(never auto-executed), and assembles the next candidate dataset shards.
Then it stops for Codex review.

Safety properties enforced here:

- Live mode refuses to start without a Codex-approved manifest bound to the
  reviewed head SHA; mock mode performs zero network I/O by construction.
- The active training dataset and any approved (frozen) dataset can never
  be a harvest target.
- Work keys are deterministic, and completed work keys found in the ledger
  are skipped on resume — a resumed harvest cannot duplicate provider calls
  or effects.
- Retries and adjudication calls charge the same hard budget meter as
  first attempts.
- Provider/model/plane receipt mismatches against the runtime-discovered
  catalog (and, live, the manifest allowlists) quarantine the candidate.
- Lanes are bounded: work is dispatched in deterministic batches of at most
  ``max_concurrent_lanes`` items.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.needle_harvest.contracts import Ceilings, HarvestManifest, HarvestRequest
from evals.needle_harvest.dataset import CandidateSample, DatasetBuilder
from evals.needle_harvest.ledger import AttemptLedger
from evals.needle_harvest.proposer import ShadowProposer, WaveObservation
from evals.needle_harvest.transport import Transport, TransportRequest
from evals.needle_harvest.truth import (
    EpisodeOutcome,
    JudgeVote,
    OracleResult,
    ProposalVerdict,
    TransportStatus,
    VerifierResult,
    evaluate_candidate,
)

REPORT_SCHEMA = "needle-harvest-report/v1"

_RETRYABLE = frozenset({"TIMEOUT", "ERROR", "EMPTY"})


class HarvestAuthorizationError(RuntimeError):
    """Raised when a session may not start or a request may not run."""


class BudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class MechanicalCheck:
    """Result of mechanical, pre-judge evaluation of one candidate."""

    artifact_present: bool
    oracle: OracleResult | None = None
    score: float = 0.0
    confusion_cell: str = ""


MechanicalFn = Callable[[HarvestRequest, str], MechanicalCheck]
VerifierFn = Callable[[HarvestRequest, str], VerifierResult | None]
JudgeFn = Callable[[str, str], tuple[JudgeVote, ...]]


class BudgetMeter:
    """Hard ceilings. Every provider call — first attempt, retry, or
    adjudication — charges the same meter."""

    def __init__(self, ceilings: Ceilings) -> None:
        self.ceilings = ceilings
        self.provider_calls = 0
        self.retries = 0

    def can_call(self) -> bool:
        return self.provider_calls < self.ceilings.max_provider_calls

    def charge_call(self) -> None:
        if not self.can_call():
            raise BudgetExhausted("max_provider_calls ceiling reached")
        self.provider_calls += 1

    def can_retry(self) -> bool:
        return self.retries < self.ceilings.max_retries and self.can_call()

    def charge_retry(self) -> None:
        if self.retries >= self.ceilings.max_retries:
            raise BudgetExhausted("max_retries ceiling reached")
        self.retries += 1
        self.charge_call()


@dataclass(frozen=True)
class _WorkItem:
    request: HarvestRequest
    donor_key: str
    sample_index: int
    work_key: str


class HarvestSession:
    """One bounded harvesting wave for a single function pack."""

    def __init__(
        self,
        *,
        mode: str,
        transport: Transport,
        ledger: AttemptLedger,
        active_training_dataset_id: str,
        mechanical_fn: MechanicalFn,
        now_fn: Callable[[], float],
        manifest: HarvestManifest | None = None,
        current_head_sha: str | None = None,
        ceilings: Ceilings | None = None,
        approved_dataset_ids: tuple[str, ...] = (),
        verifier_fn: VerifierFn | None = None,
        judge_fn: JudgeFn | None = None,
        adjudicate_fn: JudgeFn | None = None,
        max_concurrent_lanes: int = 4,
        proposer: ShadowProposer | None = None,
    ) -> None:
        if mode not in ("mock", "live"):
            raise HarvestAuthorizationError(f"unknown mode {mode!r}")
        if mode == "live":
            if manifest is None:
                raise HarvestAuthorizationError(
                    "live harvesting refused: no Codex-approved harvest manifest"
                )
            if not manifest.authorizes_live():
                raise HarvestAuthorizationError(
                    "live harvesting refused: manifest is not Codex-approved "
                    "or harvesting is disabled"
                )
            if current_head_sha != manifest.approved_head_sha:
                raise HarvestAuthorizationError(
                    "live harvesting refused: current head does not match the "
                    "manifest's approved head SHA (fail closed)"
                )
            if manifest.active_training_dataset_id != active_training_dataset_id:
                raise HarvestAuthorizationError(
                    "live harvesting refused: manifest and session disagree on "
                    "the active training dataset"
                )
            self.ceilings = manifest.ceilings
            self.approved_dataset_ids = tuple(manifest.approved_dataset_ids)
        else:
            if ceilings is None:
                raise HarvestAuthorizationError("mock mode requires explicit ceilings")
            self.ceilings = ceilings
            self.approved_dataset_ids = tuple(approved_dataset_ids)
        if not (1 <= max_concurrent_lanes <= 64):
            raise HarvestAuthorizationError("max_concurrent_lanes must be in [1, 64]")
        self.mode = mode
        self.transport = transport
        self.ledger = ledger
        self.active_training_dataset_id = active_training_dataset_id
        self.manifest = manifest
        self.mechanical_fn = mechanical_fn
        self.verifier_fn = verifier_fn
        self.judge_fn = judge_fn
        self.adjudicate_fn = adjudicate_fn
        self.now_fn = now_fn
        self.max_concurrent_lanes = max_concurrent_lanes
        self.proposer = proposer

    # ------------------------------------------------------------- guards

    def _guard_request(self, request: HarvestRequest) -> None:
        if request.target_dataset_id == self.active_training_dataset_id:
            raise HarvestAuthorizationError(
                "harvesting must never modify the dataset currently being "
                f"trained ({self.active_training_dataset_id})"
            )
        if request.target_dataset_id in self.approved_dataset_ids:
            raise HarvestAuthorizationError(
                f"dataset {request.target_dataset_id} is approved/frozen; "
                "harvest into the next candidate generation instead"
            )
        if self.mode == "live" and self.manifest is not None:
            if request.campaign_id != self.manifest.campaign_id:
                raise HarvestAuthorizationError(
                    "request campaign does not match the approved manifest"
                )
            if request.target_dataset_id != self.manifest.target_dataset_id:
                raise HarvestAuthorizationError(
                    "request target dataset does not match the approved manifest"
                )

    # ---------------------------------------------------------------- run

    def run(
        self,
        requests: Sequence[HarvestRequest],
        *,
        shard_dir: Path | None = None,
        validation_score: float = 0.0,
        wave_index: int = 0,
    ) -> dict:
        if not requests:
            raise HarvestAuthorizationError("no harvest requests supplied")
        packs = {r.function_pack_id for r in requests}
        if len(packs) > 1:
            raise HarvestAuthorizationError(
                f"cross-function mixing rejected: one wave, one pack (got {sorted(packs)})"
            )
        if len(requests) > self.ceilings.max_roots:
            raise HarvestAuthorizationError(
                f"{len(requests)} roots exceed the max_roots ceiling "
                f"({self.ceilings.max_roots})"
            )
        for request in requests:
            self._guard_request(request)

        meter = BudgetMeter(self.ceilings)
        catalog = {model.donor_key: model for model in self.transport.discover()}
        completed = self.ledger.completed_work_keys()
        builder = DatasetBuilder(
            function_pack_id=next(iter(packs)),
            target_dataset_id=requests[0].target_dataset_id,
            approved_dataset_ids=self.approved_dataset_ids,
        )

        work_items = self._plan_work(requests)
        counts = {
            "planned": len(work_items),
            "skipped_resume": 0,
            "accepted": 0,
            "verified_failure": 0,
            "quarantined": 0,
            "transport_failure": 0,
            "expired": 0,
            "budget_stopped": 0,
        }
        failed_cells: list[str] = []
        donor_accepts: dict[str, list[int]] = {}
        budget_stop = False

        # Bounded lanes: deterministic batches of at most
        # ``max_concurrent_lanes`` items. The transport contract is
        # synchronous here; a live async transport applies the same bound.
        for batch_start in range(0, len(work_items), self.max_concurrent_lanes):
            if budget_stop:
                break
            batch = work_items[batch_start : batch_start + self.max_concurrent_lanes]
            for item in batch:
                if item.work_key in completed:
                    counts["skipped_resume"] += 1
                    continue
                if item.request.expired(self.now_fn()):
                    counts["expired"] += 1
                    self.ledger.append(
                        work_key=item.work_key,
                        attempt_id=item.request.attempt_id(
                            item.donor_key, item.sample_index
                        ),
                        effect_id=item.request.effect_id(
                            item.donor_key, item.sample_index
                        ),
                        status="EXPIRED",
                        detail="request TTL expired before dispatch (fail closed)",
                    )
                    continue
                if not meter.can_call():
                    counts["budget_stopped"] += 1
                    self.ledger.append(
                        work_key=item.work_key,
                        attempt_id=item.request.attempt_id(
                            item.donor_key, item.sample_index
                        ),
                        effect_id=item.request.effect_id(
                            item.donor_key, item.sample_index
                        ),
                        status="BUDGET_EXHAUSTED",
                        detail="provider-call ceiling reached before dispatch",
                    )
                    budget_stop = True
                    break
                outcome, cell = self._process(item, meter, catalog, builder)
                donor_accepts.setdefault(item.donor_key, []).append(
                    1 if outcome == "ACCEPTED" else 0
                )
                if outcome == "ACCEPTED":
                    counts["accepted"] += 1
                elif outcome == "REJECTED":
                    counts["verified_failure"] += 1
                elif outcome == "QUARANTINED":
                    counts["quarantined"] += 1
                elif outcome == "TRANSPORT_FAILURE":
                    counts["transport_failure"] += 1
                elif outcome == "EXPIRED":
                    counts["expired"] += 1
                if outcome == "REJECTED" and cell:
                    failed_cells.append(cell)

        report = self._build_report(
            requests=list(requests),
            counts=counts,
            meter=meter,
            builder=builder,
            failed_cells=failed_cells,
            donor_accepts=donor_accepts,
            shard_dir=shard_dir,
            validation_score=validation_score,
            wave_index=wave_index,
        )
        return report

    def _plan_work(self, requests: Sequence[HarvestRequest]) -> list[_WorkItem]:
        items: list[_WorkItem] = []
        for request in requests:
            # The same exact root goes to every allocated donor.
            for donor_key in sorted(request.recipe.donor_allocation):
                for sample_index in range(request.recipe.samples_per_donor):
                    items.append(
                        _WorkItem(
                            request=request,
                            donor_key=donor_key,
                            sample_index=sample_index,
                            work_key=request.work_key(donor_key, sample_index),
                        )
                    )
        items.sort(key=lambda item: item.work_key)
        return items

    # ------------------------------------------------------------ process

    def _process(
        self,
        item: _WorkItem,
        meter: BudgetMeter,
        catalog: dict,
        builder: DatasetBuilder,
    ) -> tuple[str, str]:
        request, donor_key = item.request, item.donor_key
        attempt_id = request.attempt_id(donor_key, item.sample_index)
        effect_id = request.effect_id(donor_key, item.sample_index)

        meter.charge_call()
        result = self.transport.call(
            TransportRequest(
                work_key=item.work_key,
                donor_key=donor_key,
                objective=request.model_visible_objective,
                temperature=request.recipe.temperature,
                top_p=request.recipe.top_p,
                seed=request.seed,
                max_tokens=min(request.ceilings.max_tokens, 4096),
            )
        )

        retry_index = 0
        while result.transport_status in _RETRYABLE and meter.can_retry():
            retry_index += 1
            meter.charge_retry()
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=f"{attempt_id}-retry{retry_index}",
                effect_id=effect_id,
                status="RETRIED",
                transport_status=result.transport_status,
                detail=f"transport {result.transport_status}; retry {retry_index}",
            )
            result = self.transport.call(
                TransportRequest(
                    work_key=item.work_key,
                    donor_key=donor_key,
                    objective=request.model_visible_objective,
                    temperature=request.recipe.temperature,
                    top_p=request.recipe.top_p,
                    seed=request.seed,
                    max_tokens=min(request.ceilings.max_tokens, 4096),
                )
            )

        transport_status = TransportStatus(result.transport_status)
        if transport_status is not TransportStatus.OK:
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=attempt_id,
                effect_id=effect_id,
                status="TRANSPORT_FAILURE",
                transport_status=transport_status.value,
                episode_outcome=EpisodeOutcome.TRANSPORT_FAILURE.value,
                detail="infrastructure failure — recorded, never a negative answer",
            )
            return "TRANSPORT_FAILURE", ""

        # Receipt validation against the runtime-discovered catalog and, in
        # live mode, the manifest allowlists. Mismatch fails closed.
        receipt = result.receipt
        model = catalog.get(donor_key)
        receipt_problem = ""
        if receipt is None or model is None:
            receipt_problem = "missing provider receipt or unknown donor"
        elif (
            receipt.provider != model.provider
            or receipt.model_id != model.model_id
            or receipt.plane != model.plane
        ):
            receipt_problem = "receipt does not match discovered catalog identity"
        elif self.mode == "live" and self.manifest is not None:
            if receipt.provider not in self.manifest.provider_allowlist:
                receipt_problem = "provider not in approved allowlist"
            elif receipt.plane not in self.manifest.plane_allowlist:
                receipt_problem = "plane not in approved allowlist"
        if receipt_problem:
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=attempt_id,
                effect_id=effect_id,
                status="QUARANTINED",
                transport_status=transport_status.value,
                episode_outcome=EpisodeOutcome.QUARANTINED.value,
                detail=f"receipt mismatch fails closed: {receipt_problem}",
            )
            return "QUARANTINED", ""

        check = self.mechanical_fn(request, result.text)
        verifier = self.verifier_fn(request, result.text) if self.verifier_fn else None
        judge_votes: tuple[JudgeVote, ...] = ()
        if check.oracle is None and verifier is None and self.judge_fn is not None:
            # Blinded judging: judges see the objective and the text only —
            # never the donor identity.
            if meter.can_call():
                meter.charge_call()
                judge_votes = self.judge_fn(
                    request.model_visible_objective, result.text
                )

        evaluation = evaluate_candidate(
            transport_status=transport_status,
            now=self.now_fn(),
            expires_at=request.expires_at,
            required_artifact_present=check.artifact_present,
            oracle=check.oracle,
            verifier=verifier,
            judge_votes=judge_votes,
        )

        # Judge disagreement gets one bounded adjudication round; the
        # adjudication call charges the same meter as any provider call.
        if (
            evaluation.proposal_verdict is ProposalVerdict.INDETERMINATE
            and judge_votes
            and self.adjudicate_fn is not None
            and meter.can_call()
        ):
            meter.charge_call()
            extra_votes = self.adjudicate_fn(
                request.model_visible_objective, result.text
            )
            evaluation = evaluate_candidate(
                transport_status=transport_status,
                now=self.now_fn(),
                expires_at=request.expires_at,
                required_artifact_present=check.artifact_present,
                oracle=check.oracle,
                verifier=verifier,
                judge_votes=judge_votes + extra_votes,
            )

        receipt_dict = {
            "provider": receipt.provider,
            "model_id": receipt.model_id,
            "plane": receipt.plane,
            "runtime": receipt.runtime,
        }
        if evaluation.episode_outcome is EpisodeOutcome.EXPIRED:
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=attempt_id,
                effect_id=effect_id,
                status="EXPIRED",
                transport_status=transport_status.value,
                proposal_verdict=evaluation.proposal_verdict.value,
                episode_outcome=evaluation.episode_outcome.value,
                detail=evaluation.reason,
                provider_receipt=receipt_dict,
            )
            return "EXPIRED", check.confusion_cell
        if evaluation.episode_outcome is EpisodeOutcome.QUARANTINED:
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=attempt_id,
                effect_id=effect_id,
                status="QUARANTINED",
                transport_status=transport_status.value,
                proposal_verdict=evaluation.proposal_verdict.value,
                episode_outcome=evaluation.episode_outcome.value,
                detail=evaluation.reason,
                provider_receipt=receipt_dict,
            )
            return "QUARANTINED", check.confusion_cell

        sample = CandidateSample(
            function_pack_id=request.function_pack_id,
            semantic_root_id=request.semantic_root_id,
            leakage_group_id=request.leakage_group_id,
            function_contract_digest=request.function_contract_digest,
            tool_catalog_digest=request.tool_catalog_digest,
            ttl_condition=f"ttl-{request.ttl_seconds:g}s",
            response_type=request.response_type,
            donor_key=donor_key,
            recipe_id=request.recipe.recipe_id,
            text=result.text,
            transport_status=transport_status,
            proposal_verdict=evaluation.proposal_verdict,
            score=check.score,
            confusion_cell=check.confusion_cell,
            provenance_receipt=receipt_dict,
        )
        ingested = builder.ingest(sample)
        accepted = (
            ingested
            and evaluation.proposal_verdict is ProposalVerdict.VERIFIED_SUCCESS
        )
        self.ledger.append(
            work_key=item.work_key,
            attempt_id=attempt_id,
            effect_id=effect_id,
            status=(
                "ACCEPTED"
                if accepted
                else ("REJECTED" if ingested else "QUARANTINED")
            ),
            transport_status=transport_status.value,
            proposal_verdict=evaluation.proposal_verdict.value,
            episode_outcome=evaluation.episode_outcome.value,
            detail=evaluation.reason if ingested else "rejected by dataset rules",
            provider_receipt=receipt_dict,
        )
        if not ingested:
            return "QUARANTINED", check.confusion_cell
        return ("ACCEPTED" if accepted else "REJECTED"), check.confusion_cell

    # ------------------------------------------------------------- report

    def _build_report(
        self,
        *,
        requests: list[HarvestRequest],
        counts: dict[str, int],
        meter: BudgetMeter,
        builder: DatasetBuilder,
        failed_cells: list[str],
        donor_accepts: dict[str, list[int]],
        shard_dir: Path | None,
        validation_score: float,
        wave_index: int,
    ) -> dict:
        dataset_manifest = None
        if shard_dir is not None:
            dataset_manifest = builder.write_shards(shard_dir)

        stop_decision = {"stop": True, "reason": "single-wave session complete"}
        proposed_recipe_ids: list[str] = []
        if self.proposer is not None:
            attempted = counts["planned"] - counts["skipped_resume"]
            self.proposer.observe(
                WaveObservation(
                    wave_index=wave_index,
                    accepted=counts["accepted"],
                    attempted=max(attempted, 1),
                    validation_score=validation_score,
                    weak_confusion_cells=tuple(sorted(set(failed_cells))),
                    retention_cells=(),
                    donor_acceptance={
                        donor: (sum(flags) / len(flags) if flags else 0.0)
                        for donor, flags in sorted(donor_accepts.items())
                    },
                )
            )
            decision = self.proposer.should_stop()
            stop_decision = {"stop": decision.stop, "reason": decision.reason}
            proposed_recipe_ids = [r.recipe_id for r in self.proposer.propose()]

        # Targeted variant *requests* from aggregated failures. Request-only:
        # nothing here executes them; they await review / the next wave.
        variant_requests = []
        base = requests[0]
        now = self.now_fn()
        for cell in sorted(set(failed_cells)):
            variant = base.model_copy(
                update={
                    "recipe": base.recipe.model_copy(
                        update={
                            "recipe_id": f"{base.recipe.recipe_id}-variant-{cell}",
                            "prompt_surface": "confusion-targeted",
                        }
                    ),
                    "issued_at": now,
                    "expires_at": now + base.ttl_seconds,
                }
            )
            variant_requests.append(
                {
                    "confusion_cell": cell,
                    "recipe_id": variant.recipe.recipe_id,
                    "work_key_root": variant.work_key(
                        sorted(variant.recipe.donor_allocation)[0], 0
                    ),
                }
            )

        body = {
            "schema": REPORT_SCHEMA,
            "mode": self.mode,
            "campaign_id": requests[0].campaign_id,
            "function_pack_id": requests[0].function_pack_id,
            "source_dataset_id": requests[0].source_dataset_id,
            "target_dataset_id": requests[0].target_dataset_id,
            "counts": counts,
            "budget": {
                "provider_calls": meter.provider_calls,
                "retries": meter.retries,
                "max_provider_calls": meter.ceilings.max_provider_calls,
                "max_retries": meter.ceilings.max_retries,
            },
            "dataset_manifest": dataset_manifest,
            "rejections": builder.rejections,
            "variant_requests": variant_requests,
            "stop_decision": stop_decision,
            "proposed_recipe_ids": proposed_recipe_ids,
            "authorizes_training": False,
            "authorizes_sealed_evaluation": False,
            "next_step": "codex review and dataset freeze",
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        body["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return body
