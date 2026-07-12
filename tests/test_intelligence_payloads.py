import copy
import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest

from src import intelligence_capsule as capsule
from src import intelligence_payloads as payloads


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "intelligence_capsule" / "v1" / "golden-envelope.json"
CONFORMANCE = ROOT / "docs" / "okf" / "intelligence-payload-conformance-v1.json"


def _capsule_id(digit: str) -> str:
    return f"ucap1:sha256:{digit * 64}"


def _evidence(name: str, raw: bytes, media_type: str = "text/plain") -> dict:
    return {
        "bytes": len(raw),
        "media_type": media_type,
        "name": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _ref(descriptor: dict) -> dict:
    return {
        "evidence_name": descriptor["name"],
        "sha256": descriptor["sha256"],
    }


def _body(
    *,
    kind: str,
    schema: str,
    data: dict,
    evidence: list[dict],
    parents: list[str],
    metrics: list[dict] | None = None,
) -> dict:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]
    base["kind"] = kind
    base["parents"] = sorted(parents)
    base["evidence"] = sorted(evidence, key=lambda item: (item["name"], item["sha256"]))
    base["payload"] = {"data": data, "schema": schema}
    if schema == payloads.GNO_ENVELOPE_SCHEMA and data.get("phase") == "result":
        base.pop("execution", None)
    if metrics is None:
        base.pop("metrics", None)
    else:
        base["metrics"] = metrics
    return base


def _validate_known_body(body: dict) -> None:
    capsule.validate_body(body)
    assert payloads.validate_known_payload_profile(body) is True


def _gno_dispatch_with_blobs() -> tuple[dict, dict[str, bytes]]:
    task_spec = _evidence("task-spec", b"optimize foo without ambient reads")
    routing = {
        "algorithm": "discounted_ucb_v1",
        "arm": "hot_loop",
        "arm_pull": 7,
        "parameters": [
            {"name": "decay", "value": "0.95"},
            {"name": "exploration", "value": "1"},
        ],
        "reason": "discounted_ucb",
        "seed": 42,
        "step": 25,
    }
    execution = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]["execution"]
    declared = [{"role": "task_spec", **_ref(task_spec)}]
    input_manifest_raw = capsule.canonicalize(
        {
            "declared_artifacts": declared,
            "execution": execution,
            "routing": routing,
        }
    )
    input_manifest = _evidence(
        "input-manifest", input_manifest_raw, "application/json"
    )
    data = {
        "artifacts": [
            {"role": "input_manifest", **_ref(input_manifest)},
            *declared,
        ],
        "generation": 1,
        "mutator": {"origin": "agent", "profile": "hot_loop"},
        "node_id": "node-01",
        "phase": "dispatch",
        "routing": routing,
        "slot": 2,
    }
    body = _body(
        kind="task",
        schema=payloads.GNO_ENVELOPE_SCHEMA,
        data=data,
        evidence=[input_manifest, task_spec],
        parents=[_capsule_id("1")],
    )
    return body, {
        "input-manifest": input_manifest_raw,
        "task-spec": b"optimize foo without ambient reads",
    }


def _gno_dispatch() -> dict:
    return _gno_dispatch_with_blobs()[0]


def _optibench() -> dict:
    candidate = _capsule_id("2")
    blobs = {
        "counter-output": b"1,234 instructions",
        "environment": b'{"runner":"linux-x86_64"}',
        "harness": b'{"tokenizer":"pinned-v1"}',
        "population": b'{"closed":true}',
        "tests": b'{"passed":true}',
    }
    evidence = {name: _evidence(name, raw) for name, raw in blobs.items()}
    data = {
        "aggregation": "median",
        "baseline_relation": "dominates_baseline",
        "candidate_capsule": candidate,
        "cohort": {
            "counter_profile": "perf_stat_instructions_v1",
            "environment": _ref(evidence["environment"]),
            "harness": _ref(evidence["harness"]),
            "tool": "perf",
            "tool_version": "6.8.0",
        },
        "counter_output": _ref(evidence["counter-output"]),
        "gate_receipts": [_ref(evidence["tests"])],
        "nsga2": {
            "algorithm": "nsga2_exact_v1",
            "crowding": {"kind": "boundary"},
            "pareto_rank": 0,
        },
        "objective_metrics": [
            "cpu_instructions_retired",
            "diff_tokens",
            "peak_memory_bytes",
        ],
        "population_closed": True,
        "population_manifest": _ref(evidence["population"]),
        "repetitions": 5,
    }
    metrics = [
        {"name": "cpu_instructions_retired", "unit": "instruction", "value": "1234"},
        {"name": "diff_tokens", "unit": "token", "value": "18"},
        {"name": "peak_memory_bytes", "unit": "byte", "value": "29300"},
    ]
    return _body(
        kind="benchmark",
        schema=payloads.OPTIBENCH_RESULT_SCHEMA,
        data=data,
        evidence=list(evidence.values()),
        parents=[candidate],
        metrics=metrics,
    )


def _dpo() -> tuple[dict, dict[str, bytes]]:
    blobs = {
        "chosen-output": b"optimized implementation",
        "cohort-proof": b'{"benchmarks":[]}',
        "dominance": b'{"chosen":[10,20,3],"rejected":[12,25,4]}',
        "population": b'{"closed":true,"cohort":"abc"}',
        "prompt": b"Optimize the focus function.",
        "rejected-output": b"slower implementation",
    }
    evidence = {name: _evidence(name, raw) for name, raw in blobs.items()}
    task = _capsule_id("1")
    chosen_candidate = _capsule_id("2")
    chosen_benchmark = _capsule_id("3")
    rejected_candidate = _capsule_id("4")
    rejected_benchmark = _capsule_id("5")
    data = {
        "chosen": {
            "artifact": _ref(evidence["chosen-output"]),
            "benchmark_capsule": chosen_benchmark,
            "candidate_capsule": chosen_candidate,
        },
        "cohort_proof_manifest": _ref(evidence["cohort-proof"]),
        "export_profile": "preference_jsonl_v1",
        "population_manifest": _ref(evidence["population"]),
        "preference": {
            "basis": "pareto_dominance",
            "dominance_receipt": _ref(evidence["dominance"]),
            "policy": "nsga2_direct_dominance_v1",
        },
        "prompt": _ref(evidence["prompt"]),
        "rejected": {
            "artifact": _ref(evidence["rejected-output"]),
            "benchmark_capsule": rejected_benchmark,
            "candidate_capsule": rejected_candidate,
        },
        "task_capsule": task,
    }
    body = _body(
        kind="evaluation",
        schema=payloads.AGENTIC_DPO_PAIR_SCHEMA,
        data=data,
        evidence=list(evidence.values()),
        parents=[
            task,
            chosen_candidate,
            chosen_benchmark,
            rejected_candidate,
            rejected_benchmark,
        ],
    )
    return body, blobs


