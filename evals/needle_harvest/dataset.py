"""Candidate dataset assembly: SFT and DPO views, packs, splits, shards.

Rules enforced here (each with a test):

- Distinct SFT-target and DPO-preference views.
- A DPO pair's chosen and rejected must answer the same semantic root, TTL
  condition, function contract, tool catalog, and response type. A rationale
  is never paired with an answer.
- Authentication/timeout/transport/empty/malformed failures are never DPO
  negatives; preferred pairing is winner-versus-runner-up plus
  confusion-specific hard negatives.
- No hidden chain-of-thought: only deliberately generated, visible
  ``decision_summary`` / ``plan_state`` surfaces may exist, and forbidden
  field names are rejected at ingestion.
- Deterministic semantic content IDs; exact and semantic deduplication.
- A root and all its sibling variants share one leakage group, and the
  partition is a deterministic function of the group — siblings can never
  cross partitions.
- One function pack per builder; cross-pack rows are rejected.
- Never append to an approved dataset generation.
- Shards are content-addressed files; the manifest carries digests only.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evals.needle_harvest.truth import EpisodeOutcome, ProposalVerdict, TransportStatus

DATASET_MANIFEST_SCHEMA = "needle-harvest-dataset/v1"

SPLITS = ("train", "dev", "holdout")
_SPLIT_WEIGHTS = (8, 1, 1)  # deterministic 80/10/10 by leakage group

# Any of these appearing as a candidate field means hidden reasoning is
# trying to enter the artifact stream. Rejected at ingestion.
FORBIDDEN_FIELDS = frozenset(
    {
        "chain_of_thought",
        "cot",
        "reasoning",
        "raw_reasoning",
        "hidden_reasoning",
        "scratchpad",
        "internal_monologue",
        "thinking",
    }
)

VISIBLE_RESPONSE_FIELDS = frozenset({"answer", "decision_summary", "plan_state"})

# Transport-level failures that must never become preference negatives.
_NON_SEMANTIC_STATUSES = frozenset(
    {
        TransportStatus.TIMEOUT,
        TransportStatus.EMPTY,
        TransportStatus.MALFORMED,
        TransportStatus.REFUSED,
        TransportStatus.ERROR,
    }
)


def semantic_content_id(pack_id: str, root_id: str, contract_digest: str, text: str) -> str:
    """Deterministic semantic content identity for dedup and provenance."""
    normalized = _normalize_text(text)
    digest = hashlib.sha256(
        "\x1f".join((pack_id, root_id, contract_digest, normalized)).encode("utf-8")
    ).hexdigest()
    return f"content-{digest[:40]}"


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\u201c\u201d]", '"', text)
    text = re.sub(r"[\u2018\u2019]", "'", text)
    return text


def split_for_leakage_group(leakage_group_id: str) -> str:
    """Deterministic partition of a whole leakage group (never per-row)."""
    digest = hashlib.sha256(leakage_group_id.encode("utf-8")).digest()
    bucket = digest[0] % sum(_SPLIT_WEIGHTS)
    edge = 0
    for split, weight in zip(SPLITS, _SPLIT_WEIGHTS):
        edge += weight
        if bucket < edge:
            return split
    return SPLITS[0]


@dataclass(frozen=True)
class CandidateSample:
    """One evaluated candidate response bound to its request context.

    Carries the exact model-visible input (objective + TTL envelope), not
    just the response: a training example is unlearnable without its
    prompt. Provenance (provider receipt, judge votes) lives in separate
    fields that never enter model-visible text.
    """

    function_pack_id: str
    semantic_root_id: str
    leakage_group_id: str
    function_contract_digest: str
    tool_catalog_digest: str
    ttl_condition: str
    response_type: str
    donor_key: str
    recipe_id: str
    text: str
    transport_status: TransportStatus
    proposal_verdict: ProposalVerdict
    episode_outcome: EpisodeOutcome
    model_visible_objective: str
    ttl_seconds: float
    issued_at: float
    expires_at: float
    score: float = 0.0
    confusion_cell: str = ""
    visible_fields: tuple[str, ...] = ("answer",)
    provenance_receipt: dict[str, str] = field(default_factory=dict)
    judge_votes: tuple[tuple[str, bool], ...] = ()

    @property
    def content_id(self) -> str:
        return semantic_content_id(
            self.function_pack_id,
            self.semantic_root_id,
            self.function_contract_digest,
            self.text,
        )

    @property
    def input_digest(self) -> str:
        """Digest of the exact model-visible input for this candidate."""
        canonical = json.dumps(
            {
                "objective": self.model_visible_objective,
                "ttl_seconds": self.ttl_seconds,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "function_contract_digest": self.function_contract_digest,
                "tool_catalog_digest": self.tool_catalog_digest,
                "response_type": self.response_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe dict carrying the complete candidate content."""
        payload = asdict(self)
        payload["transport_status"] = self.transport_status.value
        payload["proposal_verdict"] = self.proposal_verdict.value
        payload["episode_outcome"] = self.episode_outcome.value
        payload["visible_fields"] = list(self.visible_fields)
        payload["judge_votes"] = [
            [judge_key, approves] for judge_key, approves in self.judge_votes
        ]
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CandidateSample:
        """Reconstruct the exact candidate persisted by :meth:`to_payload`."""
        return cls(
            function_pack_id=payload["function_pack_id"],
            semantic_root_id=payload["semantic_root_id"],
            leakage_group_id=payload["leakage_group_id"],
            function_contract_digest=payload["function_contract_digest"],
            tool_catalog_digest=payload["tool_catalog_digest"],
            ttl_condition=payload["ttl_condition"],
            response_type=payload["response_type"],
            donor_key=payload["donor_key"],
            recipe_id=payload["recipe_id"],
            text=payload["text"],
            transport_status=TransportStatus(payload["transport_status"]),
            proposal_verdict=ProposalVerdict(payload["proposal_verdict"]),
            episode_outcome=EpisodeOutcome(payload["episode_outcome"]),
            model_visible_objective=payload["model_visible_objective"],
            ttl_seconds=payload["ttl_seconds"],
            issued_at=payload["issued_at"],
            expires_at=payload["expires_at"],
            score=payload.get("score", 0.0),
            confusion_cell=payload.get("confusion_cell", ""),
            visible_fields=tuple(payload.get("visible_fields", ("answer",))),
            provenance_receipt=dict(payload.get("provenance_receipt", {})),
            judge_votes=tuple(
                (str(judge_key), bool(approves))
                for judge_key, approves in payload.get("judge_votes", [])
            ),
        )


