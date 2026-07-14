"""The sixteen required mock-mode proofs for the adaptive harvester.

Each test is numbered to match the harvesting specification. Everything
here is pure and offline: no network, no credentials, no training, no
sealed evaluation.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from evals.needle_harvest.contracts import (
    ALLOWED_EFFECTS,
    Ceilings,
    GenerationRecipe,
    HarvestManifest,
    HarvestRequest,
)
from evals.needle_harvest.dataset import (
    CandidateSample,
    DatasetBuildError,
    DatasetBuilder,
    split_for_leakage_group,
)
from evals.needle_harvest.harvester import (
    HarvestAuthorizationError,
    HarvestSession,
    MechanicalCheck,
)
from evals.needle_harvest.ledger import AttemptLedger
from evals.needle_harvest.transport import (
    MockTransport,
    ProviderModel,
)
from evals.needle_harvest.truth import (
    JudgeVote,
    OracleResult,
    ProposalVerdict,
    TransportStatus,
    evaluate_candidate,
)

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "evals" / "needle_harvest"

NOW = 2_000.0
HEAD_SHA = "f" * 40


def make_ceilings(**overrides) -> Ceilings:
    values = dict(
        max_provider_calls=200,
        max_tokens=2048,
        max_retries=2,
        max_seconds=300.0,
        max_cost_usd=2.0,
        max_roots=16,
    )
    values.update(overrides)
    return Ceilings(**values)


def make_recipe(**overrides) -> GenerationRecipe:
    values = dict(
        recipe_id="recipe-base",
        donor_allocation={"donor-a": 0.5, "donor-b": 0.5},
        temperature=0.7,
        top_p=0.9,
        samples_per_donor=2,
    )
    values.update(overrides)
    return GenerationRecipe(**values)


def make_request(**overrides) -> HarvestRequest:
    values = dict(
        campaign_id="needle-campaign-1",
        source_dataset_id="D0001",
        target_dataset_id="D0002",
        function_pack_id="pack-alpha",
        semantic_root_id="root-001",
        leakage_group_id="lg-001",
        function_contract_digest="a" * 40,
        model_visible_objective="resolve the root task",
        ttl_seconds=3_600.0,
        issued_at=1_000.0,
        expires_at=10_000.0,
        authorized_effects=("provider_call", "ledger_append", "shard_write"),
        tool_catalog_digest="b" * 40,
        recipe=make_recipe(),
        seed=7,
        ceilings=make_ceilings(),
    )
    values.update(overrides)
    return HarvestRequest(**values)


def make_manifest(**overrides) -> HarvestManifest:
    values = dict(
        campaign_id="needle-campaign-1",
        approved_by="codex",
        approved_head_sha=HEAD_SHA,
        harvesting_enabled=True,
        source_dataset_id="D0001",
        target_dataset_id="D0002",
        active_training_dataset_id="D0001",
        approved_dataset_ids=("D0001",),
        provider_allowlist=("prov-x", "prov-y"),
        plane_allowlist=("mock",),
        ceilings=make_ceilings(),
    )
    values.update(overrides)
    return HarvestManifest(**values)


CATALOG = (
    ProviderModel(donor_key="donor-a", provider="prov-x", model_id="model-1", plane="mock"),
    ProviderModel(donor_key="donor-b", provider="prov-y", model_id="model-2", plane="mock"),
)


def oracle_mechanical(request, text) -> MechanicalCheck:
    return MechanicalCheck(
        artifact_present=True,
        oracle=OracleResult(passed=text.startswith("mock-answer-"), receipt_digest="c" * 40),
        score=1.0 if text.startswith("mock-answer-") else 0.0,
    )


def make_session(tmp_path: Path, **overrides) -> tuple[HarvestSession, MockTransport, AttemptLedger]:
    transport = overrides.pop("transport", None) or MockTransport(CATALOG)
    ledger = overrides.pop("ledger", None) or AttemptLedger(tmp_path / "ledger.jsonl")
    values = dict(
        mode="mock",
        transport=transport,
        ledger=ledger,
        active_training_dataset_id="D0001",
        mechanical_fn=oracle_mechanical,
        now_fn=lambda: NOW,
        ceilings=make_ceilings(),
    )
    values.update(overrides)
    session = HarvestSession(**values)
    return session, transport, ledger


def make_sample(**overrides) -> CandidateSample:
    values = dict(
        function_pack_id="pack-alpha",
        semantic_root_id="root-001",
        leakage_group_id="lg-001",
        function_contract_digest="a" * 40,
        tool_catalog_digest="b" * 40,
        ttl_condition="ttl-3600s",
        response_type="answer",
        donor_key="donor-a",
        recipe_id="recipe-base",
        text="the answer is 42",
        transport_status=TransportStatus.OK,
        proposal_verdict=ProposalVerdict.VERIFIED_SUCCESS,
        score=1.0,
    )
    values.update(overrides)
    return CandidateSample(**values)


# ---------------------------------------------------------------------------
# 1. Mock mode makes zero provider/network calls and reads zero credentials.
# ---------------------------------------------------------------------------


def test_01_mock_mode_zero_network_zero_credentials(tmp_path, monkeypatch):
    def _blocked(*_a, **_k):
        raise AssertionError("network access attempted during mock harvesting")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    import os

    accessed: list[str] = []
    real_getenv = os.getenv
    monkeypatch.setattr(
        os, "getenv", lambda key, default=None: (accessed.append(key), real_getenv(key, default))[1]
    )

    session, transport, _ = make_session(tmp_path)
    report = session.run([make_request()], shard_dir=tmp_path / "shards")
    assert report["counts"]["accepted"] == 4
    assert len(transport.calls) == 4  # in-memory mock only

    credentialish = [
        k
        for k in accessed
        if re.search(r"KEY|TOKEN|SECRET|AUTH|CRED", k, re.IGNORECASE)
    ]
    assert credentialish == []

    # Structural proof: the package never touches env/credential/network APIs.
    deny = re.compile(
        r"\bimport os\b|\bos\.environ\b|\bgetenv\b|\bdotenv\b|auth\.json|"
        r"\.grok\b|XAI_API_KEY|keyring|\bimport requests\b|\bimport httpx\b|"
        r"\bimport urllib\b|\bimport socket\b|\bimport aiohttp\b"
    )
    for source_file in sorted(PACKAGE_DIR.glob("*.py")):
        assert not deny.search(source_file.read_text()), source_file.name


# ---------------------------------------------------------------------------
# 2. Live mode refuses to start without a Codex-approved harvesting manifest.
# ---------------------------------------------------------------------------


def test_02_live_mode_requires_codex_manifest(tmp_path):
    with pytest.raises(HarvestAuthorizationError, match="no Codex-approved"):
        make_session(tmp_path, mode="live", manifest=None, current_head_sha=HEAD_SHA)

    for bad in (
        make_manifest(approved_by="someone-else"),
        make_manifest(harvesting_enabled=False),
    ):
        with pytest.raises(HarvestAuthorizationError, match="not Codex-approved|disabled"):
            make_session(tmp_path, mode="live", manifest=bad, current_head_sha=HEAD_SHA)

    with pytest.raises(HarvestAuthorizationError, match="approved head"):
        make_session(
            tmp_path, mode="live", manifest=make_manifest(), current_head_sha="0" * 40
        )

    # A fully valid manifest does construct.
    session, _, _ = make_session(
        tmp_path, mode="live", manifest=make_manifest(), current_head_sha=HEAD_SHA
    )
    assert session.mode == "live"


# ---------------------------------------------------------------------------
# 3. Harvesting cannot mutate the active training dataset.
# ---------------------------------------------------------------------------


def test_03_active_training_dataset_is_untouchable(tmp_path):
    session, _, _ = make_session(tmp_path)
    with pytest.raises(HarvestAuthorizationError, match="currently being"):
        session.run([make_request(target_dataset_id="D0001", source_dataset_id="D0000")])

    # Approved (frozen) generations are equally untouchable.
    session2, _, _ = make_session(
        tmp_path, ledger=AttemptLedger(tmp_path / "l2.jsonl"), approved_dataset_ids=("D0002",)
    )
    with pytest.raises(HarvestAuthorizationError, match="approved/frozen"):
        session2.run([make_request()])

    # The contract itself refuses source == target.
    with pytest.raises(ValueError, match="never modifies"):
        make_request(source_dataset_id="D0002", target_dataset_id="D0002")

    # DatasetBuilder refuses to append to an approved generation.
    with pytest.raises(DatasetBuildError, match="approved/frozen"):
        DatasetBuilder(
            function_pack_id="pack-alpha",
            target_dataset_id="D0001",
            approved_dataset_ids=("D0001",),
        )


# ---------------------------------------------------------------------------
# 4. Expired TTL fails closed.
# ---------------------------------------------------------------------------


def test_04_expired_ttl_fails_closed(tmp_path):
    session, transport, ledger = make_session(tmp_path, now_fn=lambda: 99_999.0)
    report = session.run([make_request()])
    assert report["counts"]["expired"] == report["counts"]["planned"] == 4
    assert report["counts"]["accepted"] == 0
    assert transport.calls == []  # expiry precedes dispatch
    assert {row["status"] for row in ledger.rows()} == {"EXPIRED"}

    evaluation = evaluate_candidate(
        transport_status=TransportStatus.OK,
        now=11_000.0,
        expires_at=10_000.0,
        required_artifact_present=True,
        oracle=OracleResult(passed=True, receipt_digest="c" * 40),
    )
    assert evaluation.episode_outcome.value == "EXPIRED"
    assert evaluation.proposal_verdict is ProposalVerdict.NOT_EVALUATED


# ---------------------------------------------------------------------------
# 5. Provider/model/plane or receipt mismatches fail closed.
# ---------------------------------------------------------------------------


class TamperedReceiptTransport:
    def __init__(self, inner: MockTransport, plane: str = "rogue") -> None:
        self.inner = inner
        self.plane = plane

    def discover(self):
        return self.inner.discover()

    def call(self, request):
        result = self.inner.call(request)
        if result.receipt is None:
            return result
        return result.model_copy(
            update={"receipt": result.receipt.model_copy(update={"plane": self.plane})}
        )


def test_05_receipt_mismatch_fails_closed(tmp_path):
    tampered = TamperedReceiptTransport(MockTransport(CATALOG))
    session, _, ledger = make_session(tmp_path, transport=tampered)
    report = session.run([make_request()])
    assert report["counts"]["quarantined"] == 4
    assert report["counts"]["accepted"] == 0
    assert all("receipt mismatch" in row["detail"] for row in ledger.rows())

    # Missing receipt entirely also fails closed.
    class NoReceiptTransport(TamperedReceiptTransport):
        def call(self, request):
            result = self.inner.call(request)
            return result.model_copy(update={"receipt": None})

    session2, _, ledger2 = make_session(
        tmp_path,
        transport=NoReceiptTransport(MockTransport(CATALOG)),
        ledger=AttemptLedger(tmp_path / "l2.jsonl"),
    )
    report2 = session2.run([make_request()])
    assert report2["counts"]["quarantined"] == 4

    # Live mode: provider outside the approved allowlist is quarantined.
    manifest = make_manifest(provider_allowlist=("prov-y",))
    session3, _, ledger3 = make_session(
        tmp_path,
        mode="live",
        manifest=manifest,
        current_head_sha=HEAD_SHA,
        ledger=AttemptLedger(tmp_path / "l3.jsonl"),
    )
    report3 = session3.run(
        [make_request(recipe=make_recipe(donor_allocation={"donor-a": 1.0}))]
    )
    assert report3["counts"]["quarantined"] == 2
    assert any("allowlist" in row["detail"] for row in ledger3.rows())


# ---------------------------------------------------------------------------
# 6. Resumption preserves effect IDs and does not duplicate calls.
# ---------------------------------------------------------------------------


def test_06_resume_preserves_effects_and_never_duplicates_calls(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    request = make_request()

    session1, transport1, ledger1 = make_session(tmp_path, ledger=AttemptLedger(ledger_path))
    report1 = session1.run([request])
    assert report1["counts"]["accepted"] == 4
    first_calls = len(transport1.calls)
    effects_before = ledger1.effect_ids_by_work_key()

    # Fresh session + fresh transport, same ledger file: full resume.
    session2, transport2, ledger2 = make_session(tmp_path, ledger=AttemptLedger(ledger_path))
    report2 = session2.run([request])
    assert transport2.calls == []  # zero duplicated provider calls
    assert report2["counts"]["skipped_resume"] == first_calls == 4
    assert ledger2.effect_ids_by_work_key() == effects_before

    # Deterministic IDs: recomputing yields the same work/effect keys.
    assert request.work_key("donor-a", 0) == make_request().work_key("donor-a", 0)
    assert request.effect_id("donor-a", 0) == make_request().effect_id("donor-a", 0)


# ---------------------------------------------------------------------------
# 7. Sibling variants cannot cross dataset partitions.
# ---------------------------------------------------------------------------


def test_07_sibling_variants_share_one_partition(tmp_path):
    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    splits = set()
    for index in range(6):  # root + 5 variants, one leakage group
        sample = make_sample(
            semantic_root_id=f"root-001-v{index}",
            leakage_group_id="lg-001",
            text=f"variant answer {index}",
        )
        assert builder.ingest(sample)
        splits.add(split_for_leakage_group(sample.leakage_group_id))
    assert len(splits) == 1  # siblings can never straddle partitions

    # The partition is a pure function of the leakage group…
    assert split_for_leakage_group("lg-001") == split_for_leakage_group("lg-001")
    # …and across many groups every split actually gets used.
    all_splits = {split_for_leakage_group(f"lg-{i:04d}") for i in range(200)}
    assert all_splits == {"train", "dev", "holdout"}


# ---------------------------------------------------------------------------
# 8. Cross-function mixing is rejected.
# ---------------------------------------------------------------------------


def test_08_cross_function_mixing_rejected(tmp_path):
    session, _, _ = make_session(tmp_path)
    with pytest.raises(HarvestAuthorizationError, match="cross-function"):
        session.run(
            [
                make_request(),
                make_request(function_pack_id="pack-beta", semantic_root_id="root-002"),
            ]
        )

    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert not builder.ingest(make_sample(function_pack_id="pack-beta"))
    assert builder.rejections[0]["reason"] == "cross-pack mixing rejected"


# ---------------------------------------------------------------------------
# 9. Semantic duplicates are rejected.
# ---------------------------------------------------------------------------


def test_09_semantic_duplicates_rejected():
    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert builder.ingest(make_sample(text="The answer is 42."))
    # Exact duplicate.
    assert not builder.ingest(make_sample(text="The answer is 42."))
    # Semantic duplicate: case/whitespace/quote-style differences only.
    assert not builder.ingest(make_sample(text="  the ANSWER is\u00a042. "))
    reasons = [r["reason"] for r in builder.rejections]
    assert "exact duplicate rejected" in reasons
    assert "semantic duplicate rejected" in reasons
    # A genuinely different answer is admitted.
    assert builder.ingest(make_sample(text="The answer is 43."))


# ---------------------------------------------------------------------------
# 10. Provider failures never become preference negatives.
# ---------------------------------------------------------------------------


def test_10_transport_failures_never_dpo_negatives():
    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert builder.ingest(make_sample(text="winning answer", score=1.0))
    # A timeout with the same root: recorded upstream, but structurally
    # ineligible as a rejected side.
    assert builder.ingest(
        make_sample(
            text="",
            transport_status=TransportStatus.TIMEOUT,
            proposal_verdict=ProposalVerdict.VERIFIED_FAILURE,
            donor_key="donor-b",
            score=0.0,
        )
    )
    assert builder.dpo_view() == []  # no semantic negative -> no pair at all

    # With a genuine semantic failure available, the pair uses it instead.
    assert builder.ingest(
        make_sample(
            text="confidently wrong answer",
            proposal_verdict=ProposalVerdict.VERIFIED_FAILURE,
            donor_key="donor-b",
            score=0.4,
        )
    )
    pairs = builder.dpo_view()
    assert len(pairs) == 1
    assert pairs[0]["rejected"]["text"] == "confidently wrong answer"
    assert pairs[0]["pairing"] == "winner-vs-verified-failure"


# ---------------------------------------------------------------------------
# 11. Chosen/rejected contract mismatches are rejected.
# ---------------------------------------------------------------------------


def test_11_pair_contract_mismatch_rejected():
    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert builder.ingest(make_sample(text="winner", score=1.0))
    assert builder.ingest(
        make_sample(
            text="loser under a drifted tool catalog",
            proposal_verdict=ProposalVerdict.VERIFIED_FAILURE,
            tool_catalog_digest="d" * 40,
            donor_key="donor-b",
        )
    )
    with pytest.raises(DatasetBuildError, match="tool_catalog"):
        builder.dpo_view()

    # A rationale can never be paired against an answer: differing
    # response types make the candidate ineligible outright.
    builder2 = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert builder2.ingest(make_sample(text="winner", score=1.0))
    assert builder2.ingest(
        make_sample(
            text="a plan-state rationale",
            response_type="plan_state",
            proposal_verdict=ProposalVerdict.VERIFIED_FAILURE,
            donor_key="donor-b",
            visible_fields=("plan_state",),
        )
    )
    assert builder2.dpo_view() == []


# ---------------------------------------------------------------------------
# 12. Judge disagreement stays provisional or indeterminate.
# ---------------------------------------------------------------------------


def test_12_judges_cannot_mint_verified_success(tmp_path):
    agree = (JudgeVote("judge-1", True), JudgeVote("judge-2", True))
    split = (JudgeVote("judge-1", True), JudgeVote("judge-2", False))

    unanimous = evaluate_candidate(
        transport_status=TransportStatus.OK,
        now=NOW,
        expires_at=10_000.0,
        required_artifact_present=True,
        judge_votes=agree,
    )
    assert unanimous.proposal_verdict is ProposalVerdict.JUDGE_PROVISIONAL

    disagreement = evaluate_candidate(
        transport_status=TransportStatus.OK,
        now=NOW,
        expires_at=10_000.0,
        required_artifact_present=True,
        judge_votes=split,
    )
    assert disagreement.proposal_verdict is ProposalVerdict.INDETERMINATE
    assert disagreement.episode_outcome.value == "QUARANTINED"

    # Through the harvester with blinded judges that keep disagreeing even
    # after the bounded adjudication round: quarantined, never accepted.
    session, _, ledger = make_session(
        tmp_path,
        mechanical_fn=lambda request, text: MechanicalCheck(artifact_present=True),
        judge_fn=lambda objective, text: split,
        adjudicate_fn=lambda objective, text: (JudgeVote("judge-3", True),),
        ceilings=make_ceilings(),
    )
    report = session.run(
        [make_request(recipe=make_recipe(donor_allocation={"donor-a": 1.0}, samples_per_donor=1))]
    )
    assert report["counts"]["quarantined"] == 1
    assert report["counts"]["accepted"] == 0
    verdicts = {row["proposal_verdict"] for row in ledger.rows() if row["proposal_verdict"]}
    assert verdicts <= {"INDETERMINATE", "JUDGE_PROVISIONAL"}
    assert "VERIFIED_SUCCESS" not in verdicts


# ---------------------------------------------------------------------------
# 13. Retry and adjudication calls count against hard ceilings.
# ---------------------------------------------------------------------------


def test_13_retries_and_adjudication_charge_the_budget(tmp_path):
    # All calls time out; retries burn the shared provider-call ceiling and
    # the remaining work fails closed as BUDGET_EXHAUSTED.
    cassette = {"donor-a": [{"status": "TIMEOUT"}], "donor-b": [{"status": "TIMEOUT"}]}
    transport = MockTransport(CATALOG, cassette=cassette)
    session, _, ledger = make_session(
        tmp_path,
        transport=transport,
        ceilings=make_ceilings(max_provider_calls=3, max_retries=2),
    )
    report = session.run([make_request()])
    assert report["budget"]["provider_calls"] == 3  # never exceeds the ceiling
    assert report["budget"]["retries"] == 2
    assert report["counts"]["budget_stopped"] >= 1
    statuses = {row["status"] for row in ledger.rows()}
    assert "BUDGET_EXHAUSTED" in statuses
    assert "RETRIED" in statuses
    assert len(transport.calls) == 3

    # Adjudication charges the same meter: 1 transport + 1 judge + 1
    # adjudication = 3 provider calls for a single work item.
    split = (JudgeVote("judge-1", True), JudgeVote("judge-2", False))
    session2, transport2, _ = make_session(
        tmp_path,
        ledger=AttemptLedger(tmp_path / "l2.jsonl"),
        mechanical_fn=lambda request, text: MechanicalCheck(artifact_present=True),
        judge_fn=lambda objective, text: split,
        adjudicate_fn=lambda objective, text: (JudgeVote("judge-3", True),),
    )
    report2 = session2.run(
        [make_request(recipe=make_recipe(donor_allocation={"donor-a": 1.0}, samples_per_donor=1))]
    )
    assert report2["budget"]["provider_calls"] == 3
    assert len(transport2.calls) == 1


# ---------------------------------------------------------------------------
# 14. Hidden CoT cannot enter artifacts.
# ---------------------------------------------------------------------------


def test_14_hidden_cot_cannot_enter_artifacts(tmp_path):
    # Contract depth: unknown fields are rejected at parse time.
    with pytest.raises(Exception, match="chain_of_thought"):
        HarvestRequest(**{**make_request().model_dump(), "chain_of_thought": "secret"})
    with pytest.raises(Exception):
        make_request(response_type="chain_of_thought")

    # Ingestion depth: forbidden raw fields and non-visible surfaces.
    builder = DatasetBuilder(function_pack_id="pack-alpha", target_dataset_id="D0002")
    assert not builder.ingest(make_sample(), raw_fields={"reasoning": "hidden steps"})
    assert not builder.ingest(
        make_sample(text="other"), raw_fields={"scratchpad": "hidden"}
    )
    assert not builder.ingest(
        make_sample(text="third", visible_fields=("reasoning",))
    )
    assert all("chain-of-thought" in r["reason"] for r in builder.rejections)

    # Artifact depth: shards contain only whitelisted visible surfaces.
    assert builder.ingest(make_sample(text="clean answer"))
    manifest = builder.write_shards(tmp_path / "shards")
    for shard in (tmp_path / "shards").glob("*.jsonl"):
        for line in shard.read_text().splitlines():
            row = json.loads(line)
            forbidden = {"reasoning", "chain_of_thought", "cot", "scratchpad", "thinking"}
            assert not (set(row) & forbidden)
    assert manifest["rejections"] == 3


# ---------------------------------------------------------------------------
# 15. No training or sealed evaluation can occur from the harvester.
# ---------------------------------------------------------------------------


def test_15_training_and_sealed_eval_are_unauthorizable(tmp_path):
    assert "train" not in ALLOWED_EFFECTS
    assert "sealed_evaluation" not in ALLOWED_EFFECTS
    for effect in ("train", "training", "sealed_evaluation", "sealed_eval_read"):
        with pytest.raises(ValueError, match="never be authorized"):
            make_request(authorized_effects=(effect,))

    # The report structurally denies both, and the public API exposes no
    # training or sealed-evaluation entry point.
    session, _, _ = make_session(tmp_path)
    report = session.run([make_request()])
    assert report["authorizes_training"] is False
    assert report["authorizes_sealed_evaluation"] is False

    import evals.needle_harvest as pkg

    banned = re.compile(r"train|sealed", re.IGNORECASE)
    assert not [name for name in pkg.__all__ if banned.search(name)]


# ---------------------------------------------------------------------------
# 16. The same mock inputs produce identical manifests and digests.
# ---------------------------------------------------------------------------


def test_16_same_inputs_identical_manifests_and_digests(tmp_path):
    reports = []
    manifests = []
    for run_index in range(2):
        run_dir = tmp_path / f"run{run_index}"
        session, _, _ = make_session(run_dir, ledger=AttemptLedger(run_dir / "ledger.jsonl"))
        report = session.run([make_request()], shard_dir=run_dir / "shards")
        manifests.append((run_dir / "shards" / "manifest.json").read_bytes())
        reports.append(report)

    assert reports[0]["report_sha256"] == reports[1]["report_sha256"]
    assert reports[0] == reports[1]
    assert manifests[0] == manifests[1]  # byte-identical manifest files
    shard_digests = {
        name: info["sha256"] for name, info in reports[0]["dataset_manifest"]["shards"].items()
    }
    assert shard_digests == {
        name: info["sha256"] for name, info in reports[1]["dataset_manifest"]["shards"].items()
    }


# ---------------------------------------------------------------------------
# Supporting proofs: ledger failure classes and structural incompleteness.
# ---------------------------------------------------------------------------


def test_ledger_records_all_failure_classes(tmp_path):
    cassette = {
        "donor-a": [{"status": "EMPTY"}],
        "donor-b": [{"status": "MALFORMED"}],
    }
    transport = MockTransport(CATALOG, cassette=cassette)
    session, _, ledger = make_session(
        tmp_path, transport=transport, ceilings=make_ceilings(max_retries=0)
    )
    report = session.run(
        [make_request(recipe=make_recipe(samples_per_donor=1))]
    )
    assert report["counts"]["transport_failure"] == 2
    recorded = {row["transport_status"] for row in ledger.rows()}
    assert recorded == {"EMPTY", "MALFORMED"}
    # Rows are append-only and replayable from disk.
    replay = AttemptLedger(ledger.path)
    assert replay.rows() == ledger.rows()


def test_wording_never_beats_missing_artifacts():
    # "I have completed the task" with no artifact is structurally
    # incomplete regardless of phrasing — no promise-phrase regex exists.
    evaluation = evaluate_candidate(
        transport_status=TransportStatus.OK,
        now=NOW,
        expires_at=10_000.0,
        required_artifact_present=False,
        judge_votes=(JudgeVote("judge-1", True), JudgeVote("judge-2", True)),
    )
    assert evaluation.proposal_verdict is ProposalVerdict.STRUCTURALLY_INCOMPLETE
    assert evaluation.episode_outcome.value == "FAILURE"


def test_variant_requests_are_request_only(tmp_path):
    # Failed cells surface as targeted variant *requests* that nothing in
    # this package executes automatically.
    cassette = {"donor-a": [{"status": "OK", "text": "wrong"}]}
    transport = MockTransport(CATALOG, cassette=cassette)

    def mech(request, text):
        good = text.startswith("mock-answer-")
        return MechanicalCheck(
            artifact_present=True,
            oracle=OracleResult(passed=good, receipt_digest="c" * 40),
            score=1.0 if good else 0.0,
            confusion_cell="" if good else "cell-a-vs-b",
        )

    session, _, _ = make_session(tmp_path, transport=transport, mechanical_fn=mech)
    report = session.run(
        [make_request(recipe=make_recipe(donor_allocation={"donor-a": 1.0}, samples_per_donor=1))]
    )
    assert report["counts"]["verified_failure"] == 1
    assert [v["confusion_cell"] for v in report["variant_requests"]] == ["cell-a-vs-b"]
    assert report["next_step"] == "codex review and dataset freeze"