def _trusted_preference_graph(
    *,
    chosen_test_raw: bytes | None = None,
    task_parents: list[str] | None = None,
):
    task_raw = b"Optimize the focus function."
    task_evidence = _evidence("prompt", task_raw)
    routing = {
        "algorithm": "deterministic_v1",
        "arm": "preference-fixture",
        "arm_pull": 1,
        "parameters": [],
        "reason": "test-fixture",
        "seed": 7,
        "step": 1,
    }
    execution = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]["execution"]
    declared = [{"role": "task_spec", **_ref(task_evidence)}]
    task_manifest_raw = capsule.canonicalize(
        {
            "declared_artifacts": declared,
            "execution": execution,
            "routing": routing,
        }
    )
    task_manifest = _evidence(
        "input-manifest", task_manifest_raw, "application/json"
    )
    task = _body(
        kind="task",
        schema=payloads.GNO_ENVELOPE_SCHEMA,
        data={
            "artifacts": [
                {"role": "input_manifest", **_ref(task_manifest)},
                *declared,
            ],
            "generation": 1,
            "mutator": {"origin": "agent", "profile": "preference-fixture"},
            "node_id": "preference-node",
            "phase": "dispatch",
            "routing": routing,
            "slot": 1,
        },
        evidence=[task_manifest, task_evidence],
        parents=task_parents or [],
    )
    task_id = capsule.capsule_id(task)
    task_blobs = {
        "input-manifest": task_manifest_raw,
        "prompt": task_raw,
    }

    chosen_raw = b"optimized implementation"
    rejected_raw = b"slower implementation"

    def candidate(
        name: str, raw: bytes, test_raw: bytes | None = None
    ) -> tuple[dict, dict[str, bytes]]:
        descriptor = _evidence(name, raw)
        artifacts = [{"role": "output_diff", **_ref(descriptor)}]
        evidence_items = [descriptor]
        blobs = {name: raw}
        if test_raw is not None:
            test_descriptor = _evidence("chosen-tests", test_raw, "application/json")
            artifacts.append({"role": "test_receipt", **_ref(test_descriptor)})
            evidence_items.append(test_descriptor)
            blobs["chosen-tests"] = test_raw
        result = _body(
            kind="candidate",
            schema=payloads.GNO_ENVELOPE_SCHEMA,
            data={
                "artifacts": sorted(
                    artifacts,
                    key=lambda item: (
                        item["role"],
                        item["evidence_name"],
                        item["sha256"],
                    ),
                ),
                "dispatch_capsule": task_id,
                "generation": 1,
                "node_id": "preference-node",
                "outcome": "candidate",
                "phase": "result",
                "slot": 1,
            },
            evidence=evidence_items,
            parents=[task_id],
        )
        return result, blobs

    chosen_candidate, chosen_candidate_blobs = candidate(
        "chosen-output", chosen_raw, chosen_test_raw
    )
    rejected_candidate, rejected_candidate_blobs = candidate(
        "rejected-output", rejected_raw
    )
    chosen_candidate_id = capsule.capsule_id(chosen_candidate)
    rejected_candidate_id = capsule.capsule_id(rejected_candidate)
    population = sorted([chosen_candidate_id, rejected_candidate_id])

    environment_raw = capsule.canonicalize(
        {
            "architecture": "x86_64",
            "cpu_model": "zen4",
            "kernel": "linux-6.8.0",
            "operating_system": "ubuntu-24.04",
            "runner": "linux-x86_64",
        }
    )
    harness_raw = capsule.canonicalize(
        {
            "diff_metric": "token_count",
            "peak_memory_method": "max_rss",
            "source_commit": "aa1daa2de2a2bc1d1dab6c2e053022287aa63157",
            "tokenizer": "pinned-v1",
            "version": "1.0.0",
        }
    )
    tests_raw = capsule.canonicalize({"gate": "tests", "passed": True})
    environment = _evidence("environment", environment_raw, "application/json")
    harness = _evidence("harness", harness_raw, "application/json")
    tests = _evidence("tests", tests_raw, "application/json")
    cohort = {
        "counter_profile": "perf_stat_instructions_v1",
        "environment": _ref(environment),
        "harness": _ref(harness),
        "tool": "perf",
        "tool_version": "6.8.0",
    }
    population_raw = capsule.canonicalize(
        {
            "candidate_capsules": population,
            "closed": True,
            "cohort_sha256": hashlib.sha256(capsule.canonicalize(cohort)).hexdigest(),
        }
    )
    population_evidence = _evidence("population", population_raw, "application/json")

    objective_names = [
        "cpu_instructions_retired",
        "diff_tokens",
        "peak_memory_bytes",
    ]

    def benchmark(
        candidate_capsule: str,
        point: tuple[int, int, int],
        rank: int,
        baseline_relation: str,
    ) -> tuple[dict, dict[str, bytes]]:
        samples = [
            {name: str(value) for name, value in zip(objective_names, point)}
            for _ in range(5)
        ]
        counter_raw = capsule.canonicalize(
            {
                "baseline": {
                    name: str(value)
                    for name, value in zip(objective_names, (1100, 11, 105))
                },
                "counter_profile": "perf_stat_instructions_v1",
                "samples": samples,
            }
        )
        counter = _evidence("counter-output", counter_raw, "application/json")
        data = {
            "aggregation": "median",
            "baseline_relation": baseline_relation,
            "candidate_capsule": candidate_capsule,
            "cohort": cohort,
            "counter_output": _ref(counter),
            "gate_receipts": [_ref(tests)],
            "nsga2": {
                "algorithm": "nsga2_exact_v1",
                "crowding": {"kind": "boundary"},
                "pareto_rank": rank,
            },
            "objective_metrics": objective_names,
            "population_closed": True,
            "population_manifest": _ref(population_evidence),
            "repetitions": 5,
        }
        metrics = [
            {"name": name, "unit": unit, "value": str(value)}
            for (name, unit), value in zip(payloads.PERF_OBJECTIVES, point)
        ]
        result = _body(
            kind="benchmark",
            schema=payloads.OPTIBENCH_RESULT_SCHEMA,
            data=data,
            evidence=[counter, environment, harness, population_evidence, tests],
            parents=population,
            metrics=metrics,
        )
        return result, {
            "counter-output": counter_raw,
            "environment": environment_raw,
            "harness": harness_raw,
            "population": population_raw,
            "tests": tests_raw,
        }

    chosen_benchmark, chosen_benchmark_blobs = benchmark(
        chosen_candidate_id, (1000, 10, 100), 0, "dominates_baseline"
    )
    rejected_benchmark, rejected_benchmark_blobs = benchmark(
        rejected_candidate_id, (1200, 12, 110), 1, "dominated_by_baseline"
    )
    chosen_benchmark_id = capsule.capsule_id(chosen_benchmark)
    rejected_benchmark_id = capsule.capsule_id(rejected_benchmark)
    benchmark_ids = {
        chosen_candidate_id: chosen_benchmark_id,
        rejected_candidate_id: rejected_benchmark_id,
    }
    cohort_proof_raw = capsule.canonicalize(
        {
            "benchmarks": [
                {
                    "benchmark_capsule": benchmark_ids[candidate_id],
                    "candidate_capsule": candidate_id,
                }
                for candidate_id in population
            ],
            "cohort_sha256": hashlib.sha256(capsule.canonicalize(cohort)).hexdigest(),
            "population_manifest_sha256": population_evidence["sha256"],
        }
    )

    dominance_raw = capsule.canonicalize(
        {
            "chosen_benchmark": chosen_benchmark_id,
            "metrics": [
                {
                    "chosen": str(chosen),
                    "name": name,
                    "rejected": str(rejected),
                    "unit": unit,
                }
                for (name, unit), chosen, rejected in zip(
                    payloads.PERF_OBJECTIVES,
                    (1000, 10, 100),
                    (1200, 12, 110),
                )
            ],
            "rejected_benchmark": rejected_benchmark_id,
            "relation": "dominates",
        }
    )
    prompt_raw = b"Optimize the focus function."
    dpo_blobs = {
        "chosen-output": chosen_raw,
        "cohort-proof": cohort_proof_raw,
        "dominance": dominance_raw,
        "population": population_raw,
        "prompt": prompt_raw,
        "rejected-output": rejected_raw,
    }
    dpo_evidence = {
        name: _evidence(
            name,
            raw,
            "application/json"
            if name in {"cohort-proof", "dominance", "population"}
            else "text/plain",
        )
        for name, raw in dpo_blobs.items()
    }
    dpo_data = {
        "chosen": {
            "artifact": _ref(dpo_evidence["chosen-output"]),
            "benchmark_capsule": chosen_benchmark_id,
            "candidate_capsule": chosen_candidate_id,
        },
        "cohort_proof_manifest": _ref(dpo_evidence["cohort-proof"]),
        "export_profile": "preference_jsonl_v1",
        "population_manifest": _ref(dpo_evidence["population"]),
        "preference": {
            "basis": "pareto_dominance",
            "dominance_receipt": _ref(dpo_evidence["dominance"]),
            "policy": "nsga2_direct_dominance_v1",
        },
        "prompt": _ref(dpo_evidence["prompt"]),
        "rejected": {
            "artifact": _ref(dpo_evidence["rejected-output"]),
            "benchmark_capsule": rejected_benchmark_id,
            "candidate_capsule": rejected_candidate_id,
        },
        "task_capsule": task_id,
    }
    dpo = _body(
        kind="evaluation",
        schema=payloads.AGENTIC_DPO_PAIR_SCHEMA,
        data=dpo_data,
        evidence=list(dpo_evidence.values()),
        parents=[
            task_id,
            chosen_candidate_id,
            rejected_candidate_id,
            chosen_benchmark_id,
            rejected_benchmark_id,
        ],
    )
    graph = {
        task_id: task,
        chosen_candidate_id: chosen_candidate,
        rejected_candidate_id: rejected_candidate,
        chosen_benchmark_id: chosen_benchmark,
        rejected_benchmark_id: rejected_benchmark,
    }
    graph_blobs = {
        task_id: task_blobs,
        chosen_candidate_id: chosen_candidate_blobs,
        rejected_candidate_id: rejected_candidate_blobs,
        chosen_benchmark_id: chosen_benchmark_blobs,
        rejected_benchmark_id: rejected_benchmark_blobs,
    }
    return dpo, dpo_blobs, graph, graph_blobs


