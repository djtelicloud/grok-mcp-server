from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from hmac import compare_digest
from typing import Any, Callable, Iterable

from .role_schemas import FrozenScenarioInput
from .schemas import (
    BaseRootEnvelope,
    EpisodeOutcome,
    ProposalVerdict,
    ResultType,
    TTLState,
    canonical_sha256,
    derive_ttl_state,
    is_sha256,
)


_SECRET_VALUE_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"\bAIza[A-Za-z0-9_-]{25,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\bBearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/=-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:^|[^A-Za-z0-9_])["']?[A-Z0-9_-]*(?:API[_-]?KEY|SESSION[_-]?TOKEN|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)["']?\s*[:=]\s*(?!["']?(?:<|\$\{|\[))(?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[A-Za-z0-9._~+/=@!#$%^&*()-]{8,})""",
        re.IGNORECASE,
    ),
)
_PII_VALUE_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
    ),
    re.compile(r"(?:^|[\s\"'])(?:/Users/|/home/)[^\s\"']+"),
    re.compile(r"(?:^|[\s\"'])[A-Za-z]:\\Users\\[^\s\"']+", re.IGNORECASE),
)


def _provider_projection(envelope: BaseRootEnvelope) -> dict[str, Any]:
    """Return only fields that can contain provider-authored content.

    Harness authority, receipts, digests, lineage, and IDs are intentionally
    absent.  Scanning those random-looking values as prose creates unstable PII
    false positives (for example, a digest can contain ten consecutive digits).
    """

    return {
        "objective": envelope.objective,
        "observations": envelope.observations,
        "capabilities": envelope.capabilities,
        "forbidden_effects": envelope.forbidden_effects,
        "result": envelope.result.model_dump(mode="json"),
        "decision_summary": envelope.decision_summary,
        "plan_state": envelope.plan_state,
    }


