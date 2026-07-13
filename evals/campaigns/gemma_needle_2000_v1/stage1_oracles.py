"""Executable, digest-pinned mechanical oracles for the Stage 1 safety gate."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any

from .role_schemas import FrozenScenarioInput
from .schemas import (
    BaseRootEnvelope,
    OracleRegistryContract,
    Receipt,
    canonical_sha256,
)
from .validators import MechanicalValidators


OracleHandler = Callable[[BaseRootEnvelope, Mapping[str, Any]], dict[str, Any]]


def _handler_digest(handler: OracleHandler) -> str:
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError) as exc:
        raise ValueError(
            "Mechanical oracle handlers must have inspectable source."
        ) from exc
    return canonical_sha256({"source": source})


@dataclass(frozen=True)
class OracleDefinition:
    name: str
    version: str
    declared_inputs: tuple[str, ...]
    code_digest: str
    handler: OracleHandler


class ExecutableOracleRegistry:
    """Register real callables and verify attestations by re-executing them."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], OracleDefinition] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        declared_inputs: Sequence[str],
        handler: OracleHandler,
    ) -> OracleDefinition:
        key = (name, version)
        if key in self._definitions:
            raise ValueError(
                f"Mechanical oracle {name!r} version {version!r} is registered."
            )
        definition = OracleDefinition(
            name=name,
            version=version,
            declared_inputs=tuple(declared_inputs),
            code_digest=_handler_digest(handler),
            handler=handler,
        )
        self._definitions[key] = definition
        return definition

    def definition(self, name: str, version: str) -> OracleDefinition:
        try:
            return self._definitions[(name, version)]
        except KeyError as exc:
            raise KeyError(
                f"Unknown mechanical oracle {name!r} version {version!r}."
            ) from exc

    @staticmethod
    def _verifier_code_digest() -> str:
        return canonical_sha256(
            {"source": inspect.getsource(ExecutableOracleRegistry.verify)}
        )

    def attest(
        self,
        envelope: BaseRootEnvelope,
        *,
        name: str,
        version: str,
        deterministic_parameters: Mapping[str, Any],
    ) -> OracleRegistryContract:
        """Execute a registered oracle and return a fully bound contract."""

        definition = self.definition(name, version)
        placeholder = OracleRegistryContract(
            name=definition.name,
            version=definition.version,
            code_digest=definition.code_digest,
            declared_inputs=list(definition.declared_inputs),
            deterministic_parameters=dict(deterministic_parameters),
        )
        envelope.mechanical_oracle = placeholder
        declared_input_digest = MechanicalValidators.compute_oracle_input_digest(
            envelope
        )
        if declared_input_digest is None:
            raise ValueError("Mechanical oracle declared inputs cannot be resolved.")
        oracle_output = definition.handler(envelope, dict(deterministic_parameters))
        if not isinstance(oracle_output, dict) or not isinstance(
            oracle_output.get("passed"), bool
        ):
            raise ValueError(
                "Mechanical oracle output must be an object with boolean passed."
            )
        output_digest = canonical_sha256(oracle_output)
        receipt = Receipt(
            receipt_spec_id="oracle_execution",
            receipt_spec_version="1.0",
            issuer_identity="mechanical_oracle",
            verifier_identity="stage1_harness",
            verifier_code_digest=self._verifier_code_digest(),
            effect_id=envelope.stable_effect_id,
            observation_timestamp=envelope.evaluated_at,
            verification_result=True,
            content_digest=output_digest,
            declared_input_digest=declared_input_digest,
            oracle_name=definition.name,
            oracle_version=definition.version,
            oracle_code_digest=definition.code_digest,
            observed_content=oracle_output,
        )
        contract = OracleRegistryContract(
            name=definition.name,
            version=definition.version,
            code_digest=definition.code_digest,
            declared_inputs=list(definition.declared_inputs),
            deterministic_parameters=dict(deterministic_parameters),
            declared_input_digest=declared_input_digest,
            oracle_output=oracle_output,
            execution_receipt=receipt,
            pass_fail_result=oracle_output["passed"],
            output_digest=output_digest,
        )
        envelope.mechanical_oracle = contract
        return contract

    def verify(self, envelope: BaseRootEnvelope) -> bool:
        """Re-execute the pinned callable and compare every recorded field."""

        contract = envelope.mechanical_oracle
        try:
            definition = self.definition(contract.name, contract.version)
        except KeyError:
            return False
        if not (
            compare_digest(contract.code_digest, definition.code_digest)
            and tuple(contract.declared_inputs) == definition.declared_inputs
            and MechanicalValidators.verify_oracle_contract(envelope)
        ):
            return False
        expected_input = MechanicalValidators.compute_oracle_input_digest(envelope)
        if expected_input is None or not compare_digest(
            expected_input, contract.declared_input_digest or ""
        ):
            return False
        try:
            expected_output = definition.handler(
                envelope, dict(contract.deterministic_parameters)
            )
        except Exception:
            return False
        if expected_output != contract.oracle_output:
            return False
        expected_digest = canonical_sha256(expected_output)
        return compare_digest(
            expected_digest, contract.output_digest or ""
        ) and contract.pass_fail_result is expected_output.get("passed")