def test_capsule_v1_schema_remains_pinned_while_profiles_evolve_separately():
    capsule_schema = ROOT / "docs" / "okf" / "intelligence-capsule-v1.schema.json"
    assert hashlib.sha256(capsule_schema.read_bytes()).hexdigest() == (
        "10c2ec4638bd6c4e303b3e2c4c7d91ae582554f48aaa01fac2d9370062b98d4c"
    )
    for schema, file_name in payloads.PROFILE_SCHEMA_FILES.items():
        path = ROOT / "docs" / "okf" / file_name
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["$id"] == f"https://grokmcp.org/docs/okf/{file_name}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            payloads.payload_profile_schema_sha256(schema)
        )
    for schema, file_name in payloads.PROJECTION_SCHEMA_FILES.items():
        path = ROOT / "docs" / "okf" / file_name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            payloads.PROJECTION_SCHEMA_SHA256[schema]
        )
    semantic_spec = ROOT / "docs" / "okf" / payloads.SEMANTIC_SPEC_FILE
    assert hashlib.sha256(semantic_spec.read_bytes()).hexdigest() == (
        payloads.SEMANTIC_SPEC_SHA256
    )
    assert hashlib.sha256(CONFORMANCE.read_bytes()).hexdigest() == (
        payloads.CONFORMANCE_SHA256
    )
    semantic_document = json.loads(semantic_spec.read_text(encoding="utf-8"))
    assert semantic_document["conformance"] == {
        "file": payloads.CONFORMANCE_FILE,
        "sha256": payloads.CONFORMANCE_SHA256,
    }
    assert [
        {
            "expression": pattern.pattern,
            "ignore_case": bool(pattern.flags & re.IGNORECASE),
        }
        for pattern in payloads._SHARED_SECRET_PATTERNS
    ] == semantic_document["secret_rejection"]["patterns"]