class DatasetBuildError(ValueError):
    pass


class DatasetBuilder:
    """Builds one function pack's candidate dataset generation."""

    def __init__(
        self,
        *,
        function_pack_id: str,
        target_dataset_id: str,
        approved_dataset_ids: tuple[str, ...] = (),
    ) -> None:
        if target_dataset_id in approved_dataset_ids:
            raise DatasetBuildError(
                f"dataset {target_dataset_id} is already approved/frozen — "
                "never append to an approved generation; propose the next one"
            )
        self.function_pack_id = function_pack_id
        self.target_dataset_id = target_dataset_id
        self._samples: list[CandidateSample] = []
        self._exact_seen: set[str] = set()
        self._semantic_seen: set[str] = set()
        self._rejections: list[dict[str, str]] = []

    # ------------------------------------------------------------ ingestion

    def ingest(self, sample: CandidateSample, raw_fields: dict[str, Any] | None = None) -> bool:
        """Admit one candidate; returns False (and records why) if rejected."""
        if sample.function_pack_id != self.function_pack_id:
            self._reject(sample, "cross-pack mixing rejected")
            return False
        raw_keys = {k.lower() for k in (raw_fields or {})}
        hidden = raw_keys & FORBIDDEN_FIELDS
        if hidden or set(sample.visible_fields) - VISIBLE_RESPONSE_FIELDS:
            self._reject(sample, f"hidden chain-of-thought fields rejected: {sorted(hidden) or sorted(set(sample.visible_fields) - VISIBLE_RESPONSE_FIELDS)}")
            return False
        exact_key = hashlib.sha256(
            f"{sample.semantic_root_id}\x1f{sample.text}".encode()
        ).hexdigest()
        if exact_key in self._exact_seen:
            self._reject(sample, "exact duplicate rejected")
            return False
        if sample.content_id in self._semantic_seen:
            self._reject(sample, "semantic duplicate rejected")
            return False
        self._exact_seen.add(exact_key)
        self._semantic_seen.add(sample.content_id)
        self._samples.append(sample)
        return True

    def _reject(self, sample: CandidateSample, reason: str) -> None:
        self._rejections.append(
            {
                "content_id": sample.content_id,
                "root": sample.semantic_root_id,
                "reason": reason,
            }
        )

    @property
    def rejections(self) -> list[dict[str, str]]:
        return list(self._rejections)

    # ---------------------------------------------------------------- views

    def sft_view(self) -> list[dict[str, Any]]:
        """SFT targets: verified-success candidates only, split by group."""
        rows = []
        for sample in self._samples:
            if sample.proposal_verdict is not ProposalVerdict.VERIFIED_SUCCESS:
                continue
            rows.append(self._row(sample, view="sft"))
        rows.sort(key=lambda r: r["content_id"])
        return rows

    def provisional_sft_view(self) -> list[dict[str, Any]]:
        """Judge-approved provisional SFT targets — an explicit opt-in view.

        Unanimous ``JUDGE_PROVISIONAL`` approvals are imperfect but
        learnable research data. They never enter the verified ``sft`` view
        and are never relabeled ``VERIFIED_SUCCESS``; training recipes that
        want them must opt into this view deliberately. Judge provenance is
        preserved on every row.
        """
        rows = []
        for sample in self._samples:
            if sample.proposal_verdict is not ProposalVerdict.JUDGE_PROVISIONAL:
                continue
            if sample.episode_outcome is not EpisodeOutcome.SUCCESS:
                continue
            rows.append(self._row(sample, view="sft_provisional"))
        rows.sort(key=lambda r: r["content_id"])
        return rows

    def dpo_view(self) -> list[dict[str, Any]]:
        """DPO preference pairs under the same-contract rule.

        Ranking per root: verified successes ordered by score. Chosen =
        winner; rejected = runner-up when the runner-up is a *semantic*
        failure candidate, else the best same-root verified/judged failure.
        Confusion-specific hard negatives (same root, failing candidate
        tagged with a confusion cell) are preferred over generic failures.
        Winner-versus-worst is deliberately not the default.
        """
        by_root: dict[str, list[CandidateSample]] = {}
        for sample in self._samples:
            by_root.setdefault(sample.semantic_root_id, []).append(sample)

        pairs = []
        for root_id in sorted(by_root):
            candidates = by_root[root_id]
            winners = sorted(
                (s for s in candidates if s.proposal_verdict is ProposalVerdict.VERIFIED_SUCCESS),
                key=lambda s: (-s.score, s.content_id),
            )
            if not winners:
                continue
            chosen = winners[0]
            rejected = self._pick_rejected(chosen, candidates, winners)
            if rejected is None:
                continue
            self._assert_same_contract(chosen, rejected)
            pairs.append(
                {
                    "pair_id": semantic_content_id(
                        self.function_pack_id,
                        root_id,
                        chosen.function_contract_digest,
                        chosen.text + "\x1f" + rejected.text,
                    ),
                    "root_id": root_id,
                    "chosen": self._row(chosen, view="dpo"),
                    "rejected": self._row(rejected, view="dpo"),
                    "pairing": (
                        "winner-vs-runner-up"
                        if rejected in winners
                        else (
                            "confusion-hard-negative"
                            if rejected.confusion_cell
                            else "winner-vs-verified-failure"
                        )
                    ),
                }
            )
        return pairs

    def _pick_rejected(
        self,
        chosen: CandidateSample,
        candidates: list[CandidateSample],
        winners: list[CandidateSample],
    ) -> CandidateSample | None:
        def eligible(sample: CandidateSample) -> bool:
            if sample is chosen:
                return False
            # Never pair infra failures; never pair unevaluated text.
            if sample.transport_status in _NON_SEMANTIC_STATUSES:
                return False
            if sample.proposal_verdict not in (
                ProposalVerdict.VERIFIED_SUCCESS,
                ProposalVerdict.VERIFIED_FAILURE,
                ProposalVerdict.JUDGE_PROVISIONAL,
            ):
                return False
            # A judge-approved provisional answer is (provisionally) good;
            # it must not be spent as a preference negative.
            if (
                sample.proposal_verdict is ProposalVerdict.JUDGE_PROVISIONAL
                and sample.episode_outcome is EpisodeOutcome.SUCCESS
            ):
                return False
            if sample.response_type != chosen.response_type:
                return False
            return True

        hard_negatives = sorted(
            (
                s
                for s in candidates
                if eligible(s)
                and s.confusion_cell
                and s.proposal_verdict is ProposalVerdict.VERIFIED_FAILURE
            ),
            key=lambda s: (-s.score, s.content_id),
        )
        if hard_negatives:
            return hard_negatives[0]
        if len(winners) > 1 and eligible(winners[1]):
            return winners[1]
        failures = sorted(
            (
                s
                for s in candidates
                if eligible(s) and s.proposal_verdict is ProposalVerdict.VERIFIED_FAILURE
            ),
            key=lambda s: (-s.score, s.content_id),
        )
        return failures[0] if failures else None

    def _assert_same_contract(self, chosen: CandidateSample, rejected: CandidateSample) -> None:
        mismatches = [
            name
            for name, a, b in (
                ("root", chosen.semantic_root_id, rejected.semantic_root_id),
                ("ttl_condition", chosen.ttl_condition, rejected.ttl_condition),
                ("function_contract", chosen.function_contract_digest, rejected.function_contract_digest),
                ("tool_catalog", chosen.tool_catalog_digest, rejected.tool_catalog_digest),
                ("response_type", chosen.response_type, rejected.response_type),
            )
            if a != b
        ]
        if mismatches:
            raise DatasetBuildError(
                f"chosen/rejected contract mismatch on {', '.join(mismatches)} — "
                "a preference pair must answer the same contract"
            )

    def _row(self, sample: CandidateSample, *, view: str) -> dict[str, Any]:
        return {
            "view": view,
            "content_id": sample.content_id,
            "pack": self.function_pack_id,
            "dataset": self.target_dataset_id,
            "root_id": sample.semantic_root_id,
            "leakage_group": sample.leakage_group_id,
            "split": split_for_leakage_group(sample.leakage_group_id),
            "ttl_condition": sample.ttl_condition,
            "response_type": sample.response_type,
            # The exact model-visible input: without it a row is not a
            # learnable example. ``text`` below is the target response.
            "input": {
                "objective": sample.model_visible_objective,
                "ttl_seconds": sample.ttl_seconds,
                "issued_at": sample.issued_at,
                "expires_at": sample.expires_at,
                "function_contract_digest": sample.function_contract_digest,
                "tool_catalog_digest": sample.tool_catalog_digest,
                "response_type": sample.response_type,
            },
            "input_digest": sample.input_digest,
            "text": sample.text,
            "label": sample.proposal_verdict.value,
            "episode_outcome": sample.episode_outcome.value,
            "confusion_cell": sample.confusion_cell,
            # Provenance receipts stay out of model-visible text; the donor
            # key is neutral and the provider identity lives only here.
            "provenance": dict(sorted(sample.provenance_receipt.items())),
            "judges": [
                {"judge_key": judge_key, "approves": approves}
                for judge_key, approves in sample.judge_votes
            ],
            "donor_key": sample.donor_key,
            "recipe_id": sample.recipe_id,
        }

    # -------------------------------------------------------------- balance

    def balance_report(self) -> dict[str, dict[str, int]]:
        report: dict[str, dict[str, int]] = {
            "by_label": {},
            "by_donor": {},
            "by_recipe": {},
            "by_ttl_condition": {},
            "by_split": {},
        }
        for sample in self._samples:
            for key, value in (
                ("by_label", sample.proposal_verdict.value),
                ("by_donor", sample.donor_key),
                ("by_recipe", sample.recipe_id),
                ("by_ttl_condition", sample.ttl_condition),
                ("by_split", split_for_leakage_group(sample.leakage_group_id)),
            ):
                report[key][value] = report[key].get(value, 0) + 1
        return {k: dict(sorted(v.items())) for k, v in report.items()}

    # --------------------------------------------------------------- shards

    def write_shards(self, out_dir: Path) -> dict[str, Any]:
        """Content-addressed shards + digest-only manifest.

        Massive data lives in the artifact store (``out_dir``); git is meant
        to carry only the manifest (schemas, digests, counts). An existing
        populated manifest can never be replaced by an empty build — that
        would silently destroy a dataset on a broken resume.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        views = (
            ("sft", self.sft_view()),
            ("sft_provisional", self.provisional_sft_view()),
            ("dpo", self.dpo_view()),
        )
        new_total = sum(len(rows) for _, rows in views)
        manifest_path = out_dir / "manifest.json"
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())
            existing_total = sum(
                int(info.get("rows", 0))
                for info in existing.get("shards", {}).values()
            )
            if existing_total > 0 and new_total == 0:
                raise DatasetBuildError(
                    "refusing to replace a populated dataset manifest with an "
                    "empty build — resume must reconstruct candidates first"
                )
        shards: dict[str, dict[str, Any]] = {}
        for name, rows in views:
            payload = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
            payload += "\n" if rows else ""
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            shard_path = out_dir / f"{digest}.jsonl"
            shard_path.write_text(payload)
            shards[name] = {"sha256": digest, "rows": len(rows), "path": shard_path.name}
        manifest = {
            "schema": DATASET_MANIFEST_SCHEMA,
            "pack": self.function_pack_id,
            "dataset": self.target_dataset_id,
            "shards": shards,
            "balance": self.balance_report(),
            "rejections": len(self._rejections),
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        manifest["manifest_sha256"] = hashlib.sha256(
            manifest_bytes.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        return manifest
