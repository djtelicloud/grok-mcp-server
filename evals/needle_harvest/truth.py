"""Truth hierarchy for harvested candidates.

Order of authority:

1. Mechanical oracle / downstream-effect receipts — the only source of
   ``VERIFIED_SUCCESS`` or ``VERIFIED_FAILURE``.
2. Claim-scoped verifier — verifies exactly the claims it is scoped to.
3. Blinded cross-provider judge — only when exact verification is
   impossible; can produce at most ``JUDGE_PROVISIONAL``. Disagreement is
   ``INDETERMINATE`` and quarantined or adjudicated within a fixed budget.

``transport_status`` (infrastructure), ``proposal_verdict`` (semantics), and
``episode_outcome`` (whole-episode disposition) are kept independent: a
timeout is not a wrong answer; a wrong answer is not a transport error.

There is no promise-phrase regex anywhere in this module: wording never
proves completion. A response that lacks the required completed artifact or
receipt is structurally incomplete regardless of what it says.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class TransportStatus(str, enum.Enum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    EMPTY = "EMPTY"
    MALFORMED = "MALFORMED"
    REFUSED = "REFUSED"
    ERROR = "ERROR"


class ProposalVerdict(str, enum.Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    JUDGE_PROVISIONAL = "JUDGE_PROVISIONAL"
    INDETERMINATE = "INDETERMINATE"
    STRUCTURALLY_INCOMPLETE = "STRUCTURALLY_INCOMPLETE"
    NOT_EVALUATED = "NOT_EVALUATED"


class EpisodeOutcome(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    QUARANTINED = "QUARANTINED"
    EXPIRED = "EXPIRED"
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"


@dataclass(frozen=True)
class OracleResult:
    """Output of a mechanical oracle or a downstream-effect receipt check."""

    passed: bool
    receipt_digest: str  # digest of the oracle evidence; required


@dataclass(frozen=True)
class VerifierResult:
    """Claim-scoped verifier outcome (claims listed explicitly)."""

    passed: bool
    claims_checked: tuple[str, ...]
    receipt_digest: str


@dataclass(frozen=True)
class JudgeVote:
    """One blinded judge's vote. Judges never see donor identities."""

    judge_key: str
    approves: bool


@dataclass(frozen=True)
class Evaluation:
    transport_status: TransportStatus
    proposal_verdict: ProposalVerdict
    episode_outcome: EpisodeOutcome
    reason: str = ""
    evidence_digests: tuple[str, ...] = field(default_factory=tuple)


def evaluate_candidate(
    *,
    transport_status: TransportStatus,
    now: float,
    expires_at: float,
    required_artifact_present: bool,
    oracle: OracleResult | None = None,
    verifier: VerifierResult | None = None,
    judge_votes: tuple[JudgeVote, ...] = (),
) -> Evaluation:
    """Apply the truth hierarchy to one candidate response."""
    # TTL is checked first and fails closed: post-expiry work is void even
    # if a beautiful answer arrived.
    if now >= expires_at:
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=ProposalVerdict.NOT_EVALUATED,
            episode_outcome=EpisodeOutcome.EXPIRED,
            reason="request TTL expired before evaluation",
        )

    # Infrastructure failures are infrastructure facts — never semantic
    # verdicts, never negatives.
    if transport_status is not TransportStatus.OK:
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=ProposalVerdict.NOT_EVALUATED,
            episode_outcome=EpisodeOutcome.TRANSPORT_FAILURE,
            reason=f"transport {transport_status.value} is not an answer",
        )

    # Structural completeness precedes any wording: no artifact/receipt, no
    # success — regardless of how confident the text sounds.
    if not required_artifact_present:
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=ProposalVerdict.STRUCTURALLY_INCOMPLETE,
            episode_outcome=EpisodeOutcome.FAILURE,
            reason="required completed artifact/receipt missing",
        )

    if oracle is not None:
        if not oracle.receipt_digest:
            return Evaluation(
                transport_status=transport_status,
                proposal_verdict=ProposalVerdict.STRUCTURALLY_INCOMPLETE,
                episode_outcome=EpisodeOutcome.FAILURE,
                reason="oracle result lacks a receipt digest",
            )
        verdict = (
            ProposalVerdict.VERIFIED_SUCCESS
            if oracle.passed
            else ProposalVerdict.VERIFIED_FAILURE
        )
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=verdict,
            episode_outcome=(
                EpisodeOutcome.SUCCESS if oracle.passed else EpisodeOutcome.FAILURE
            ),
            reason="mechanical oracle",
            evidence_digests=(oracle.receipt_digest,),
        )

    if verifier is not None:
        if not verifier.claims_checked or not verifier.receipt_digest:
            return Evaluation(
                transport_status=transport_status,
                proposal_verdict=ProposalVerdict.INDETERMINATE,
                episode_outcome=EpisodeOutcome.QUARANTINED,
                reason="verifier result without explicit claim scope/receipt",
            )
        verdict = (
            ProposalVerdict.VERIFIED_SUCCESS
            if verifier.passed
            else ProposalVerdict.VERIFIED_FAILURE
        )
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=verdict,
            episode_outcome=(
                EpisodeOutcome.SUCCESS if verifier.passed else EpisodeOutcome.FAILURE
            ),
            reason="claim-scoped verifier",
            evidence_digests=(verifier.receipt_digest,),
        )

    if judge_votes:
        approvals = [v.approves for v in judge_votes]
        if all(approvals):
            # A model judge can never mint VERIFIED_SUCCESS.
            return Evaluation(
                transport_status=transport_status,
                proposal_verdict=ProposalVerdict.JUDGE_PROVISIONAL,
                episode_outcome=EpisodeOutcome.SUCCESS,
                reason="unanimous blinded judges (provisional only)",
            )
        if not any(approvals):
            return Evaluation(
                transport_status=transport_status,
                proposal_verdict=ProposalVerdict.JUDGE_PROVISIONAL,
                episode_outcome=EpisodeOutcome.FAILURE,
                reason="unanimous blinded judges against (provisional only)",
            )
        return Evaluation(
            transport_status=transport_status,
            proposal_verdict=ProposalVerdict.INDETERMINATE,
            episode_outcome=EpisodeOutcome.QUARANTINED,
            reason="judge disagreement — quarantined pending adjudication budget",
        )

    return Evaluation(
        transport_status=transport_status,
        proposal_verdict=ProposalVerdict.INDETERMINATE,
        episode_outcome=EpisodeOutcome.QUARANTINED,
        reason="no oracle, verifier, or judges available",
    )