def test_unknown_profile_stays_structural_but_is_not_registered():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]
    capsule.validate_body(body)
    assert payloads.validate_known_payload_profile(body) is False

    invalid_known = _gno_dispatch()
    invalid_known["payload"]["data"]["artifacts"] = [
        item
        for item in invalid_known["payload"]["data"]["artifacts"]
        if item["role"] != "task_spec"
    ]
    capsule.validate_body(invalid_known)
    with pytest.raises(capsule.CapsuleValidationError, match="task_spec"):
        payloads.validate_known_payload_profile(invalid_known)


def test_python_matches_shared_profile_identity_vectors():
    vectors = json.loads(CONFORMANCE.read_text(encoding="utf-8"))
    bodies = (_gno_dispatch(), _optibench(), _dpo()[0])
    for body in bodies:
        schema = body["payload"]["schema"]
        assert capsule.digest_body(body) == vectors["accepted_body_sha256"][schema]


def test_gno_dispatch_and_result_are_input_closed_and_parent_bound():
    dispatch, dispatch_blobs = _gno_dispatch_with_blobs()
    _validate_known_body(dispatch)
    payloads.validate_gno_dispatch_evidence(dispatch, dispatch_blobs)

    output_raw = b"diff --git a/a.py b/a.py\n"
    output = _evidence("candidate-patch", output_raw)
    dispatch_id = capsule.capsule_id(dispatch)
    result = _body(
        kind="candidate",
        schema=payloads.GNO_ENVELOPE_SCHEMA,
        data={
            "artifacts": [{"role": "output_diff", **_ref(output)}],
            "dispatch_capsule": dispatch_id,
            "generation": 1,
            "node_id": "node-01",
            "outcome": "candidate",
            "phase": "result",
            "slot": 2,
        },
        evidence=[output],
        parents=[dispatch_id],
    )
    _validate_known_body(result)
    payloads.validate_gno_result_graph(
        result,
        dispatch,
        result_evidence_blobs={"candidate-patch": output_raw},
        dispatch_evidence_blobs=dispatch_blobs,
    )

    invalid = copy.deepcopy(result)
    invalid["parents"] = [_capsule_id("f")]
    with pytest.raises(capsule.CapsuleValidationError, match="sole parent"):
        _validate_known_body(invalid)

    opposite = copy.deepcopy(result)
    failure_raw = b'{"error":"failed"}'
    failure = _evidence("failure", failure_raw, "application/json")
    opposite["evidence"].append(failure)
    opposite["evidence"].sort(key=lambda item: (item["name"], item["sha256"]))
    opposite["payload"]["data"]["artifacts"].append(
        {"role": "failure_receipt", **_ref(failure)}
    )
    opposite["payload"]["data"]["artifacts"].sort(
        key=lambda item: (item["role"], item["evidence_name"], item["sha256"])
    )
    with pytest.raises(capsule.CapsuleValidationError, match="forbids failure_receipt"):
        _validate_known_body(opposite)

    contradictory_execution = copy.deepcopy(result)
    contradictory_execution["execution"] = {
        "model": "grok-build-0.1",
        "plane": "api",
        "runtime": "cloud",
        "target": "cloud_api",
    }
    with pytest.raises(capsule.CapsuleValidationError, match="inherits execution"):
        _validate_known_body(contradictory_execution)


def test_gno_rejects_dishonest_or_noncanonical_receipts():
    body = _gno_dispatch()
    body["payload"]["data"]["routing"]["parameters"][0]["value"] = 0.95
    with pytest.raises(capsule.CapsuleValidationError, match="decimal string"):
        _validate_known_body(body)

    body = _gno_dispatch()
    body["payload"]["data"]["routing"]["parameters"].reverse()
    with pytest.raises(capsule.CapsuleValidationError, match="canonically sorted"):
        _validate_known_body(body)

    body = _gno_dispatch()
    body["payload"]["data"]["artifacts"][0]["sha256"] = "f" * 64
    with pytest.raises(capsule.CapsuleValidationError, match="does not match"):
        _validate_known_body(body)

    body = _gno_dispatch()
    duplicate = copy.deepcopy(body["evidence"][0])
    duplicate["name"] = "input-manifest-2"
    body["evidence"].append(duplicate)
    body["evidence"].sort(key=lambda item: (item["name"], item["sha256"]))
    body["payload"]["data"]["artifacts"].append(
        {"role": "input_manifest", **_ref(duplicate)}
    )
    body["payload"]["data"]["artifacts"].sort(
        key=lambda item: (item["role"], item["evidence_name"], item["sha256"])
    )
    with pytest.raises(capsule.CapsuleValidationError, match="exactly one input_manifest"):
        _validate_known_body(body)


