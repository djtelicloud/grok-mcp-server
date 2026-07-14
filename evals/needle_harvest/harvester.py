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
- Authorization precedes effects: authorized_effects, the donor catalog,
  and the provider/plane allowlists are all checked *before* any transport
  call, ledger write, or shard write happens for a work item.
- Work keys hash the complete request semantics and are deterministic;
  completed work keys found in the ledger are skipped on resume, and the
  builder is rehydrated from persisted candidate content — a resumed
  harvest reproduces the same dataset without duplicating a single
  provider call or effect.
- One wave = one campaign, one source dataset, one target dataset, one
  function pack; each root binds to exactly one leakage group.
- The effective budget is the elementwise minimum of request, session, and
  manifest ceilings; retries and adjudication charge the same meter.
- Returned work keys and receipt provider/model/plane/runtime are validated
  against the runtime-discovered catalog (and, live, the manifest
  allowlists); any mismatch fails closed.
- Donor allocation weights become bounded discrete sample counts, and every
  donor/sample slot gets its own deterministic seed.
- Lanes are bounded: work is dispatched in deterministic batches of at most
  ``max_concurrent_lanes`` items.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.needle_harvest.contracts import (
    Ceilings,
    HarvestManifest,
    HarvestRequest,
    allocate_samples,
    min_ceilings,
)
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
        provider_allowlist: tuple[str, ...] | None = None,
        plane_allowlist: tuple[str, ...] | None = None,
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
            # Live allowlists come from the approved manifest, nowhere else.
            self.provider_allowlist: tuple[str, ...] | None = tuple(
                manifest.provider_allowlist
            )
            self.plane_allowlist: tuple[str, ...] | None = tuple(
                manifest.plane_allowlist
            )
        else:
            if ceilings is None:
                raise HarvestAuthorizationError("mock mode requires explicit ceilings")
            self.ceilings = ceilings
            self.approved_dataset_ids = tuple(approved_dataset_ids)
            self.provider_allowlist = (
                tuple(provider_allowlist) if provider_allowlist is not None else None
            )
            self.plane_allowlist = (
                tuple(plane_allowlist) if plane_allowlist is not None else None
            )
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
        # Effects are checked before anything happens: a request that does
        # not authorize provider calls or ledger appends cannot be run at
        # all — there is no partially-authorized execution path.
        for effect in ("provider_call", "ledger_append"):
            if effect not in request.authorized_effects:
                raise HarvestAuthorizationError(
                    f"request for root {request.semantic_root_id} does not "
                    f"authorize {effect!r} — refusing before any effect"
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
        # Wave consistency: one campaign, one source dataset, one target
        # dataset, one function pack per wave — and one leakage group for
        # each root and all its siblings.
        for label, values in (
            ("campaign", {r.campaign_id for r in requests}),
            ("source dataset", {r.source_dataset_id for r in requests}),
            ("target dataset", {r.target_dataset_id for r in requests}),
        ):
            if len(values) > 1:
                raise HarvestAuthorizationError(
                    f"wave consistency violated: one wave, one {label} "
                    f"(got {sorted(values)})"
                )
        packs = {r.function_pack_id for r in requests}
        if len(packs) > 1:
            raise HarvestAuthorizationError(
                f"cross-function mixing rejected: one wave, one pack (got {sorted(packs)})"
            )
        root_groups: dict[str, str] = {}
        for request in requests:
            bound = root_groups.setdefault(
                request.semantic_root_id, request.leakage_group_id
            )
            if bound != request.leakage_group_id:
                raise HarvestAuthorizationError(
                    f"root {request.semantic_root_id} appears with two leakage "
                    f"groups ({bound!r}, {request.leakage_group_id!r}) — a root "
                    "and its siblings share exactly one group"
                )
        if shard_dir is not None:
            for request in requests:
                if "shard_write" not in request.authorized_effects:
                    raise HarvestAuthorizationError(
                        f"request for root {request.semantic_root_id} does not "
                        "authorize shard_write — refusing before any effect"
                    )
        # Effective ceilings: the elementwise minimum of the session (live:
        # manifest) budget and every request's own budget.
        effective_ceilings = min_ceilings(
            self.ceilings, *[r.ceilings for r in requests]
        )
        if len(requests) > effective_ceilings.max_roots:
            raise HarvestAuthorizationError(
                f"{len(requests)} roots exceed the max_roots ceiling "
                f"({effective_ceilings.max_roots})"
            )
        for request in requests:
            self._guard_request(request)

        meter = BudgetMeter(effective_ceilings)
        catalog = {model.donor_key: model for model in self.transport.discover()}
        completed = self.ledger.completed_work_keys()
        builder = DatasetBuilder(
            function_pack_id=next(iter(packs)),
            target_dataset_id=requests[0].target_dataset_id,
            approved_dataset_ids=self.approved_dataset_ids,
        )
        # Resume rehydration: rebuild the dataset from candidate content
        # persisted in the ledger, in original ledger order, so a resumed
        # run reproduces identical nonempty shards.
        self._rehydrate_builder(builder, completed, requests[0].target_dataset_id)

        work_items = self._plan_work(requests)
        counts = {
            "planned": len(work_items),
            "skipped_resume": 0,
            "accepted": 0,
            "provisional": 0,
            "verified_failure": 0,
            "quarantined": 0,
            "refused_unauthorized": 0,
            "transport_failure": 0,
            "expired": 0,
            "budget_stopped": 0,
        }
        failed: list[tuple[str, HarvestRequest]] = []
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
                elif outcome == "PROVISIONAL":
                    counts["provisional"] += 1
                elif outcome == "REJECTED":
                    counts["verified_failure"] += 1
                elif outcome == "QUARANTINED":
                    counts["quarantined"] += 1
                elif outcome == "REFUSED_UNAUTHORIZED":
                    counts["refused_unauthorized"] += 1
                elif outcome == "TRANSPORT_FAILURE":
                    counts["transport_failure"] += 1
                elif outcome == "EXPIRED":
                    counts["expired"] += 1
                if outcome == "REJECTED" and cell:
                    failed.append((cell, item.request))

        report = self._build_report(
            requests=list(requests),
            counts=counts,
            meter=meter,
            builder=builder,
            failed=failed,
            donor_accepts=donor_accepts,
            shard_dir=shard_dir,
            validation_score=validation_score,
            wave_index=wave_index,
        )
        return report

    def _rehydrate_builder(
        self,
        builder: DatasetBuilder,
        completed_work_keys: set[str],
        target_dataset_id: str,
    ) -> None:
        """Rebuild the dataset from candidate content persisted in the
        ledger, in original ledger order, for work that will be skipped."""
        for row in self.ledger.rows():
            candidate = row.get("candidate")
            if not candidate or not candidate.get("ingested"):
                continue
            if row.get("work_key") not in completed_work_keys:
                continue
            if (
                candidate.get("function_pack_id") != builder.function_pack_id
                or candidate.get("target_dataset_id") != target_dataset_id
            ):
                continue
            builder.ingest(CandidateSample.from_payload(candidate))

    def _plan_work(self, requests: Sequence[HarvestRequest]) -> list[_WorkItem]:
        items: list[_WorkItem] = []
        for request in requests:
            # The same exact root goes to the allocated donors. Allocation
            # weights become bounded discrete counts (largest remainder), so
            # a 99/1 weighting yields genuinely unequal call counts.
            donor_counts = allocate_samples(
                request.recipe.donor_allocation,
                total=request.recipe.samples_per_donor
                * len(request.recipe.donor_allocation),
            )
            for donor_key in sorted(donor_counts):
                for sample_index in range(donor_counts[donor_key]):
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

        # Authorization precedes effects: the donor must exist in the
        # runtime-discovered catalog and its provider/plane must be on the
        # allowlist BEFORE any transport call is attempted.
        model = catalog.get(donor_key)
        refusal = ""
        if model is None:
            refusal = f"donor {donor_key!r} is not in the discovered catalog"
        elif (
            self.provider_allowlist is not None
            and model.provider not in self.provider_allowlist
        ):
            refusal = (
                f"provider {model.provider!r} is not in the approved allowlist"
            )
        elif (
            self.plane_allowlist is not None
            and model.plane not in self.plane_allowlist
        ):
            refusal = f"plane {model.plane!r} is not in the approved allowlist"
        if refusal:
            self.ledger.append(
                work_key=item.work_key,
                attempt_id=attempt_id,
                effect_id=effect_id,
                status="REFUSED_UNAUTHORIZED",
                detail=f"refused before transport call: {refusal}",
            )
            return "REFUSED_UNAUTHORIZED", ""

        sample_seed = request.sample_seed(donor_key, item.sample_index)
        transport_request = TransportRequest(
            work_key=item.work_key,
            donor_key=donor_key,
            objective=request.model_visible_objective,
            temperature=request.recipe.temperature,
            top_p=request.recipe.top_p,
            seed=sample_seed,
            max_tokens=min(meter.ceilings.max_tokens, 4096),
        )
        meter.charge_call()
        result = self.transport.call(transport_request)

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
            # Retry keeps the same work identity, effect ID, and seed.
            result = self.transport.call(transport_request)

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
        # live mode, the manifest allowlists. The transport must echo the
        # exact work key it executed. Mismatch fails closed.
        receipt = result.receipt
        receipt_problem = ""
        if result.work_key != item.work_key:
            receipt_problem = "returned work_key mismatch fails closed"
        elif receipt is None:
            receipt_problem = "missing provider receipt"
        elif (
            receipt.provider != model.provider
            or receipt.model_id != model.model_id
            or receipt.plane != model.plane
        ):
            receipt_problem = "receipt does not match discovered catalog identity"
        elif self.mode == "mock" and receipt.runtime != "mock":
            receipt_problem = (
                f"receipt runtime {receipt.runtime!r} is not the mock runtime"
            )
        elif self.mode == "live" and (
            not receipt.runtime or receipt.runtime == "mock"
        ):
            receipt_problem = (
                f"receipt runtime {receipt.runtime!r} is not a live runtime"
            )
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
        combined_votes = judge_votes
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
            combined_votes = judge_votes + extra_votes
            evaluation = evaluate_candidate(
                transport_status=transport_status,
                now=self.now_fn(),
                expires_at=request.expires_at,
                required_artifact_present=check.artifact_present,
                oracle=check.oracle,
                verifier=verifier,
                judge_votes=combined_votes,
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
            episode_outcome=evaluation.episode_outcome,
            model_visible_objective=request.model_visible_objective,
            ttl_seconds=request.ttl_seconds,
            issued_at=request.issued_at,
            expires_at=request.expires_at,
            score=check.score,
            confusion_cell=check.confusion_cell,
            provenance_receipt=receipt_dict,
            judge_votes=tuple(
                (vote.judge_key, vote.approves) for vote in combined_votes
            ),
        )

        def _candidate_payload(ingested: bool) -> dict:
            payload = sample.to_payload()
            payload["ingested"] = ingested
            payload["target_dataset_id"] = request.target_dataset_id
            return payload

        if evaluation.episode_outcome is EpisodeOutcome.QUARANTINED:
            # Substantive but unresolved answers keep their full content in
            # the ledger so future waves can build hard negatives from them.
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
                candidate=_candidate_payload(False),
            )
            return "QUARANTINED", check.confusion_cell

        ingested = builder.ingest(sample)
        if not ingested:
            status = "QUARANTINED"
        elif evaluation.proposal_verdict is ProposalVerdict.VERIFIED_SUCCESS:
            status = "ACCEPTED"
        elif evaluation.proposal_verdict is ProposalVerdict.JUDGE_PROVISIONAL:
            # Unanimous judge approval stays provisional: retained for the
            # opt-in provisional training view, never verified, never
            # converted into a rejection.
            status = "PROVISIONAL"
        else:
            status = "REJECTED"
        self.ledger.append(
            work_key=item.work_key,
            attempt_id=attempt_id,
            effect_id=effect_id,
            status=status,
            transport_status=transport_status.value,
            proposal_verdict=evaluation.proposal_verdict.value,
            episode_outcome=evaluation.episode_outcome.value,
            detail=evaluation.reason if ingested else "rejected by dataset rules",
            provider_receipt=receipt_dict,
            candidate=_candidate_payload(ingested),
        )
        return status, check.confusion_cell

    # ------------------------------------------------------------- report

    def _build_report(
        self,
        *,
        requests: list[HarvestRequest],
        counts: dict[str, int],
        meter: BudgetMeter,
        builder: DatasetBuilder,
        failed: list[tuple[str, HarvestRequest]],
        donor_accepts: dict[str, list[int]],
        shard_dir: Path | None,
        validation_score: float,
        wave_index: int,
    ) -> dict:
        dataset_manifest = None
        if shard_dir is not None:
            dataset_manifest = builder.write_shards(shard_dir)

        failed_cells = [cell for cell, _ in failed]
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

        # Targeted variant *requests* from aggregated failures. Each variant
        # derives from the ORIGINATING request, so a weak root produces
        # variants of that root — never clones of the first request.
        # Request-only: nothing here executes them; they await review.
        variant_requests = []
        now = self.now_fn()
        seen_variants: set[tuple[str, str]] = set()
        for cell, origin in sorted(
            failed, key=lambda pair: (pair[0], pair[1].semantic_root_id)
        ):
            key = (cell, origin.semantic_root_id)
            if key in seen_variants:
                continue
            seen_variants.add(key)
            variant = origin.model_copy(
                update={
                    "recipe": origin.recipe.model_copy(
                        update={
                            "recipe_id": f"{origin.recipe.recipe_id}-variant-{cell}",
                            "prompt_surface": "confusion-targeted",
                        }
                    ),
                    "issued_at": now,
                    "expires_at": now + origin.ttl_seconds,
                }
            )
            variant_requests.append(
                {
                    "confusion_cell": cell,
                    "root_id": variant.semantic_root_id,
                    "leakage_group_id": variant.leakage_group_id,
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