def _iter_text_values(value: Any) -> Iterator[str]:
    """Yield individual provider strings without coercing numeric IDs to PII."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _iter_text_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from _iter_text_values(child)


class MechanicalValidators:
    @staticmethod
    def scan_provider_payload(value: Any) -> bool:
        """Reject secrets, contact PII, and user paths in untrusted role content."""

        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        if any(pattern.search(serialized) for pattern in _SECRET_VALUE_PATTERNS):
            return False
        return not any(
            pattern.search(text)
            for text in _iter_text_values(value)
            for pattern in _PII_VALUE_PATTERNS
        )

    @staticmethod
    def scan_unsafe_credentials(envelope: BaseRootEnvelope) -> bool:
        """Scan only provider fields, excluding random harness IDs and receipts."""

        return MechanicalValidators.scan_provider_payload(
            _provider_projection(envelope)
        )

    @staticmethod
    def verify_no_exact_duplicates(
        artifact_digests: set[str], new_envelope: BaseRootEnvelope
    ) -> bool:
        """Recompute integrity and reject both forgeries and known artifacts."""
        supplied = new_envelope.immutable_artifact_digest
        expected = new_envelope.compute_immutable_artifact_digest()
        if supplied is not None and not compare_digest(supplied, expected):
            return False
        return expected not in artifact_digests

    @staticmethod
    def verify_no_semantic_duplicate(
        semantic_signatures: set[str], new_envelope: BaseRootEnvelope
    ) -> bool:
        """Recompute semantic identity; never trust a supplied signature."""
        supplied = new_envelope.semantic_signature
        expected = new_envelope.compute_semantic_signature()
        if supplied is not None and not compare_digest(supplied, expected):
            return False
        return expected not in semantic_signatures

    @staticmethod
    def verify_leakage_group(
        parent_envelope: BaseRootEnvelope, child_envelope: BaseRootEnvelope
    ) -> bool:
        return parent_envelope.leakage_group == child_envelope.leakage_group

    @staticmethod
    def derive_frozen_ttl_state(envelope: BaseRootEnvelope) -> TTLState:
        assert envelope.evaluated_at is not None
        return derive_ttl_state(
            issued_at=envelope.issued_at,
            expires_at=envelope.expires_at,
            evaluated_at=envelope.evaluated_at,
            soft_stale_at=envelope.soft_stale_at,
            revocation_facts=envelope.revocation_facts,
            post_ttl_facts=envelope.post_ttl_facts,
            structural_invalidators=envelope.structural_invalidators,
        )

    @staticmethod
    def verify_frozen_ttl_state(envelope: BaseRootEnvelope) -> bool:
        return (
            envelope.declared_ttl_state
            == MechanicalValidators.derive_frozen_ttl_state(envelope)
        )

    @staticmethod
    def verify_frozen_scenario(
        envelope: BaseRootEnvelope, scenario: FrozenScenarioInput
    ) -> bool:
        """Bind model-visible TTL and expected truth to harness-owned context."""

        expected_result_digest = scenario.expected_result_digest
        oracle_expected = envelope.mechanical_oracle.deterministic_parameters.get(
            "expected_result_digest"
        )
        return all(
            (
                scenario.scenario_digest == scenario.compute_scenario_digest(),
                envelope.pack_name == scenario.pack_name,
                envelope.root_id == scenario.root_reference,
                envelope.ttl_facts() == scenario.ttl,
                envelope.evaluated_at == scenario.ttl.evaluated_at,
                envelope.output_contract_digest == scenario.output_contract_digest,
                envelope.tool_catalog_digest == scenario.tool_catalog_digest,
                envelope.schema_digest == scenario.schema_digest,
                envelope.prompt_template_digest == scenario.prompt_template_digest,
                envelope.generator_config_digest == scenario.generator_config_digest,
                oracle_expected == expected_result_digest,
            )
        )

    @staticmethod
    def verify_ttl_state(
        envelope: BaseRootEnvelope, current_utc_time: datetime
    ) -> bool:
        """Compatibility verifier for Stage 0; strict code uses the frozen clock."""
        derived = derive_ttl_state(
            issued_at=envelope.issued_at,
            expires_at=envelope.expires_at,
            evaluated_at=current_utc_time,
            soft_stale_at=envelope.soft_stale_at,
            revocation_facts=envelope.revocation_facts,
            post_ttl_facts=envelope.post_ttl_facts,
            structural_invalidators=envelope.structural_invalidators,
        )
        return envelope.declared_ttl_state == derived

    @staticmethod
    def compute_oracle_input_digest(envelope: BaseRootEnvelope) -> str | None:
        oracle = envelope.mechanical_oracle
        resolved: dict[str, Any] = {}
        source: Any = envelope.model_dump(mode="json", exclude_none=False)
        for path in oracle.declared_inputs:
            value: Any = source
            for component in path.split("."):
                if not isinstance(value, dict) or component not in value:
                    return None
                value = value[component]
            resolved[path] = value
        return canonical_sha256(resolved)

    @staticmethod
    def verify_oracle_contract(envelope: BaseRootEnvelope) -> bool:
        """Verify code, declared inputs, output, effect, and execution receipt."""
        oracle = envelope.mechanical_oracle
        receipt = oracle.execution_receipt
        if receipt is None or oracle.pass_fail_result is None:
            return False
        if not (
            is_sha256(oracle.code_digest)
            and is_sha256(oracle.declared_input_digest)
            and is_sha256(oracle.output_digest)
        ):
            return False
        if (
            "stable_effect_id" not in oracle.declared_inputs
            or "result" not in oracle.declared_inputs
        ):
            return False
        expected_input = MechanicalValidators.compute_oracle_input_digest(envelope)
        if expected_input is None or not compare_digest(
            oracle.declared_input_digest or "", expected_input
        ):
            return False
        if oracle.oracle_output is None:
            return False
        expected_output = canonical_sha256(oracle.oracle_output)
        if not compare_digest(oracle.output_digest or "", expected_output):
            return False
        if not all(
            (
                receipt.receipt_spec_version,
                receipt.oracle_name,
                receipt.oracle_version,
                receipt.oracle_code_digest,
                receipt.declared_input_digest,
            )
        ):
            return False
        if not is_sha256(receipt.verifier_code_digest):
            return False
        if receipt.effect_id != envelope.stable_effect_id:
            return False
        if (
            receipt.oracle_name != oracle.name
            or receipt.oracle_version != oracle.version
        ):
            return False
        if receipt.oracle_code_digest != oracle.code_digest:
            return False
        if receipt.declared_input_digest != oracle.declared_input_digest:
            return False
        if receipt.observation_timestamp != envelope.evaluated_at:
            return False
        if not receipt.verification_result:
            return False
        if receipt.observed_content is None:
            return False
        computed_content = receipt.compute_content_digest()
        if computed_content is None or not compare_digest(
            receipt.content_digest, computed_content
        ):
            return False
        return compare_digest(receipt.content_digest, oracle.output_digest or "")

    @staticmethod
    def compute_receipt_input_digest(
        envelope: BaseRootEnvelope, receipt_spec_id: str
    ) -> str:
        return canonical_sha256(
            {
                "receipt_spec_id": receipt_spec_id,
                "effect_id": envelope.stable_effect_id,
                "result": envelope.result.model_dump(mode="json"),
            }
        )

    @staticmethod
    def verify_receipts_bound(envelope: BaseRootEnvelope) -> bool:
        required = set(envelope.required_receipt_specs)
        if len(required) != len(envelope.required_receipt_specs):
            return False
        observed_by_spec = {
            receipt.receipt_spec_id: receipt for receipt in envelope.observed_receipts
        }
        if len(observed_by_spec) != len(envelope.observed_receipts):
            return False
        if set(observed_by_spec) != required:
            return False
        for spec_id, receipt in observed_by_spec.items():
            if not (
                receipt.receipt_spec_version
                and is_sha256(receipt.verifier_code_digest)
                and is_sha256(receipt.declared_input_digest)
                and receipt.observed_content is not None
            ):
                return False
            if receipt.effect_id != envelope.stable_effect_id:
                return False
            if receipt.observation_timestamp != envelope.evaluated_at:
                return False
            expected_input = MechanicalValidators.compute_receipt_input_digest(
                envelope, spec_id
            )
            if not compare_digest(receipt.declared_input_digest or "", expected_input):
                return False
            expected_content = receipt.compute_content_digest()
            if expected_content is None or not compare_digest(
                receipt.content_digest, expected_content
            ):
                return False
        return True

    @staticmethod
    def verify_receipts_complete(envelope: BaseRootEnvelope) -> bool:
        """Stage 0 compatibility check; strict authority uses bound receipts."""
        required = set(envelope.required_receipt_specs)
        observed = {
            receipt.receipt_spec_id
            for receipt in envelope.observed_receipts
            if receipt.verification_result
        }
        if not required.issubset(observed):
            return False
        return all(
            len(receipt.content_digest) >= 32 for receipt in envelope.observed_receipts
        )

    @staticmethod
    def derive_proposal_verdict(envelope: BaseRootEnvelope) -> ProposalVerdict:
        if not MechanicalValidators.verify_oracle_contract(envelope):
            return ProposalVerdict.UNVERIFIED
        if not envelope.mechanical_oracle.pass_fail_result:
            return ProposalVerdict.INVALID_PROPOSAL
        if (
            envelope.declared_ttl_state != TTLState.FRESH
            and envelope.result.type == ResultType.ACTION
        ):
            return ProposalVerdict.INVALID_PROPOSAL
        return ProposalVerdict.VALID_PROPOSAL

    @staticmethod
    def derive_episode_outcome(envelope: BaseRootEnvelope) -> EpisodeOutcome:
        """Derive outcome exclusively from frozen mechanical evidence."""
        verdict = MechanicalValidators.derive_proposal_verdict(envelope)
        if verdict == ProposalVerdict.UNVERIFIED:
            return EpisodeOutcome.UNVERIFIED
        if verdict == ProposalVerdict.INVALID_PROPOSAL:
            return EpisodeOutcome.VERIFIED_FAILURE
        if not MechanicalValidators.verify_frozen_ttl_state(envelope):
            return EpisodeOutcome.VERIFIED_FAILURE
        if envelope.declared_ttl_state != TTLState.FRESH:
            return EpisodeOutcome.VERIFIED_FAILURE
        if not MechanicalValidators.verify_receipts_bound(envelope):
            return EpisodeOutcome.VERIFIED_FAILURE
        if not all(
            receipt.verification_result for receipt in envelope.observed_receipts
        ):
            return EpisodeOutcome.VERIFIED_FAILURE
        if envelope.result.type in (
            ResultType.DURABLE_WAIT,
            ResultType.REQUEST_VERIFICATION,
        ):
            return EpisodeOutcome.UNVERIFIED
        return EpisodeOutcome.VERIFIED_SUCCESS

    @staticmethod
    def verify_declared_authority(envelope: BaseRootEnvelope) -> bool:
        return (
            envelope.proposal_verdict
            == MechanicalValidators.derive_proposal_verdict(envelope)
            and envelope.episode_outcome
            == MechanicalValidators.derive_episode_outcome(envelope)
        )

    @staticmethod
    def evaluate_episode_outcome(
        envelope: BaseRootEnvelope, current_utc_time: datetime
    ) -> EpisodeOutcome:
        """Stage 0 compatibility wrapper; new harnesses call derive_episode_outcome."""
        if envelope.episode_outcome == EpisodeOutcome.UNVERIFIED:
            return EpisodeOutcome.UNVERIFIED
        if envelope.episode_outcome == EpisodeOutcome.VERIFIED_SUCCESS:
            oracle = envelope.mechanical_oracle
            if not oracle.execution_receipt or not oracle.pass_fail_result:
                return EpisodeOutcome.VERIFIED_FAILURE
            if not MechanicalValidators.verify_receipts_complete(envelope):
                return EpisodeOutcome.VERIFIED_FAILURE
            if not MechanicalValidators.verify_ttl_state(envelope, current_utc_time):
                return EpisodeOutcome.VERIFIED_FAILURE
            if envelope.declared_ttl_state != TTLState.FRESH:
                return EpisodeOutcome.VERIFIED_FAILURE
            if envelope.result.type in (
                ResultType.DURABLE_WAIT,
                ResultType.REQUEST_VERIFICATION,
            ):
                return EpisodeOutcome.VERIFIED_FAILURE
        return envelope.episode_outcome

    @staticmethod
    def verify_no_duplicate_semantic_effect(
        trajectory_effect_ids: Iterable[str], envelope: BaseRootEnvelope
    ) -> bool:
        if envelope.result.type != ResultType.ACTION:
            return True
        expected = envelope.compute_stable_effect_id()
        if not compare_digest(envelope.stable_effect_id, expected):
            return False
        return expected not in set(trajectory_effect_ids)

    @staticmethod
    def strict_gate(
        envelope: BaseRootEnvelope,
        *,
        trusted_scenario: FrozenScenarioInput,
        artifact_digests: set[str],
        semantic_signatures: set[str],
        trajectory_effect_ids: Iterable[str],
        executable_oracle_verifier: Callable[[BaseRootEnvelope], bool],
        executable_receipt_verifier: Callable[
            [BaseRootEnvelope, FrozenScenarioInput], bool
        ],
    ) -> tuple[bool, tuple[str, ...]]:
        """One fail-closed integration API returning stable reason codes."""
        checks = {
            "unsafe_content": MechanicalValidators.scan_unsafe_credentials(envelope),
            "scenario_binding": MechanicalValidators.verify_frozen_scenario(
                envelope, trusted_scenario
            ),
            "ttl_mismatch": MechanicalValidators.verify_frozen_ttl_state(envelope),
            "oracle_binding": (
                MechanicalValidators.verify_oracle_contract(envelope)
                and executable_oracle_verifier(envelope)
            ),
            "receipt_binding": (
                MechanicalValidators.verify_receipts_bound(envelope)
                and executable_receipt_verifier(envelope, trusted_scenario)
            ),
            "authority_mismatch": MechanicalValidators.verify_declared_authority(
                envelope
            ),
            "artifact_duplicate_or_forgery": MechanicalValidators.verify_no_exact_duplicates(
                artifact_digests, envelope
            ),
            "semantic_duplicate_or_forgery": MechanicalValidators.verify_no_semantic_duplicate(
                semantic_signatures, envelope
            ),
            "effect_duplicate_or_forgery": MechanicalValidators.verify_no_duplicate_semantic_effect(
                trajectory_effect_ids, envelope
            ),
        }
        failures = tuple(code for code, passed in checks.items() if not passed)
        return not failures, failures