def test_known_profiles_forbid_ambiguous_duplicate_evidence_names():
    body = _gno_dispatch()
    duplicate = copy.deepcopy(body["evidence"][0])
    duplicate["sha256"] = "f" * 64
    body["evidence"].append(duplicate)
    body["evidence"].sort(key=lambda item: (item["name"], item["sha256"]))
    with pytest.raises(capsule.CapsuleValidationError, match="unique evidence names"):
        _validate_known_body(body)

    vector = next(
        item
        for item in json.loads(CONFORMANCE.read_text(encoding="utf-8"))["reject_vectors"]
        if item["mutation"] == "secret_identifier"
    )
    secret_value = "".join(vector["value_parts"])
    secret_name = _gno_dispatch()
    secret_name["evidence"].append(
        _evidence(secret_value, b"unused non-secret evidence")
    )
    secret_name["evidence"].sort(key=lambda item: (item["name"], item["sha256"]))
    with pytest.raises(capsule.CapsuleValidationError, match=vector["error"]):
        _validate_known_body(secret_name)

    secret_actor = _gno_dispatch()
    secret_actor["actor"]["agent_id"] = secret_value
    capsule.validate_body(secret_actor)
    with pytest.raises(capsule.CapsuleValidationError, match=vector["error"]):
        payloads.validate_known_payload_profile(secret_actor)

    enum_vector = next(
        item
        for item in json.loads(CONFORMANCE.read_text(encoding="utf-8"))["reject_vectors"]
        if item["mutation"] == "enum_type_confusion"
    )
    confused = _gno_dispatch()
    confused["payload"]["data"]["mutator"]["origin"] = enum_vector["value"]
    capsule.validate_body(confused)
    with pytest.raises(capsule.CapsuleValidationError, match=enum_vector["error"]):
        payloads.validate_known_payload_profile(confused)


@pytest.mark.parametrize(
    "text",
    [
        "XAI_API_KEY: notproviderformattedsecret123",
        '"XAI_API_KEY":"notproviderformattedsecret123"',
        "password: correcthorsebatterystaple",
        "client_secret: verysecretvalue123",
        '"password":"correct horse battery staple"',
        'password: "p@ssw0rd!"',
        'CLIENT_SECRET: "!very-secret-value!"',
        '"clientSecret":"verysecretvalue123"',
        '"apiKey":"verysecretvalue123"',
        "accessToken: verysecretvalue123",
        "privateKey: verysecretvalue123",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    ],
)
def test_pinned_secret_detector_rejects_mapping_and_auth_forms(text: str):
    assert payloads._contains_secret_like(text)
    assert not payloads._contains_secret_like('"XAI_API_KEY":"${XAI_API_KEY}"')


def test_optibench_requires_closed_evidenced_hardware_cohort():
    body = _optibench()
    _validate_known_body(body)

    mixed = copy.deepcopy(body)
    mixed["payload"]["data"]["cohort"]["counter_profile"] = "callgrind_ir_v1"
    with pytest.raises(capsule.CapsuleValidationError, match="profile and tool disagree"):
        _validate_known_body(mixed)

    provisional = copy.deepcopy(body)
    provisional["payload"]["data"]["population_closed"] = False
    with pytest.raises(capsule.CapsuleValidationError, match="population must be closed"):
        _validate_known_body(provisional)

    fabricated = copy.deepcopy(body)
    fabricated["payload"]["data"]["counter_output"]["sha256"] = "f" * 64
    with pytest.raises(capsule.CapsuleValidationError, match="does not match"):
        _validate_known_body(fabricated)


def test_optibench_uses_exact_integer_metrics_and_rational_crowding():
    body = _optibench()
    body["payload"]["data"]["nsga2"]["crowding"] = {
        "denominator": "6",
        "kind": "finite",
        "numerator": "17",
    }
    _validate_known_body(body)

    non_reduced = copy.deepcopy(body)
    non_reduced["payload"]["data"]["nsga2"]["crowding"] = {
        "denominator": "6",
        "kind": "finite",
        "numerator": "18",
    }
    with pytest.raises(capsule.CapsuleValidationError, match="must be reduced"):
        _validate_known_body(non_reduced)

    float_value = copy.deepcopy(body)
    float_value["metrics"][0]["value"] = "1234.5"
    with pytest.raises(capsule.CapsuleValidationError, match="nonnegative integer"):
        _validate_known_body(float_value)


def test_optibench_evidence_recomputes_population_gates_and_medians():
    _dpo_body, _dpo_blobs, graph, graph_blobs = _trusted_preference_graph()
    benchmarks = [
        (identifier, body)
        for identifier, body in graph.items()
        if body["kind"] == "benchmark"
    ]
    for identifier, body in benchmarks:
        payloads.validate_optibench_evidence(body, graph_blobs[identifier])

    identifier, tampered = benchmarks[0]
    tampered = copy.deepcopy(tampered)
    tampered["metrics"][0]["value"] = "999"
    with pytest.raises(capsule.CapsuleValidationError, match="median counter samples"):
        payloads.validate_optibench_evidence(tampered, graph_blobs[identifier])

    secret_environment = copy.deepcopy(benchmarks[0][1])
    secret_blobs = dict(graph_blobs[benchmarks[0][0]])
    environment = capsule.parse_canonical(secret_blobs["environment"])
    environment["runner"] = "github_pat_0123456789abcdefghij"
    environment_raw = capsule.canonicalize(environment)
    environment_descriptor = _evidence(
        "environment", environment_raw, "application/json"
    )
    for descriptor in secret_environment["evidence"]:
        if descriptor["name"] == "environment":
            descriptor.update(environment_descriptor)
    secret_environment["payload"]["data"]["cohort"]["environment"] = _ref(
        environment_descriptor
    )
    secret_blobs["environment"] = environment_raw
    with pytest.raises(capsule.CapsuleValidationError, match="secret-like"):
        payloads.validate_optibench_evidence(secret_environment, secret_blobs)

    secret_gate = copy.deepcopy(benchmarks[0][1])
    gate_blobs = dict(graph_blobs[benchmarks[0][0]])
    gate_raw = capsule.canonicalize(
        {"gate": "ghp_abcdefghijklmnopqrstuvwxyz123456", "passed": True}
    )
    gate_descriptor = _evidence("tests", gate_raw, "application/json")
    for descriptor in secret_gate["evidence"]:
        if descriptor["name"] == "tests":
            descriptor.update(gate_descriptor)
    secret_gate["payload"]["data"]["gate_receipts"] = [_ref(gate_descriptor)]
    gate_blobs["tests"] = gate_raw
    with pytest.raises(capsule.CapsuleValidationError, match="secret-like"):
        payloads.validate_optibench_evidence(secret_gate, gate_blobs)