def expected_result_digest_oracle(
    envelope: BaseRootEnvelope,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare the candidate result to immutable expected-result truth."""

    expected_digest = parameters.get("expected_result_digest")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("expected_result_digest must be a SHA-256 digest.")
    candidate_digest = canonical_sha256(envelope.result.model_dump(mode="json"))
    return {
        "candidate_result_digest": candidate_digest,
        "expected_result_digest": expected_digest,
        "passed": compare_digest(candidate_digest, expected_digest),
    }


def default_oracle_registry() -> ExecutableOracleRegistry:
    registry = ExecutableOracleRegistry()
    registry.register(
        name="expected_result_digest",
        version="1.0",
        declared_inputs=("stable_effect_id", "result"),
        handler=expected_result_digest_oracle,
    )
    return registry


def expected_effect_observation(
    envelope: BaseRootEnvelope,
    scenario: FrozenScenarioInput,
) -> dict[str, Any]:
    """Read the bound mock environment fixture for one candidate effect."""

    return {
        "effect_id": envelope.stable_effect_id,
        "effect_observed": scenario.expected_effect_observed,
        "result_digest": canonical_sha256(envelope.result.model_dump(mode="json")),
        "scenario_digest": scenario.scenario_digest,
    }


def _effect_verifier_code_digest() -> str:
    return canonical_sha256(
        {
            "source": inspect.getsource(expected_effect_observation),
            "spec": "effect_observation:1.0",
        }
    )


def attest_effect_receipt(
    envelope: BaseRootEnvelope,
    scenario: FrozenScenarioInput,
) -> Receipt:
    """Execute the pinned effect verifier and issue one bound receipt."""

    if not MechanicalValidators.verify_frozen_scenario(envelope, scenario):
        raise ValueError(
            "Effect receipt scenario does not bind the candidate envelope."
        )
    observed_content = expected_effect_observation(envelope, scenario)
    return Receipt(
        receipt_spec_id="effect_observation",
        receipt_spec_version="1.0",
        issuer_identity="mock_environment",
        verifier_identity="stage1_harness",
        verifier_code_digest=_effect_verifier_code_digest(),
        effect_id=envelope.stable_effect_id,
        observation_timestamp=envelope.evaluated_at,
        verification_result=scenario.expected_effect_observed,
        content_digest=canonical_sha256(observed_content),
        declared_input_digest=MechanicalValidators.compute_receipt_input_digest(
            envelope, "effect_observation"
        ),
        observed_content=observed_content,
    )


def verify_effect_receipt(
    envelope: BaseRootEnvelope,
    scenario: FrozenScenarioInput,
) -> bool:
    """Re-execute the effect verifier; self-consistent receipt fields are insufficient."""

    if not MechanicalValidators.verify_frozen_scenario(envelope, scenario):
        return False
    receipts = [
        receipt
        for receipt in envelope.observed_receipts
        if receipt.receipt_spec_id == "effect_observation"
    ]
    if len(receipts) != 1:
        return False
    receipt = receipts[0]
    expected_content = expected_effect_observation(envelope, scenario)
    expected_digest = canonical_sha256(expected_content)
    expected_input = MechanicalValidators.compute_receipt_input_digest(
        envelope, "effect_observation"
    )
    return all(
        (
            receipt.receipt_spec_version == "1.0",
            receipt.issuer_identity == "mock_environment",
            receipt.verifier_identity == "stage1_harness",
            compare_digest(
                receipt.verifier_code_digest or "", _effect_verifier_code_digest()
            ),
            receipt.effect_id == envelope.stable_effect_id,
            receipt.observation_timestamp == scenario.ttl.evaluated_at,
            receipt.verification_result is scenario.expected_effect_observed,
            receipt.observed_content == expected_content,
            compare_digest(receipt.content_digest, expected_digest),
            compare_digest(receipt.declared_input_digest or "", expected_input),
        )
    )