def test_optibench_population_recomputes_rank_and_exact_crowding():
    _dpo_body, _dpo_blobs, graph, graph_blobs = _trusted_preference_graph()
    benchmarks = {
        identifier: body
        for identifier, body in graph.items()
        if body["kind"] == "benchmark"
    }
    summaries = payloads.validate_optibench_population(benchmarks, graph_blobs)
    assert {summary["pareto_rank"] for summary in summaries.values()} == {0, 1}
    assert all(
        summary["crowding"] == {"kind": "boundary"}
        for summary in summaries.values()
    )

    original_id, original = next(iter(benchmarks.items()))
    fake_rank = copy.deepcopy(original)
    fake_rank["payload"]["data"]["nsga2"]["pareto_rank"] = 42
    fake_rank_id = capsule.capsule_id(fake_rank)
    fake_rank_population = {
        **{key: value for key, value in benchmarks.items() if key != original_id},
        fake_rank_id: fake_rank,
    }
    fake_rank_blobs = {
        **{key: value for key, value in graph_blobs.items() if key != original_id},
        fake_rank_id: graph_blobs[original_id],
    }
    with pytest.raises(capsule.CapsuleValidationError, match="Pareto rank"):
        payloads.validate_optibench_population(
            fake_rank_population, fake_rank_blobs
        )

    fake_crowding = copy.deepcopy(original)
    fake_crowding["payload"]["data"]["nsga2"]["crowding"] = {
        "denominator": "1",
        "kind": "finite",
        "numerator": "0",
    }
    fake_crowding_id = capsule.capsule_id(fake_crowding)
    fake_crowding_population = {
        **{key: value for key, value in benchmarks.items() if key != original_id},
        fake_crowding_id: fake_crowding,
    }
    fake_crowding_blobs = {
        **{key: value for key, value in graph_blobs.items() if key != original_id},
        fake_crowding_id: graph_blobs[original_id],
    }
    with pytest.raises(capsule.CapsuleValidationError, match="crowding"):
        payloads.validate_optibench_population(
            fake_crowding_population, fake_crowding_blobs
        )

    malformed = copy.deepcopy(original)
    malformed["payload"]["data"].pop("cohort")
    malformed_id = capsule.capsule_id(malformed)
    with pytest.raises(capsule.CapsuleValidationError, match="fields do not match"):
        payloads.validate_optibench_population(
            {malformed_id: malformed},
            {malformed_id: graph_blobs[original_id]},
        )


def test_exact_nsga2_crowding_is_tie_symmetric_and_order_independent():
    vector = json.loads(CONFORMANCE.read_text(encoding="utf-8"))["semantic_vectors"][
        "nsga2_exact_v1"
    ]
    points = {
        item["candidate_capsule"]: tuple(int(value) for value in item["values"])
        for item in vector["points"]
    }
    ranks = payloads._nondominated_ranks(points)
    expected_ranks = {
        item["candidate_capsule"]: item["pareto_rank"]
        for item in vector["expected"]
    }
    expected_crowding = {
        item["candidate_capsule"]: item["crowding"]
        for item in vector["expected"]
    }
    assert ranks == expected_ranks
    assert payloads._exact_crowding(points, ranks) == expected_crowding
    assert (
        payloads._exact_crowding(dict(reversed(list(points.items()))), ranks)
        == expected_crowding
    )

    identical = {"a": (1, 1), "b": (1, 1), "c": (1, 1)}
    identical_ranks = payloads._nondominated_ranks(identical)
    assert payloads._exact_crowding(identical, identical_ranks) == {
        candidate: {"denominator": "1", "kind": "finite", "numerator": "0"}
        for candidate in identical
    }


def test_public_dpo_cohort_proof_vector_has_one_canonical_identity():
    vector = json.loads(CONFORMANCE.read_text(encoding="utf-8"))["semantic_vectors"][
        "dpo_cohort_proof_v1"
    ]
    assert hashlib.sha256(capsule.canonicalize(vector["manifest"])).hexdigest() == (
        vector["expected_sha256"]
    )


def test_crowding_digit_bound_is_fail_closed_and_cross_runtime_safe():
    body = _optibench()
    body["payload"]["data"]["nsga2"]["crowding"] = {
        "denominator": "1",
        "kind": "finite",
        "numerator": "9" * (payloads.MAX_RATIONAL_DIGITS + 1),
    }
    with pytest.raises(capsule.CapsuleValidationError, match="numerator"):
        _validate_known_body(body)


def test_dpo_pair_is_graph_proven_before_verified_jsonl_export():
    body, blobs, graph, graph_blobs = _trusted_preference_graph()
    _validate_known_body(body)
    payloads.validate_dpo_preference_graph(body, graph, blobs, graph_blobs)
    example = payloads.build_preference_example(
        body,
        blobs,
        graph_bodies=graph,
        graph_evidence_blobs=graph_blobs,
    )
    rendered = payloads.render_preference_jsonl(
        body,
        blobs,
        graph_bodies=graph,
        graph_evidence_blobs=graph_blobs,
    )
    assert rendered.endswith(b"\n") and not rendered.endswith(b"\n\n")
    record = json.loads(rendered)
    assert record == example == {
        "chosen": "optimized implementation",
        "prompt": "Optimize the focus function.",
        "rejected": "slower implementation",
        "source_capsule": capsule.capsule_id(body),
    }
    assert rendered == capsule.canonicalize(record) + b"\n"


def test_dpo_pair_fails_closed_on_bad_provenance_or_blob_bytes():
    body, blobs, graph, graph_blobs = _trusted_preference_graph()
    arbitrary = copy.deepcopy(body)
    arbitrary["parents"] = arbitrary["parents"][:-1]
    with pytest.raises(capsule.CapsuleValidationError, match="parents must be"):
        _validate_known_body(arbitrary)

    same_artifact = copy.deepcopy(body)
    same_artifact["payload"]["data"]["rejected"]["artifact"] = copy.deepcopy(
        same_artifact["payload"]["data"]["chosen"]["artifact"]
    )
    with pytest.raises(capsule.CapsuleValidationError, match="artifacts must differ"):
        _validate_known_body(same_artifact)

    substituted = dict(blobs)
    substituted["chosen-output"] = b"tampered"
    with pytest.raises(capsule.CapsuleValidationError, match="byte count|digest"):
        payloads.render_preference_jsonl(
            body,
            substituted,
            graph_bodies=graph,
            graph_evidence_blobs=graph_blobs,
        )

    missing_proof = dict(blobs)
    missing_proof.pop("dominance")
    with pytest.raises(capsule.CapsuleValidationError, match="missing evidence bytes"):
        payloads.render_preference_jsonl(
            body,
            missing_proof,
            graph_bodies=graph,
            graph_evidence_blobs=graph_blobs,
        )

    incomplete_cohort = copy.deepcopy(body)
    cohort_proof = capsule.parse_canonical(blobs["cohort-proof"])
    cohort_proof["benchmarks"].pop()
    cohort_proof_raw = capsule.canonicalize(cohort_proof)
    cohort_proof_descriptor = _evidence(
        "cohort-proof", cohort_proof_raw, "application/json"
    )
    for descriptor in incomplete_cohort["evidence"]:
        if descriptor["name"] == "cohort-proof":
            descriptor.update(cohort_proof_descriptor)
    incomplete_cohort["payload"]["data"]["cohort_proof_manifest"] = _ref(
        cohort_proof_descriptor
    )
    with pytest.raises(capsule.CapsuleValidationError, match="complete benchmark"):
        payloads.validate_dpo_preference_graph(
            incomplete_cohort,
            graph,
            {**blobs, "cohort-proof": cohort_proof_raw},
            graph_blobs,
        )


def test_dpo_text_must_bind_exact_gno_semantic_roles():
    body, blobs, graph, graph_blobs = _trusted_preference_graph()
    task_id = body["payload"]["data"]["task_capsule"]
    manifest_raw = graph_blobs[task_id]["input-manifest"]
    manifest = _evidence("input-manifest", manifest_raw, "application/json")
    wrong_prompt = copy.deepcopy(body)
    wrong_prompt["evidence"].append(manifest)
    wrong_prompt["evidence"].sort(
        key=lambda item: (item["name"], item["sha256"])
    )
    wrong_prompt["payload"]["data"]["prompt"] = _ref(manifest)
    with pytest.raises(capsule.CapsuleValidationError, match="GNO task_spec"):
        payloads.validate_dpo_preference_graph(
            wrong_prompt,
            graph,
            {**blobs, "input-manifest": manifest_raw},
            graph_blobs,
        )

    test_raw = b'{"passed":true}'
    body, blobs, graph, graph_blobs = _trusted_preference_graph(
        chosen_test_raw=test_raw
    )
    test_descriptor = _evidence("chosen-tests", test_raw, "application/json")
    wrong_chosen = copy.deepcopy(body)
    wrong_chosen["evidence"].append(test_descriptor)
    wrong_chosen["evidence"].sort(
        key=lambda item: (item["name"], item["sha256"])
    )
    wrong_chosen["payload"]["data"]["chosen"]["artifact"] = _ref(
        test_descriptor
    )
    with pytest.raises(capsule.CapsuleValidationError, match="GNO output_diff"):
        payloads.validate_dpo_preference_graph(
            wrong_chosen,
            graph,
            {**blobs, "chosen-tests": test_raw},
            graph_blobs,
        )


def test_dpo_graph_rejects_unrelated_task_and_mixed_baselines():
    body, blobs, graph, graph_blobs = _trusted_preference_graph()
    data = body["payload"]["data"]

    original_task_id = data["task_capsule"]
    unrelated_task = copy.deepcopy(graph[original_task_id])
    unrelated_task["payload"]["data"]["mutator"]["profile"] = "unrelated-task"
    unrelated_task_id = capsule.capsule_id(unrelated_task)
    unrelated_body = copy.deepcopy(body)
    unrelated_body["payload"]["data"]["task_capsule"] = unrelated_task_id
    unrelated_body["parents"] = sorted(
        unrelated_task_id if item == original_task_id else item
        for item in unrelated_body["parents"]
    )
    unrelated_graph = {**graph, unrelated_task_id: unrelated_task}
    with pytest.raises(capsule.CapsuleValidationError, match="linked to the declared task"):
        payloads.validate_dpo_preference_graph(
            unrelated_body, unrelated_graph, blobs, graph_blobs
        )

    rejected_benchmark_id = data["rejected"]["benchmark_capsule"]
    rejected_benchmark = copy.deepcopy(graph[rejected_benchmark_id])
    rejected_blobs = dict(graph_blobs[rejected_benchmark_id])
    counter = capsule.parse_canonical(rejected_blobs["counter-output"])
    counter["baseline"] = {
        "cpu_instructions_retired": "1300",
        "diff_tokens": "13",
        "peak_memory_bytes": "115",
    }
    counter_raw = capsule.canonicalize(counter)
    counter_descriptor = _evidence(
        "counter-output", counter_raw, "application/json"
    )
    for descriptor in rejected_benchmark["evidence"]:
        if descriptor["name"] == "counter-output":
            descriptor.update(counter_descriptor)
    rejected_benchmark["payload"]["data"]["counter_output"] = _ref(
        counter_descriptor
    )
    rejected_benchmark["payload"]["data"][
        "baseline_relation"
    ] = "dominates_baseline"
    new_benchmark_id = capsule.capsule_id(rejected_benchmark)
    mixed_graph = dict(graph)
    mixed_graph.pop(rejected_benchmark_id)
    mixed_graph[new_benchmark_id] = rejected_benchmark
    mixed_graph_blobs = dict(graph_blobs)
    mixed_graph_blobs.pop(rejected_benchmark_id)
    rejected_blobs["counter-output"] = counter_raw
    mixed_graph_blobs[new_benchmark_id] = rejected_blobs

    mixed_body = copy.deepcopy(body)
    mixed_body["payload"]["data"]["rejected"][
        "benchmark_capsule"
    ] = new_benchmark_id
    mixed_body["parents"] = sorted(
        new_benchmark_id if item == rejected_benchmark_id else item
        for item in mixed_body["parents"]
    )
    dominance = capsule.parse_canonical(blobs["dominance"])
    dominance["rejected_benchmark"] = new_benchmark_id
    dominance_raw = capsule.canonicalize(dominance)
    dominance_descriptor = _evidence(
        "dominance", dominance_raw, "application/json"
    )
    for descriptor in mixed_body["evidence"]:
        if descriptor["name"] == "dominance":
            descriptor.update(dominance_descriptor)
    mixed_body["payload"]["data"]["preference"]["dominance_receipt"] = _ref(
        dominance_descriptor
    )
    mixed_blobs = {**blobs, "dominance": dominance_raw}
    with pytest.raises(capsule.CapsuleValidationError, match="different baselines"):
        payloads.validate_dpo_preference_graph(
            mixed_body, mixed_graph, mixed_blobs, mixed_graph_blobs
        )


def test_dpo_graph_must_close_every_transitive_parent():
    body, blobs, graph, graph_blobs = _trusted_preference_graph(
        task_parents=[_capsule_id("f")]
    )
    with pytest.raises(capsule.CapsuleValidationError, match="not closed"):
        payloads.validate_dpo_preference_graph(body, graph, blobs, graph_blobs)


def test_needle_projection_is_a_bounded_valid_tools_array_not_jsonl():
    body, blobs, graph, graph_blobs = _trusted_preference_graph()
    example = payloads.build_preference_example(
        body,
        blobs,
        graph_bodies=graph,
        graph_evidence_blobs=graph_blobs,
    )
    projection = payloads.build_needle_tools_context(
        "Find relevant optimization context",
        [example],
        tokenizer="needle-v0.1.0-pinned",
        token_counter=lambda text: len(text.encode("utf-8")),
        max_encoder_tokens=1024,
    )
    assert projection["profile"] == payloads.NEEDLE_CONTEXT_PROFILE
    assert projection["used_source_capsules"] == [example["source_capsule"]]
    assert projection["encoder_tokens"] <= projection["max_encoder_tokens"]
    tool = projection["tools"][0]
    assert tool["name"] == "select_unigrok_context"
    assert tool["parameters"]["capsule_id"]["required"] is True
    assert tool["examples"] == [example]

    too_small = payloads.build_needle_tools_context(
        "q",
        [example],
        tokenizer="needle-v0.1.0-pinned",
        token_counter=lambda text: (
            1 if text == "q" else 10 if '"examples":[]' in text else 2000
        ),
    )
    assert too_small["used_source_capsules"] == []

    with pytest.raises(capsule.CapsuleValidationError, match="non-empty"):
        payloads.build_needle_tools_context(
            "\u00a0\u3000",
            [],
            tokenizer="needle-v0.1.0-pinned",
            token_counter=lambda _text: 0,
        )
    with pytest.raises(capsule.CapsuleValidationError, match="strict UTF-8"):
        payloads.build_needle_tools_context(
            "\ud800",
            [],
            tokenizer="needle-v0.1.0-pinned",
            token_counter=lambda _text: 0,
        )
    with pytest.raises(capsule.CapsuleValidationError, match="strict UTF-8"):
        payloads.build_needle_tools_context(
            "q",
            [{**example, "chosen": "\ud800"}],
            tokenizer="needle-v0.1.0-pinned",
            token_counter=lambda _text: 0,
        )
    with pytest.raises(capsule.CapsuleValidationError, match="input count"):
        payloads.build_needle_tools_context(
            "q",
            [example] * (payloads.MAX_NEEDLE_INPUT_EXAMPLES + 1),
            tokenizer="needle-v0.1.0-pinned",
            token_counter=lambda _text: 0,
        )
    with pytest.raises(capsule.CapsuleValidationError, match="secret-like"):
        payloads.build_needle_tools_context(
            "q",
            [
                {
                    **example,
                    "chosen": "github_pat_0123456789abcdefghij",
                }
            ],
            tokenizer="needle-v0.1.0-pinned",
            token_counter=lambda _text: 0,
        )

    vector = json.loads(CONFORMANCE.read_text(encoding="utf-8"))["semantic_vectors"][
        "needle_tools_context_v1"
    ]
    vector_projection = payloads.build_needle_tools_context(
        vector["query"],
        vector["examples"],
        tokenizer=vector["tokenizer"],
        token_counter=lambda text: len(text.encode("utf-8")),
        max_encoder_tokens=vector["max_encoder_tokens"],
    )
    assert vector_projection["encoder_tokens"] == vector["expected_encoder_tokens"]
    assert (
        vector_projection["used_source_capsules"]
        == vector["expected_used_source_capsules"]
    )
    assert hashlib.sha256(capsule.canonicalize(vector_projection)).hexdigest() == (
        vector["expected_projection_sha256"]
    )


def test_payload_protocol_layer_does_not_touch_consumer_sqlite():
    source = inspect.getsource(payloads)
    assert "aiosqlite" not in source
    assert "GrokSessionStore" not in source
    assert "grok_sessions.db" not in source
