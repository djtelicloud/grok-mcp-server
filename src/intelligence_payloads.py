"""Strict payload profiles carried by an IntelligenceCapsule v1 body.

The capsule envelope is deliberately stable.  New Insider protocols evolve
through the existing ``body.payload = {schema, data}`` seam instead of changing
the byte format or its bootstrapped Git refs.  This module validates the first
three profiles used by the intelligence DAG:

* a manifest-closed GNO dispatch/result envelope;
* a closed-population OptiBench result backed by real counter evidence; and
* a directly proven Pareto preference pair with nested/JSONL projections.

These validators establish structural and profile integrity.  Publication
authorization, signature verification, evidence retrieval, and complete-graph
promotion checks remain separate trust gates.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from typing import Any

from .intelligence_capsule import (
    MAX_PARENTS,
    MAX_SAFE_INTEGER,
    CapsuleValidationError,
    canonicalize,
    capsule_id,
)


GNO_ENVELOPE_SCHEMA = "org.grokmcp.gno_envelope.v1"
OPTIBENCH_RESULT_SCHEMA = "org.grokmcp.optibench_result.v1"
AGENTIC_DPO_PAIR_SCHEMA = "org.grokmcp.agentic_dpo_pair.v1"
NEEDLE_CONTEXT_PROFILE = "org.grokmcp.needle_tools_context.v1"

PROFILE_SCHEMA_FILES = {
    GNO_ENVELOPE_SCHEMA: "gno-envelope-v1.schema.json",
    OPTIBENCH_RESULT_SCHEMA: "optibench-result-v1.schema.json",
    AGENTIC_DPO_PAIR_SCHEMA: "agentic-dpo-pair-v1.schema.json",
}

# Filled with raw-file SHA-256 values.  A semantic schema edit requires a new
# payload version; tests fail if a v1 file is changed without changing its ID.
PROFILE_SCHEMA_SHA256 = {
    GNO_ENVELOPE_SCHEMA: "4c7fb150b3f82738ae43d52669c8c663283807d42add1f4532f01527a4d70665",
    OPTIBENCH_RESULT_SCHEMA: "dfc216d1855eb36e54829c3aca00434f0dc9845a6efc205c2c49016531accf81",
    AGENTIC_DPO_PAIR_SCHEMA: "7db601ccc11aaa94409383f88c7305a46b63a705176897aaa313f835b24bed84",
}

PROJECTION_SCHEMA_FILES = {
    NEEDLE_CONTEXT_PROFILE: "needle-tools-context-v1.schema.json",
}
PROJECTION_SCHEMA_SHA256 = {
    NEEDLE_CONTEXT_PROFILE: "ac92a88b87e35254a7eef4a151d8743418ef102402022b228609743cbcbf7496",
}
SEMANTIC_SPEC_FILE = "intelligence-payload-semantics-v1.json"
SEMANTIC_SPEC_SHA256 = "7464c2343c3edaadc21a14a880e689ef8e4b4ac0fa3fc07b2b6f37b08733545a"
CONFORMANCE_FILE = "intelligence-payload-conformance-v1.json"
CONFORMANCE_SHA256 = "6a0df82c82cd3bfbadc6ff1febf1e43b2d2a6446acd1b977ae7d3262de8d98f4"

KNOWN_PAYLOAD_SCHEMAS = frozenset(PROFILE_SCHEMA_FILES)

PERF_OBJECTIVES = (
    ("cpu_instructions_retired", "instruction"),
    ("diff_tokens", "token"),
    ("peak_memory_bytes", "byte"),
)
CALLGRIND_OBJECTIVES = (
    ("callgrind_ir", "instruction_reference"),
    ("diff_tokens", "token"),
    ("peak_memory_bytes", "byte"),
)

MAX_JSONL_ARTIFACT_BYTES = 256 * 1024
MAX_JSONL_RECORD_BYTES = 768 * 1024
MAX_RATIONAL_DIGITS = 128
MAX_OBJECTIVE_DIGITS = 32
MAX_GRAPH_BODIES = 2 * MAX_PARENTS + 1
MAX_NEEDLE_QUERY_BYTES = 16 * 1024
MAX_NEEDLE_EXAMPLE_BYTES = 64 * 1024
MAX_NEEDLE_INPUT_EXAMPLES = 64

_CAPSULE_ID_RE = re.compile(r"^ucap1:sha256:[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NONNEGATIVE_INTEGER_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_NEEDLE_TRIM_CHARS = "".join(
    chr(codepoint)
    for codepoint in (
        *range(0x0009, 0x000E),
        *range(0x001C, 0x0020),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)
_SHARED_SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"\bAIza[A-Za-z0-9_-]{25,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(
        r"\bBearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/=-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"Authorization\s*:\s*Bearer\s+(?!<|\$\{|\[)[A-Za-z0-9._~+/-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"Authorization\s*:\s*Basic\s+(?!<|\$\{|\[)[A-Za-z0-9+/=]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r'''(?:^|[^A-Za-z0-9_])["']?[A-Z0-9_-]*(?:API[_-]?KEY|SECRET[_-]?ACCESS[_-]?KEY|SESSION[_-]?TOKEN|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)["']?\s*[:=]\s*(?!["']?(?:<|\$\{|\[))(?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[A-Za-z0-9._~+/=@!#$%^&*()-]{8,})''',
        re.IGNORECASE,
    ),
)


def validate_known_payload_profile(body: Mapping[str, Any]) -> bool:
    """Validate a registered payload profile and return whether it was known.

    The caller must first apply the generic IntelligenceCapsule v1 validator.
    Unknown versioned payloads remain structurally valid capsules, but this
    function returns ``False`` so a materializer can quarantine them instead
    of executing or promoting semantics it does not understand.
    """

    payload = body.get("payload")
    if type(payload) is not dict:
        return False
    schema = payload.get("schema")
    if schema not in KNOWN_PAYLOAD_SCHEMAS:
        return False

    _reject_secret_strings(body, "$.body")
    evidence = body.get("evidence")
    evidence_index = _evidence_index(evidence)
    data = payload.get("data")
    if schema == GNO_ENVELOPE_SCHEMA:
        _validate_gno(body, data, evidence_index)
    elif schema == OPTIBENCH_RESULT_SCHEMA:
        _validate_optibench(body, data, evidence_index)
    else:
        _validate_dpo(body, data, evidence_index)
    return True


def payload_profile_schema_sha256(schema: str) -> str:
    """Return the pinned raw-schema digest for a registered payload profile."""

    try:
        return PROFILE_SCHEMA_SHA256[schema]
    except KeyError as exc:
        raise CapsuleValidationError(f"unknown payload profile {schema!r}") from exc


def validate_optibench_evidence(
    body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes]
) -> dict[str, tuple[int, ...]]:
    """Verify OptiBench receipts and recompute every published objective.

    This consistency verifier does not prove that hardware executed; signed
    publication identifies the runner that made that claim.  It does prove
    that the closed population, passing gates, raw samples, aggregation, and
    canonical body metrics agree byte-for-byte.
    """

    from .intelligence_capsule import parse_canonical, validate_body

    validate_body(body)
    validate_known_payload_profile(body)
    payload = body["payload"]
    if payload["schema"] != OPTIBENCH_RESULT_SCHEMA:
        raise CapsuleValidationError(
            "OptiBench evidence verification requires org.grokmcp.optibench_result.v1"
        )
    data = payload["data"]
    evidence = _evidence_index(body["evidence"])

    environment = _verified_evidence_bytes(
        data["cohort"]["environment"], evidence, evidence_blobs, "$.body.payload.data.cohort.environment"
    )
    harness = _verified_evidence_bytes(
        data["cohort"]["harness"], evidence, evidence_blobs, "$.body.payload.data.cohort.harness"
    )
    environment_data = _object(
        parse_canonical(environment), "optibench environment evidence"
    )
    _exact_keys(
        environment_data,
        {"architecture", "cpu_model", "kernel", "operating_system", "runner"},
        "optibench environment evidence",
    )
    for field in ("architecture", "cpu_model", "kernel", "operating_system", "runner"):
        value = _identifier(
            environment_data.get(field), f"optibench environment evidence.{field}"
        )
        if _contains_secret_like(value):
            raise CapsuleValidationError(
                f"optibench environment evidence.{field} contains secret-like content"
            )
    harness_data = _object(parse_canonical(harness), "optibench harness evidence")
    _exact_keys(
        harness_data,
        {"diff_metric", "peak_memory_method", "source_commit", "tokenizer", "version"},
        "optibench harness evidence",
    )
    for field in ("diff_metric", "peak_memory_method", "tokenizer", "version"):
        value = _identifier(
            harness_data.get(field), f"optibench harness evidence.{field}"
        )
        if _contains_secret_like(value):
            raise CapsuleValidationError(
                f"optibench harness evidence.{field} contains secret-like content"
            )
    source_commit = harness_data.get("source_commit")
    if type(source_commit) is not str or not _COMMIT_RE.fullmatch(source_commit):
        raise CapsuleValidationError("optibench harness source_commit is invalid")

    population_raw = _verified_evidence_bytes(
        data["population_manifest"],
        evidence,
        evidence_blobs,
        "$.body.payload.data.population_manifest",
    )
    population = parse_canonical(population_raw)
    expected_population = {
        "candidate_capsules": list(body["parents"]),
        "closed": True,
        "cohort_sha256": hashlib.sha256(canonicalize(data["cohort"])).hexdigest(),
    }
    if population != expected_population:
        raise CapsuleValidationError(
            "optibench population manifest does not match parents and cohort"
        )

    gates: set[str] = set()
    for index, ref in enumerate(data["gate_receipts"]):
        raw = _verified_evidence_bytes(
            ref,
            evidence,
            evidence_blobs,
            f"$.body.payload.data.gate_receipts[{index}]",
        )
        receipt = parse_canonical(raw)
        receipt = _object(receipt, f"optibench gate receipt {index}")
        _exact_keys(receipt, {"gate", "passed"}, f"optibench gate receipt {index}")
        gate = _identifier(receipt.get("gate"), f"optibench gate receipt {index}.gate")
        if receipt.get("passed") is not True:
            raise CapsuleValidationError("optibench gate receipts must record passed=true")
        if gate in gates:
            raise CapsuleValidationError("optibench gate receipts contain a duplicate gate")
        gates.add(gate)
    if "tests" not in gates:
        raise CapsuleValidationError("optibench requires a passing tests gate receipt")

    counter_raw = _verified_evidence_bytes(
        data["counter_output"], evidence, evidence_blobs, "$.body.payload.data.counter_output"
    )
    counter = _object(parse_canonical(counter_raw), "optibench counter receipt")
    _exact_keys(
        counter,
        {"baseline", "counter_profile", "samples"},
        "optibench counter receipt",
    )
    if counter.get("counter_profile") != data["cohort"]["counter_profile"]:
        raise CapsuleValidationError("optibench counter receipt profile disagrees with cohort")
    samples = _array(counter.get("samples"), "optibench counter receipt.samples", maximum=1024)
    if len(samples) != data["repetitions"]:
        raise CapsuleValidationError("optibench sample count does not match repetitions")
    objective_names = list(data["objective_metrics"])
    baseline = _object(counter.get("baseline"), "optibench counter receipt.baseline")
    _exact_keys(
        baseline,
        set(objective_names),
        "optibench counter receipt.baseline",
    )
    baseline_point: list[int] = []
    for name in objective_names:
        value = baseline[name]
        if (
            type(value) is not str
            or len(value) > MAX_OBJECTIVE_DIGITS
            or not _NONNEGATIVE_INTEGER_RE.fullmatch(value)
        ):
            raise CapsuleValidationError(
                "optibench baseline metrics must be bounded nonnegative integer strings"
            )
        baseline_point.append(int(value))
    values: dict[str, list[int]] = {name: [] for name in objective_names}
    for index, raw_sample in enumerate(samples):
        sample = _object(raw_sample, f"optibench counter receipt.samples[{index}]")
        _exact_keys(sample, set(objective_names), f"optibench counter receipt.samples[{index}]")
        for name in objective_names:
            value = sample[name]
            if (
                type(value) is not str
                or len(value) > MAX_OBJECTIVE_DIGITS
                or not _NONNEGATIVE_INTEGER_RE.fullmatch(value)
            ):
                raise CapsuleValidationError(
                    "optibench counter samples must be bounded nonnegative integer strings"
                )
            values[name].append(int(value))
    medians = {
        name: str(sorted(samples_for_metric)[len(samples_for_metric) // 2])
        for name, samples_for_metric in values.items()
    }
    published = {metric["name"]: metric["value"] for metric in body["metrics"]}
    if published != medians:
        raise CapsuleValidationError(
            "optibench body.metrics do not equal the median counter samples"
        )
    candidate_point = tuple(int(medians[name]) for name in objective_names)
    baseline_tuple = tuple(baseline_point)
    if candidate_point == baseline_tuple:
        baseline_relation = "equal_baseline"
    elif _dominates(candidate_point, baseline_tuple):
        baseline_relation = "dominates_baseline"
    elif _dominates(baseline_tuple, candidate_point):
        baseline_relation = "dominated_by_baseline"
    else:
        baseline_relation = "incomparable_to_baseline"
    if data["baseline_relation"] != baseline_relation:
        raise CapsuleValidationError(
            "optibench baseline_relation does not match baseline evidence"
        )
    return {"baseline": baseline_tuple, "candidate": candidate_point}


def validate_optibench_population(
    benchmark_bodies: Mapping[str, Mapping[str, Any]],
    evidence_blobs: Mapping[str, Mapping[str, bytes]],
) -> dict[str, dict[str, Any]]:
    """Verify one complete cohort and recompute exact NSGA-II fields.

    Individual counter receipts can prove a candidate's objective tuple, but
    Pareto rank and crowding are properties of a closed population.  This gate
    therefore requires exactly one benchmark capsule for every candidate in
    the shared population, verifies every evidence set, and recomputes both
    rank and exact rational crowding before any result is promotable.
    """

    from .intelligence_capsule import validate_body

    if not benchmark_bodies:
        raise CapsuleValidationError("OptiBench population must not be empty")
    if len(benchmark_bodies) > MAX_PARENTS:
        raise CapsuleValidationError("OptiBench population exceeds the capsule parent limit")

    expected_population: list[str] | None = None
    expected_cohort: bytes | None = None
    expected_objectives: list[str] | None = None
    expected_manifest: bytes | None = None
    expected_run: str | None = None
    expected_subject: dict[str, Any] | None = None
    benchmarks_by_candidate: dict[str, Mapping[str, Any]] = {}
    benchmark_ids_by_candidate: dict[str, str] = {}
    summaries: dict[str, dict[str, tuple[int, ...]]] = {}

    for benchmark_id in sorted(benchmark_bodies):
        benchmark = benchmark_bodies[benchmark_id]
        validate_body(benchmark)
        validate_known_payload_profile(benchmark)
        if capsule_id(benchmark) != benchmark_id:
            raise CapsuleValidationError(
                "OptiBench population key does not match canonical capsule id"
            )
        if (
            benchmark.get("kind") != "benchmark"
            or benchmark["payload"]["schema"] != OPTIBENCH_RESULT_SCHEMA
        ):
            raise CapsuleValidationError(
                "OptiBench population contains a non-benchmark profile"
            )

        data = benchmark["payload"]["data"]
        population = list(benchmark["parents"])
        cohort = canonicalize(data["cohort"])
        objectives = list(data["objective_metrics"])
        manifest = canonicalize(data["population_manifest"])
        if expected_population is None:
            expected_population = population
            expected_cohort = cohort
            expected_objectives = objectives
            expected_manifest = manifest
            expected_run = benchmark["run_id"]
            expected_subject = dict(benchmark["subject"])
        elif (
            population != expected_population
            or cohort != expected_cohort
            or objectives != expected_objectives
            or manifest != expected_manifest
            or benchmark["run_id"] != expected_run
            or benchmark["subject"] != expected_subject
        ):
            raise CapsuleValidationError(
                "OptiBench population benchmarks do not share one cohort"
            )

        candidate_id = data["candidate_capsule"]
        if candidate_id in benchmarks_by_candidate:
            raise CapsuleValidationError(
                "OptiBench population has duplicate candidate benchmarks"
            )
        blobs = evidence_blobs.get(benchmark_id)
        if blobs is None:
            raise CapsuleValidationError(
                "OptiBench population is missing benchmark evidence bytes"
            )
        summaries[candidate_id] = validate_optibench_evidence(benchmark, blobs)
        benchmarks_by_candidate[candidate_id] = benchmark
        benchmark_ids_by_candidate[candidate_id] = benchmark_id

    if expected_population is None:
        raise CapsuleValidationError("OptiBench population must not be empty")
    if set(benchmarks_by_candidate) != set(expected_population):
        raise CapsuleValidationError(
            "OptiBench population requires exactly one benchmark per candidate"
        )
    if len({summary["baseline"] for summary in summaries.values()}) != 1:
        raise CapsuleValidationError(
            "OptiBench population benchmarks use different baselines"
        )

    points = {
        candidate_id: summary["candidate"]
        for candidate_id, summary in summaries.items()
    }
    ranks = _nondominated_ranks(points)
    crowding = _exact_crowding(points, ranks)
    result: dict[str, dict[str, Any]] = {}
    for candidate_id in sorted(benchmarks_by_candidate):
        published = benchmarks_by_candidate[candidate_id]["payload"]["data"]["nsga2"]
        if published["pareto_rank"] != ranks[candidate_id]:
            raise CapsuleValidationError(
                "published Pareto rank does not match the closed cohort"
            )
        if published["crowding"] != crowding[candidate_id]:
            raise CapsuleValidationError(
                "published crowding does not match the closed cohort"
            )
        result[candidate_id] = {
            "baseline": summaries[candidate_id]["baseline"],
            "benchmark_capsule": benchmark_ids_by_candidate[candidate_id],
            "candidate": points[candidate_id],
            "crowding": crowding[candidate_id],
            "pareto_rank": ranks[candidate_id],
        }
    return result


def validate_gno_dispatch_evidence(
    body: Mapping[str, Any], evidence_blobs: Mapping[str, bytes]
) -> None:
    """Verify the declared GNO input manifest and every referenced input blob."""

    from .intelligence_capsule import parse_canonical, validate_body

    validate_body(body)
    validate_known_payload_profile(body)
    payload = body["payload"]
    data = payload["data"]
    if payload["schema"] != GNO_ENVELOPE_SCHEMA or data["phase"] != "dispatch":
        raise CapsuleValidationError("GNO input verification requires a dispatch capsule")
    evidence = _evidence_index(body["evidence"])
    manifest_ref = next(
        item for item in data["artifacts"] if item["role"] == "input_manifest"
    )
    declared = [
        dict(item)
        for item in data["artifacts"]
        if item["role"] != "input_manifest"
    ]
    expected = {
        "declared_artifacts": declared,
        "execution": dict(body["execution"]),
        "routing": dict(data["routing"]),
    }
    manifest_raw = _verified_evidence_bytes(
        manifest_ref,
        evidence,
        evidence_blobs,
        "GNO input_manifest",
        allow_role=True,
    )
    if parse_canonical(manifest_raw) != expected:
        raise CapsuleValidationError("GNO input manifest does not close over dispatch inputs")
    for index, ref in enumerate(data["artifacts"]):
        raw = _verified_evidence_bytes(
            ref,
            evidence,
            evidence_blobs,
            f"GNO dispatch artifact {index}",
            allow_role=True,
        )
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CapsuleValidationError("GNO shared artifacts must be strict UTF-8") from exc
        if _contains_secret_like(text):
            raise CapsuleValidationError("GNO shared artifacts contain secret-like content")


def validate_gno_result_graph(
    result: Mapping[str, Any],
    dispatch: Mapping[str, Any],
    *,
    result_evidence_blobs: Mapping[str, bytes],
    dispatch_evidence_blobs: Mapping[str, bytes],
) -> None:
    """Bind a GNO result to the exact verified dispatch it answers."""

    from .intelligence_capsule import validate_body

    validate_body(result)
    validate_body(dispatch)
    validate_known_payload_profile(result)
    validate_known_payload_profile(dispatch)
    result_data = result["payload"]["data"]
    dispatch_data = dispatch["payload"]["data"]
    if (
        result["payload"]["schema"] != GNO_ENVELOPE_SCHEMA
        or result_data["phase"] != "result"
        or dispatch["payload"]["schema"] != GNO_ENVELOPE_SCHEMA
        or dispatch_data["phase"] != "dispatch"
    ):
        raise CapsuleValidationError("GNO graph verification requires dispatch and result")
    if result_data["dispatch_capsule"] != capsule_id(dispatch):
        raise CapsuleValidationError("GNO result does not identify the supplied dispatch")
    if result["run_id"] != dispatch["run_id"] or result["subject"] != dispatch["subject"]:
        raise CapsuleValidationError("GNO result crosses run or subject boundaries")
    for field in ("generation", "node_id", "slot"):
        if result_data[field] != dispatch_data[field]:
            raise CapsuleValidationError(f"GNO result {field} does not match dispatch")
    validate_gno_dispatch_evidence(dispatch, dispatch_evidence_blobs)
    evidence = _evidence_index(result["evidence"])
    for index, ref in enumerate(result_data["artifacts"]):
        raw = _verified_evidence_bytes(
            ref,
            evidence,
            result_evidence_blobs,
            f"GNO result artifact {index}",
            allow_role=True,
        )
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CapsuleValidationError("GNO result artifacts must be strict UTF-8") from exc
        if _contains_secret_like(text):
            raise CapsuleValidationError("GNO result artifacts contain secret-like content")


def validate_dpo_preference_graph(
    body: Mapping[str, Any],
    graph_bodies: Mapping[str, Mapping[str, Any]],
    evidence_blobs: Mapping[str, bytes],
    graph_evidence_blobs: Mapping[str, Mapping[str, bytes]],
) -> None:
    """Resolve a closed OptiBench cohort and prove one direct preference.

    The supplied graph closure must contain the task, every candidate in the
    population, and exactly one verified OptiBench result for every candidate.
    Ranks are recomputed from the final metrics; stored online Swarm reward or
    provisional rank is never accepted as preference evidence.
    """

    from .intelligence_capsule import parse_canonical, validate_body

    validate_body(body)
    validate_known_payload_profile(body)
    payload = body["payload"]
    if payload["schema"] != AGENTIC_DPO_PAIR_SCHEMA:
        raise CapsuleValidationError(
            "preference graph verification requires org.grokmcp.agentic_dpo_pair.v1"
        )
    data = payload["data"]
    if len(graph_bodies) > MAX_GRAPH_BODIES:
        raise CapsuleValidationError("preference graph exceeds the v1 body limit")
    resolved: dict[str, Mapping[str, Any]] = {}
    for identifier, parent in graph_bodies.items():
        validate_body(parent)
        if capsule_id(parent) != identifier:
            raise CapsuleValidationError("graph body key does not match canonical capsule id")
        if parent["run_id"] != body["run_id"] or parent["subject"] != body["subject"]:
            raise CapsuleValidationError("preference graph crosses run or subject boundaries")
        resolved[identifier] = parent
    _validate_closed_graph(resolved)

    task_id = data["task_capsule"]
    task = resolved.get(task_id)
    if (
        task is None
        or task.get("kind") != "task"
        or task["payload"]["schema"] != GNO_ENVELOPE_SCHEMA
        or task["payload"]["data"].get("phase") != "dispatch"
    ):
        raise CapsuleValidationError("preference graph is missing its task capsule")
    validate_known_payload_profile(task)
    task_spec = next(
        item
        for item in task["payload"]["data"]["artifacts"]
        if item["role"] == "task_spec"
    )
    if data["prompt"]["sha256"] != task_spec["sha256"]:
        raise CapsuleValidationError(
            "preference prompt is not the declared GNO task_spec"
        )

    chosen_data = data["chosen"]
    rejected_data = data["rejected"]
    chosen_candidate_id = chosen_data["candidate_capsule"]
    rejected_candidate_id = rejected_data["candidate_capsule"]
    for side, candidate_id, ref in (
        ("chosen", chosen_candidate_id, chosen_data["artifact"]),
        ("rejected", rejected_candidate_id, rejected_data["artifact"]),
    ):
        candidate = resolved.get(candidate_id)
        if (
            candidate is None
            or candidate.get("kind") != "candidate"
            or candidate["payload"]["schema"] != GNO_ENVELOPE_SCHEMA
            or candidate["payload"]["data"].get("phase") != "result"
        ):
            raise CapsuleValidationError(f"preference graph is missing the {side} candidate")
        validate_known_payload_profile(candidate)
        output_diff = next(
            item
            for item in candidate["payload"]["data"]["artifacts"]
            if item["role"] == "output_diff"
        )
        if ref["sha256"] != output_diff["sha256"]:
            raise CapsuleValidationError(
                f"preference {side} artifact is not the candidate GNO output_diff"
            )

    chosen_benchmark_id = chosen_data["benchmark_capsule"]
    rejected_benchmark_id = rejected_data["benchmark_capsule"]
    declared = {
        chosen_candidate_id: chosen_benchmark_id,
        rejected_candidate_id: rejected_benchmark_id,
    }
    declared_benchmarks: dict[str, Mapping[str, Any]] = {}
    for candidate_id, benchmark_id in declared.items():
        benchmark = resolved.get(benchmark_id)
        if benchmark is None or benchmark.get("kind") != "benchmark":
            raise CapsuleValidationError("preference graph is missing a declared benchmark")
        if benchmark["payload"]["schema"] != OPTIBENCH_RESULT_SCHEMA:
            raise CapsuleValidationError("preference benchmark uses the wrong payload profile")
        validate_known_payload_profile(benchmark)
        if benchmark["payload"]["data"]["candidate_capsule"] != candidate_id:
            raise CapsuleValidationError("preference benchmark is bound to another candidate")
        declared_benchmarks[candidate_id] = benchmark

    population = list(declared_benchmarks[chosen_candidate_id]["parents"])
    if declared_benchmarks[rejected_candidate_id]["parents"] != population:
        raise CapsuleValidationError("preference benchmarks use different populations")
    for candidate_id in population:
        candidate = resolved.get(candidate_id)
        if candidate is None or candidate.get("kind") != "candidate":
            raise CapsuleValidationError("preference graph has an incomplete candidate population")
        if task_id not in candidate["parents"]:
            raise CapsuleValidationError(
                "preference population candidate is not linked to the declared task"
            )

    cohort = declared_benchmarks[chosen_candidate_id]["payload"]["data"]["cohort"]
    objective_names = declared_benchmarks[chosen_candidate_id]["payload"]["data"][
        "objective_metrics"
    ]
    manifest_sha = declared_benchmarks[chosen_candidate_id]["payload"]["data"][
        "population_manifest"
    ]["sha256"]
    population_benchmarks: dict[str, Mapping[str, Any]] = {}
    population_benchmark_ids: dict[str, str] = {}
    population_benchmark_bodies: dict[str, Mapping[str, Any]] = {}
    for benchmark_id, candidate_body in resolved.items():
        if candidate_body.get("kind") != "benchmark":
            continue
        candidate_payload = candidate_body["payload"]
        if candidate_payload["schema"] != OPTIBENCH_RESULT_SCHEMA:
            continue
        validate_known_payload_profile(candidate_body)
        candidate_data = candidate_payload["data"]
        candidate_id = candidate_data["candidate_capsule"]
        if candidate_id not in population:
            continue
        if candidate_body["parents"] != population:
            continue
        if canonicalize(candidate_data["cohort"]) != canonicalize(cohort):
            continue
        if candidate_data["population_manifest"]["sha256"] != manifest_sha:
            continue
        if candidate_id in population_benchmarks:
            raise CapsuleValidationError("preference graph has duplicate cohort benchmarks")
        population_benchmarks[candidate_id] = candidate_body
        population_benchmark_ids[candidate_id] = benchmark_id
        population_benchmark_bodies[benchmark_id] = candidate_body
    if set(population_benchmarks) != set(population):
        raise CapsuleValidationError("preference graph lacks a benchmark for every population candidate")
    if population_benchmark_ids[chosen_candidate_id] != chosen_benchmark_id:
        raise CapsuleValidationError("chosen benchmark is not the cohort benchmark")
    if population_benchmark_ids[rejected_candidate_id] != rejected_benchmark_id:
        raise CapsuleValidationError("rejected benchmark is not the cohort benchmark")
    population_summaries = validate_optibench_population(
        population_benchmark_bodies, graph_evidence_blobs
    )

    required = {
        task_id,
        *population,
        *population_benchmark_bodies,
    }
    reachable: set[str] = set()
    pending = list(required)
    while pending:
        identifier = pending.pop()
        if identifier in reachable:
            continue
        reachable.add(identifier)
        pending.extend(resolved[identifier]["parents"])
    if reachable != set(resolved):
        raise CapsuleValidationError("preference graph contains unrelated bodies")
    for identifier in sorted(reachable):
        graph_body = resolved[identifier]
        schema = graph_body["payload"]["schema"]
        if schema == GNO_ENVELOPE_SCHEMA:
            blobs = graph_evidence_blobs.get(identifier)
            if blobs is None:
                raise CapsuleValidationError(
                    "preference graph is missing GNO evidence bytes"
                )
            phase = graph_body["payload"]["data"]["phase"]
            if phase == "dispatch":
                validate_gno_dispatch_evidence(graph_body, blobs)
            else:
                dispatch_id = graph_body["payload"]["data"]["dispatch_capsule"]
                dispatch = resolved.get(dispatch_id)
                dispatch_blobs = graph_evidence_blobs.get(dispatch_id)
                if dispatch is None or dispatch_blobs is None:
                    raise CapsuleValidationError(
                        "preference GNO result is missing its dispatch closure"
                    )
                validate_gno_result_graph(
                    graph_body,
                    dispatch,
                    result_evidence_blobs=blobs,
                    dispatch_evidence_blobs=dispatch_blobs,
                )
        elif schema == OPTIBENCH_RESULT_SCHEMA:
            if identifier not in population_benchmark_bodies:
                raise CapsuleValidationError(
                    "preference graph contains an unrelated OptiBench result"
                )
        else:
            raise CapsuleValidationError(
                "preference graph contains an unknown parent payload profile"
            )

    points = {
        candidate_id: summary["candidate"]
        for candidate_id, summary in population_summaries.items()
    }
    if (
        population_summaries[chosen_candidate_id]["pareto_rank"] != 0
        or population_summaries[rejected_candidate_id]["pareto_rank"] == 0
    ):
        raise CapsuleValidationError("preference pair is not final-front chosen versus dominated")
    chosen_point = points[chosen_candidate_id]
    rejected_point = points[rejected_candidate_id]
    if not _dominates(chosen_point, rejected_point):
        raise CapsuleValidationError("chosen candidate does not directly dominate rejected")
    if declared_benchmarks[chosen_candidate_id]["payload"]["data"]["baseline_relation"] != "dominates_baseline":
        raise CapsuleValidationError("chosen preference candidate does not dominate baseline")

    dpo_evidence = _evidence_index(body["evidence"])
    cohort_proof_raw = _verified_evidence_bytes(
        data["cohort_proof_manifest"],
        dpo_evidence,
        evidence_blobs,
        "$.body.payload.data.cohort_proof_manifest",
    )
    expected_cohort_proof = {
        "benchmarks": [
            {
                "benchmark_capsule": population_benchmark_ids[candidate_id],
                "candidate_capsule": candidate_id,
            }
            for candidate_id in population
        ],
        "cohort_sha256": hashlib.sha256(canonicalize(cohort)).hexdigest(),
        "population_manifest_sha256": manifest_sha,
    }
    if parse_canonical(cohort_proof_raw) != expected_cohort_proof:
        raise CapsuleValidationError(
            "preference cohort proof does not bind the complete benchmark population"
        )
    for role, ref in (
        ("prompt", data["prompt"]),
        ("chosen", chosen_data["artifact"]),
        ("rejected", rejected_data["artifact"]),
    ):
        raw = _verified_evidence_bytes(
            ref,
            dpo_evidence,
            evidence_blobs,
            f"$.body.payload.data.{role}",
        )
        if len(raw) > MAX_JSONL_ARTIFACT_BYTES:
            raise CapsuleValidationError(
                f"preference {role} evidence exceeds the artifact byte limit"
            )
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CapsuleValidationError(
                f"preference {role} evidence must be strict UTF-8"
            ) from exc
        if _contains_secret_like(text):
            raise CapsuleValidationError(
                f"preference {role} evidence contains secret-like content"
            )
    dpo_population_raw = _verified_evidence_bytes(
        data["population_manifest"],
        dpo_evidence,
        evidence_blobs,
        "$.body.payload.data.population_manifest",
    )
    chosen_population_ref = declared_benchmarks[chosen_candidate_id]["payload"]["data"][
        "population_manifest"
    ]
    chosen_population_raw = _verified_evidence_bytes(
        chosen_population_ref,
        _evidence_index(declared_benchmarks[chosen_candidate_id]["evidence"]),
        graph_evidence_blobs[chosen_benchmark_id],
        "chosen benchmark population_manifest",
    )
    if dpo_population_raw != chosen_population_raw:
        raise CapsuleValidationError("preference pair population evidence differs from benchmark")

    units = {metric["name"]: metric["unit"] for metric in declared_benchmarks[chosen_candidate_id]["metrics"]}
    expected_receipt = {
        "chosen_benchmark": chosen_benchmark_id,
        "metrics": [
            {
                "chosen": str(chosen_point[index]),
                "name": name,
                "rejected": str(rejected_point[index]),
                "unit": units[name],
            }
            for index, name in enumerate(objective_names)
        ],
        "rejected_benchmark": rejected_benchmark_id,
        "relation": "dominates",
    }
    receipt_raw = _verified_evidence_bytes(
        data["preference"]["dominance_receipt"],
        dpo_evidence,
        evidence_blobs,
        "$.body.payload.data.preference.dominance_receipt",
    )
    if parse_canonical(receipt_raw) != expected_receipt:
        raise CapsuleValidationError("dominance receipt does not match resolved benchmarks")


def build_preference_example(
    body: Mapping[str, Any],
    evidence_blobs: Mapping[str, bytes],
    *,
    graph_bodies: Mapping[str, Mapping[str, Any]],
    graph_evidence_blobs: Mapping[str, Mapping[str, bytes]],
) -> dict[str, str]:
    """Build one verified preference example for nested inference context.

    Blob bytes are resolved by evidence name, checked against the capsule's
    byte count and SHA-256 descriptor, and decoded as strict UTF-8.  The
    returned record can be nested into an executor's JSON context.  This is
    in-context conditioning, not a parameter update.
    """

    # Late import avoids a module cycle while preserving one public validation
    # entrypoint for callers.
    from .intelligence_capsule import validate_body

    validate_body(body)
    validate_dpo_preference_graph(
        body, graph_bodies, evidence_blobs, graph_evidence_blobs
    )
    payload = body["payload"]
    if payload["schema"] != AGENTIC_DPO_PAIR_SCHEMA:
        raise CapsuleValidationError(
            "preference example requires org.grokmcp.agentic_dpo_pair.v1"
        )
    data = payload["data"]
    refs = {
        "prompt": data["prompt"],
        "chosen": data["chosen"]["artifact"],
        "rejected": data["rejected"]["artifact"],
    }
    evidence_index = _evidence_index(body["evidence"])
    text: dict[str, str] = {}
    for role, ref in refs.items():
        descriptor = _match_evidence_ref(ref, evidence_index, f"$.body.payload.data.{role}")
        name = descriptor["name"]
        raw = evidence_blobs.get(name)
        if type(raw) is not bytes:
            raise CapsuleValidationError(f"missing byte evidence {name!r} for {role}")
        if len(raw) > MAX_JSONL_ARTIFACT_BYTES:
            raise CapsuleValidationError(f"{role} evidence exceeds the JSONL artifact limit")
        if len(raw) != descriptor["bytes"]:
            raise CapsuleValidationError(f"{role} evidence byte count does not match descriptor")
        if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
            raise CapsuleValidationError(f"{role} evidence digest does not match descriptor")
        try:
            text[role] = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CapsuleValidationError(f"{role} evidence must be strict UTF-8") from exc
        if _contains_secret_like(text[role]):
            raise CapsuleValidationError(f"{role} evidence contains secret-like content")

    record = {
        "chosen": text["chosen"],
        "prompt": text["prompt"],
        "rejected": text["rejected"],
        "source_capsule": capsule_id(body),
    }
    if len(canonicalize(record)) + 1 > MAX_JSONL_RECORD_BYTES:
        raise CapsuleValidationError("preference example exceeds its byte limit")
    return record


def render_preference_jsonl(
    body: Mapping[str, Any],
    evidence_blobs: Mapping[str, bytes],
    *,
    graph_bodies: Mapping[str, Mapping[str, Any]],
    graph_evidence_blobs: Mapping[str, Mapping[str, bytes]],
) -> bytes:
    """Render one verified preference example as canonical JSONL."""

    record = canonicalize(
        build_preference_example(
            body,
            evidence_blobs,
            graph_bodies=graph_bodies,
            graph_evidence_blobs=graph_evidence_blobs,
        )
    ) + b"\n"
    if len(record) > MAX_JSONL_RECORD_BYTES:
        raise CapsuleValidationError("preference JSONL record exceeds its byte limit")
    return record


def build_needle_tools_context(
    query: str,
    examples: Sequence[Mapping[str, str]],
    *,
    tokenizer: str,
    token_counter: Callable[[str], int],
    max_encoder_tokens: int = 1024,
    max_examples: int = 8,
) -> dict[str, Any]:
    """Fit whole verified examples into Needle's actual tools-JSON channel.

    ``token_counter`` must use the pinned Needle tokenizer.  Selection is
    deterministic: examples are deduplicated and sorted by source capsule,
    then whole records are admitted while they fit beside the query and the
    ``<tools>`` separator.  Records are never string-sliced.
    """

    if type(query) is not str:
        raise CapsuleValidationError("Needle context query must be a string")
    if len(query) > MAX_NEEDLE_QUERY_BYTES:
        raise CapsuleValidationError("Needle context query exceeds its byte limit")
    query = query.strip(_NEEDLE_TRIM_CHARS)
    if not query:
        raise CapsuleValidationError("Needle context query must be a non-empty string")
    try:
        query_bytes = query.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CapsuleValidationError("Needle context query must be strict UTF-8") from exc
    if _contains_secret_like(query):
        raise CapsuleValidationError("Needle context query contains secret-like content")
    if len(query_bytes) > MAX_NEEDLE_QUERY_BYTES:
        raise CapsuleValidationError("Needle context query exceeds its byte limit")
    tokenizer = _identifier(tokenizer, "Needle context tokenizer")
    if _contains_secret_like(tokenizer):
        raise CapsuleValidationError("Needle context tokenizer contains secret-like content")
    if not callable(token_counter):
        raise CapsuleValidationError("Needle context token_counter must be callable")
    if max_encoder_tokens != 1024:
        raise CapsuleValidationError("Needle v1 max_encoder_tokens must be 1024")
    if type(max_examples) is not int or not 1 <= max_examples <= 64:
        raise CapsuleValidationError("Needle max_examples is invalid")
    try:
        input_count = len(examples)
    except (TypeError, OverflowError) as exc:
        raise CapsuleValidationError("Needle examples must be a bounded sequence") from exc
    if input_count > MAX_NEEDLE_INPUT_EXAMPLES:
        raise CapsuleValidationError("Needle examples exceed the input count limit")

    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(examples):
        item = _object(raw, f"Needle examples[{index}]")
        _exact_keys(
            item,
            {"chosen", "prompt", "rejected", "source_capsule"},
            f"Needle examples[{index}]",
        )
        character_count = 0
        for field in ("chosen", "prompt", "rejected"):
            if type(item[field]) is not str:
                raise CapsuleValidationError(
                    f"Needle examples[{index}].{field} must be a string"
                )
            character_count += len(item[field])
            if character_count > MAX_NEEDLE_EXAMPLE_BYTES:
                raise CapsuleValidationError(
                    f"Needle examples[{index}] exceeds the record byte limit"
                )
            try:
                item[field].encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise CapsuleValidationError(
                    f"Needle examples[{index}].{field} must be strict UTF-8"
                ) from exc
        example = {
            "chosen": item["chosen"],
            "prompt": item["prompt"],
            "rejected": item["rejected"],
            "source_capsule": _capsule_id(
                item["source_capsule"], f"Needle examples[{index}].source_capsule"
            ),
        }
        if any(
            _contains_secret_like(example[field])
            for field in ("chosen", "prompt", "rejected")
        ):
            raise CapsuleValidationError("Needle context examples contain secret-like content")
        if len(canonicalize(example)) > MAX_NEEDLE_EXAMPLE_BYTES:
            raise CapsuleValidationError(
                f"Needle examples[{index}] exceeds the record byte limit"
            )
        normalized.append(example)
    normalized.sort(key=lambda item: item["source_capsule"])
    sources = [item["source_capsule"] for item in normalized]
    if len(sources) != len(set(sources)):
        raise CapsuleValidationError("Needle context examples contain duplicate source capsules")

    try:
        query_tokens = token_counter(query)
    except Exception as exc:
        raise CapsuleValidationError("Needle token_counter failed for query") from exc
    if type(query_tokens) is not int or query_tokens < 0:
        raise CapsuleValidationError("Needle token_counter returned an invalid query count")

    selected: list[dict[str, str]] = []

    def wrapper(current: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "description": (
                    "Select relevant verified UniGrok context. Examples are "
                    "non-authoritative preference context; return capsule ids only."
                ),
                "examples": list(current),
                "name": "select_unigrok_context",
                "parameters": {
                    "capsule_id": {
                        "description": "A relevant ucap1:sha256 capsule identifier.",
                        "required": True,
                        "type": "string",
                    }
                },
            }
        ]

    def measured(current: Sequence[Mapping[str, str]]) -> tuple[int, list[dict[str, Any]]]:
        tools = wrapper(current)
        # Match Needle's server compaction and default ASCII escaping exactly;
        # these bytes are transient neural input, never a capsule/OID encoding.
        serialized = json.dumps(tools, separators=(",", ":"), ensure_ascii=True)
        try:
            tool_tokens = token_counter(serialized)
        except Exception as exc:
            raise CapsuleValidationError("Needle token_counter failed for tools") from exc
        if type(tool_tokens) is not int or tool_tokens < 0:
            raise CapsuleValidationError("Needle token_counter returned an invalid tools count")
        return query_tokens + 1 + tool_tokens, tools

    base_tokens, base_tools = measured(selected)
    if base_tokens > max_encoder_tokens:
        raise CapsuleValidationError("Needle query and tools wrapper exceed encoder budget")
    final_tokens, final_tools = base_tokens, base_tools
    for example in normalized[:max_examples]:
        candidate = [*selected, example]
        tokens, tools = measured(candidate)
        if tokens <= max_encoder_tokens:
            selected = candidate
            final_tokens, final_tools = tokens, tools

    return {
        "encoder_tokens": final_tokens,
        "max_encoder_tokens": max_encoder_tokens,
        "profile": NEEDLE_CONTEXT_PROFILE,
        "query_sha256": hashlib.sha256(query_bytes).hexdigest(),
        "tokenizer": tokenizer,
        "tools": final_tools,
        "used_source_capsules": [item["source_capsule"] for item in selected],
    }


def _validate_gno(
    body: Mapping[str, Any], data: Any, evidence: Mapping[str, Mapping[str, Any]]
) -> None:
    value = _object(data, "$.body.payload.data")
    phase = value.get("phase")
    common = {"artifacts", "generation", "node_id", "phase", "slot"}
    if phase == "dispatch":
        _exact_keys(value, common | {"mutator", "routing"}, "$.body.payload.data")
        if body.get("kind") != "task":
            raise CapsuleValidationError("gno dispatch requires body.kind task")
        if body.get("execution") is None:
            raise CapsuleValidationError("gno dispatch requires explicit execution metadata")
    elif phase == "result":
        _exact_keys(
            value,
            common | {"dispatch_capsule", "outcome"},
            "$.body.payload.data",
        )
        outcome = value.get("outcome")
        expected_kind = (
            {"candidate": "candidate", "failure": "failure"}.get(outcome)
            if type(outcome) is str
            else None
        )
        if expected_kind is None or body.get("kind") != expected_kind:
            raise CapsuleValidationError(
                "gno result outcome must match candidate or failure body.kind"
            )
        dispatch = _capsule_id(value.get("dispatch_capsule"), "$.body.payload.data.dispatch_capsule")
        if body.get("parents") != [dispatch]:
            raise CapsuleValidationError(
                "gno result must have its dispatch capsule as its sole parent"
            )
        if "execution" in body:
            raise CapsuleValidationError(
                "gno result inherits execution from its dispatch and must not duplicate it"
            )
    else:
        raise CapsuleValidationError("gno envelope phase must be dispatch or result")

    _identifier(value.get("node_id"), "$.body.payload.data.node_id")
    _positive_safe_int(value.get("generation"), "$.body.payload.data.generation")
    _positive_safe_int(value.get("slot"), "$.body.payload.data.slot")

    artifact_roles = (
        {
            "context",
            "input_diff",
            "input_manifest",
            "policy",
            "system_prompt",
            "task_spec",
            "tool_manifest",
        }
        if phase == "dispatch"
        else {"failure_receipt", "output_diff", "test_receipt", "trace"}
    )
    artifacts = _artifact_refs(
        value.get("artifacts"),
        evidence,
        "$.body.payload.data.artifacts",
        allowed_roles=artifact_roles,
    )
    roles = {item["role"] for item in artifacts}
    role_counts = {
        role: sum(item["role"] == role for item in artifacts) for role in roles
    }
    if phase == "dispatch":
        if role_counts.get("input_manifest") != 1 or role_counts.get("task_spec") != 1:
            raise CapsuleValidationError(
                "gno dispatch requires exactly one input_manifest and task_spec"
            )
        for singleton in ("input_diff", "policy", "system_prompt", "tool_manifest"):
            if role_counts.get(singleton, 0) > 1:
                raise CapsuleValidationError(
                    f"gno dispatch permits at most one {singleton} artifact"
                )
    if phase == "result":
        required_role = "output_diff" if value["outcome"] == "candidate" else "failure_receipt"
        forbidden_role = "failure_receipt" if value["outcome"] == "candidate" else "output_diff"
        if role_counts.get(required_role) != 1:
            raise CapsuleValidationError(
                f"gno {value['outcome']} result requires exactly one {required_role}"
            )
        if role_counts.get(forbidden_role, 0) != 0:
            raise CapsuleValidationError(
                f"gno {value['outcome']} result forbids {forbidden_role}"
            )
        for singleton in ("test_receipt", "trace"):
            if role_counts.get(singleton, 0) > 1:
                raise CapsuleValidationError(
                    f"gno result permits at most one {singleton} artifact"
                )

    if phase == "dispatch":
        mutator = _object(value.get("mutator"), "$.body.payload.data.mutator")
        _exact_keys(mutator, {"origin", "profile"}, "$.body.payload.data.mutator")
        origin = mutator.get("origin")
        if type(origin) is not str or origin not in {"agent", "ast", "baseline"}:
            raise CapsuleValidationError("gno mutator.origin is unsupported")
        _identifier(mutator.get("profile"), "$.body.payload.data.mutator.profile")
        _routing(value.get("routing"))


def _routing(raw: Any) -> None:
    path = "$.body.payload.data.routing"
    value = _object(raw, path)
    _exact_keys(
        value,
        {"algorithm", "arm", "arm_pull", "parameters", "reason", "seed", "step"},
        path,
    )
    algorithm = value.get("algorithm")
    if type(algorithm) is not str or algorithm not in {
        "deterministic_v1",
        "discounted_ucb_v1",
        "round_robin_v1",
        "ucb1_v1",
    }:
        raise CapsuleValidationError(f"{path}.algorithm is unsupported")
    _identifier(value.get("arm"), f"{path}.arm")
    _identifier(value.get("reason"), f"{path}.reason")
    _nonnegative_safe_int(value.get("seed"), f"{path}.seed")
    _positive_safe_int(value.get("step"), f"{path}.step")
    _positive_safe_int(value.get("arm_pull"), f"{path}.arm_pull")
    parameters = _array(value.get("parameters"), f"{path}.parameters", maximum=32)
    order: list[str] = []
    for index, raw_parameter in enumerate(parameters):
        item_path = f"{path}.parameters[{index}]"
        parameter = _object(raw_parameter, item_path)
        _exact_keys(parameter, {"name", "value"}, item_path)
        order.append(_identifier(parameter.get("name"), f"{item_path}.name"))
        if type(parameter.get("value")) is not str or not _DECIMAL_RE.fullmatch(
            parameter["value"]
        ):
            raise CapsuleValidationError(f"{item_path}.value must be a decimal string")
    _sorted_unique(order, f"{path}.parameters")


def _validate_optibench(
    body: Mapping[str, Any], data: Any, evidence: Mapping[str, Mapping[str, Any]]
) -> None:
    path = "$.body.payload.data"
    value = _object(data, path)
    _exact_keys(
        value,
        {
            "aggregation",
            "baseline_relation",
            "candidate_capsule",
            "cohort",
            "counter_output",
            "gate_receipts",
            "nsga2",
            "objective_metrics",
            "population_closed",
            "population_manifest",
            "repetitions",
        },
        path,
    )
    if body.get("kind") != "benchmark":
        raise CapsuleValidationError("optibench result requires body.kind benchmark")
    if value.get("population_closed") is not True:
        raise CapsuleValidationError("optibench population must be closed before ranking")
    candidate = _capsule_id(value.get("candidate_capsule"), f"{path}.candidate_capsule")
    if candidate not in body.get("parents", []):
        raise CapsuleValidationError("optibench candidate must be a population parent")
    if not body.get("parents"):
        raise CapsuleValidationError("optibench requires a non-empty closed population")
    _match_evidence_ref(value.get("population_manifest"), evidence, f"{path}.population_manifest")
    _match_evidence_ref(value.get("counter_output"), evidence, f"{path}.counter_output")

    cohort = _object(value.get("cohort"), f"{path}.cohort")
    _exact_keys(
        cohort,
        {"counter_profile", "environment", "harness", "tool", "tool_version"},
        f"{path}.cohort",
    )
    profile = cohort.get("counter_profile")
    if profile == "perf_stat_instructions_v1":
        expected_objectives = PERF_OBJECTIVES
        expected_tool = "perf"
    elif profile == "callgrind_ir_v1":
        expected_objectives = CALLGRIND_OBJECTIVES
        expected_tool = "valgrind"
    else:
        raise CapsuleValidationError("optibench counter_profile is unsupported")
    if cohort.get("tool") != expected_tool:
        raise CapsuleValidationError("optibench counter_profile and tool disagree")
    _identifier(cohort.get("tool_version"), f"{path}.cohort.tool_version")
    for field in ("environment", "harness"):
        _match_evidence_ref(cohort.get(field), evidence, f"{path}.cohort.{field}")

    gate_receipts = _array(value.get("gate_receipts"), f"{path}.gate_receipts", maximum=32)
    if not gate_receipts:
        raise CapsuleValidationError("optibench requires at least one gate receipt")
    gate_order: list[tuple[str, str]] = []
    for index, receipt in enumerate(gate_receipts):
        descriptor = _match_evidence_ref(
            receipt, evidence, f"{path}.gate_receipts[{index}]"
        )
        gate_order.append((descriptor["name"], descriptor["sha256"]))
    _sorted_unique(gate_order, f"{path}.gate_receipts")

    names = value.get("objective_metrics")
    expected_names = [name for name, _unit in expected_objectives]
    if names != expected_names:
        raise CapsuleValidationError(
            "optibench objective_metrics must exactly match its counter cohort"
        )
    metrics = body.get("metrics")
    if type(metrics) is not list:
        raise CapsuleValidationError("optibench requires body.metrics")
    actual_metrics: list[tuple[str, str]] = []
    for metric in metrics:
        actual_metrics.append((metric["name"], metric["unit"]))
        if (
            len(metric["value"]) > MAX_OBJECTIVE_DIGITS
            or not _NONNEGATIVE_INTEGER_RE.fullmatch(metric["value"])
        ):
            raise CapsuleValidationError(
                "optibench objective values must be nonnegative integer strings"
            )
    if tuple(actual_metrics) != expected_objectives:
        raise CapsuleValidationError(
            "optibench body.metrics must exactly match its counter cohort"
        )

    if value.get("aggregation") != "median":
        raise CapsuleValidationError("optibench aggregation must be median")
    repetitions = _positive_safe_int(value.get("repetitions"), f"{path}.repetitions")
    if repetitions % 2 == 0:
        raise CapsuleValidationError("optibench repetitions must be odd for an integer median")
    baseline_relation = value.get("baseline_relation")
    if type(baseline_relation) is not str or baseline_relation not in {
        "dominated_by_baseline",
        "dominates_baseline",
        "equal_baseline",
        "incomparable_to_baseline",
    }:
        raise CapsuleValidationError("optibench baseline_relation is unsupported")

    nsga2 = _object(value.get("nsga2"), f"{path}.nsga2")
    _exact_keys(nsga2, {"algorithm", "crowding", "pareto_rank"}, f"{path}.nsga2")
    if nsga2.get("algorithm") != "nsga2_exact_v1":
        raise CapsuleValidationError("optibench nsga2 algorithm must be nsga2_exact_v1")
    _nonnegative_safe_int(nsga2.get("pareto_rank"), f"{path}.nsga2.pareto_rank")
    _crowding(nsga2.get("crowding"), f"{path}.nsga2.crowding")


def _crowding(raw: Any, path: str) -> None:
    value = _object(raw, path)
    kind = value.get("kind")
    if kind == "boundary":
        _exact_keys(value, {"kind"}, path)
        return
    if kind != "finite":
        raise CapsuleValidationError(f"{path}.kind must be boundary or finite")
    _exact_keys(value, {"denominator", "kind", "numerator"}, path)
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        type(numerator) is not str
        or len(numerator) > MAX_RATIONAL_DIGITS
        or not _NONNEGATIVE_INTEGER_RE.fullmatch(numerator)
    ):
        raise CapsuleValidationError(f"{path}.numerator must be a nonnegative integer string")
    if (
        type(denominator) is not str
        or len(denominator) > MAX_RATIONAL_DIGITS
        or not _POSITIVE_INTEGER_RE.fullmatch(denominator)
    ):
        raise CapsuleValidationError(f"{path}.denominator must be a positive integer string")
    if math.gcd(int(numerator), int(denominator)) != 1:
        raise CapsuleValidationError(f"{path} finite rational must be reduced")


def _validate_dpo(
    body: Mapping[str, Any], data: Any, evidence: Mapping[str, Mapping[str, Any]]
) -> None:
    path = "$.body.payload.data"
    value = _object(data, path)
    _exact_keys(
        value,
        {
            "chosen",
            "cohort_proof_manifest",
            "export_profile",
            "population_manifest",
            "preference",
            "prompt",
            "rejected",
            "task_capsule",
        },
        path,
    )
    if body.get("kind") != "evaluation":
        raise CapsuleValidationError("agentic DPO pair requires body.kind evaluation")
    if body.get("metrics"):
        raise CapsuleValidationError("agentic DPO pair does not duplicate benchmark metrics")
    task = _capsule_id(value.get("task_capsule"), f"{path}.task_capsule")
    _match_evidence_ref(
        value.get("cohort_proof_manifest"),
        evidence,
        f"{path}.cohort_proof_manifest",
    )
    _match_evidence_ref(value.get("population_manifest"), evidence, f"{path}.population_manifest")
    _match_evidence_ref(value.get("prompt"), evidence, f"{path}.prompt")

    sides: dict[str, Mapping[str, Any]] = {}
    expected_parents = {task}
    for side in ("chosen", "rejected"):
        item_path = f"{path}.{side}"
        item = _object(value.get(side), item_path)
        _exact_keys(item, {"artifact", "benchmark_capsule", "candidate_capsule"}, item_path)
        candidate = _capsule_id(item.get("candidate_capsule"), f"{item_path}.candidate_capsule")
        benchmark = _capsule_id(item.get("benchmark_capsule"), f"{item_path}.benchmark_capsule")
        _match_evidence_ref(item.get("artifact"), evidence, f"{item_path}.artifact")
        expected_parents.update((candidate, benchmark))
        sides[side] = item
    if len(expected_parents) != 5 or body.get("parents") != sorted(expected_parents):
        raise CapsuleValidationError(
            "agentic DPO parents must be the task and distinct chosen/rejected candidate and benchmark capsules"
        )
    if sides["chosen"]["artifact"]["sha256"] == sides["rejected"]["artifact"]["sha256"]:
        raise CapsuleValidationError("agentic DPO chosen and rejected artifacts must differ")

    preference = _object(value.get("preference"), f"{path}.preference")
    _exact_keys(
        preference,
        {"basis", "dominance_receipt", "policy"},
        f"{path}.preference",
    )
    if preference.get("basis") != "pareto_dominance":
        raise CapsuleValidationError("agentic DPO v1 only supports direct Pareto dominance")
    if preference.get("policy") != "nsga2_direct_dominance_v1":
        raise CapsuleValidationError("agentic DPO preference policy is unsupported")
    _match_evidence_ref(
        preference.get("dominance_receipt"), evidence, f"{path}.preference.dominance_receipt"
    )
    if value.get("export_profile") != "preference_jsonl_v1":
        raise CapsuleValidationError("agentic DPO export_profile is unsupported")


def _evidence_index(raw: Any) -> dict[str, Mapping[str, Any]]:
    if type(raw) is not list:
        raise CapsuleValidationError("$.body.evidence must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw):
        if type(item) is not dict or type(item.get("name")) is not str:
            raise CapsuleValidationError(f"$.body.evidence[{index}] is malformed")
        name = _identifier(item["name"], f"$.body.evidence[{index}].name")
        if name in result:
            raise CapsuleValidationError(
                "known payload profiles require unique evidence names"
            )
        result[name] = item
    return result


def _artifact_refs(
    raw: Any,
    evidence: Mapping[str, Mapping[str, Any]],
    path: str,
    *,
    allowed_roles: set[str],
) -> list[Mapping[str, Any]]:
    values = _array(raw, path, maximum=64)
    if not values:
        raise CapsuleValidationError(f"{path} must not be empty")
    order: list[tuple[str, str, str]] = []
    result: list[Mapping[str, Any]] = []
    for index, raw_item in enumerate(values):
        item_path = f"{path}[{index}]"
        item = _object(raw_item, item_path)
        _exact_keys(item, {"evidence_name", "role", "sha256"}, item_path)
        role = item.get("role")
        if type(role) is not str or role not in allowed_roles:
            raise CapsuleValidationError(f"{item_path}.role is unsupported")
        descriptor = _match_evidence_ref(item, evidence, item_path, allow_role=True)
        order.append((item["role"], descriptor["name"], descriptor["sha256"]))
        result.append(item)
    _sorted_unique(order, path)
    return result


def _match_evidence_ref(
    raw: Any,
    evidence: Mapping[str, Mapping[str, Any]],
    path: str,
    *,
    allow_role: bool = False,
) -> Mapping[str, Any]:
    ref = _object(raw, path)
    expected = {"evidence_name", "sha256"} | ({"role"} if allow_role else set())
    _exact_keys(ref, expected, path)
    name = _identifier(ref.get("evidence_name"), f"{path}.evidence_name")
    digest = ref.get("sha256")
    if type(digest) is not str or not _DIGEST_RE.fullmatch(digest):
        raise CapsuleValidationError(f"{path}.sha256 is invalid")
    descriptor = evidence.get(name)
    if descriptor is None or descriptor.get("sha256") != digest:
        raise CapsuleValidationError(f"{path} does not match a body.evidence descriptor")
    return descriptor


def _verified_evidence_bytes(
    ref: Any,
    evidence: Mapping[str, Mapping[str, Any]],
    blobs: Mapping[str, bytes],
    path: str,
    *,
    allow_role: bool = False,
) -> bytes:
    descriptor = _match_evidence_ref(
        ref, evidence, path, allow_role=allow_role
    )
    name = descriptor["name"]
    raw = blobs.get(name)
    if type(raw) is not bytes:
        raise CapsuleValidationError(f"{path} is missing evidence bytes")
    if len(raw) != descriptor["bytes"]:
        raise CapsuleValidationError(f"{path} evidence byte count does not match")
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise CapsuleValidationError(f"{path} evidence SHA-256 does not match")
    return raw


def _dominates(left: Sequence[int], right: Sequence[int]) -> bool:
    return all(a <= b for a, b in zip(left, right)) and any(
        a < b for a, b in zip(left, right)
    )


def _validate_closed_graph(bodies: Mapping[str, Mapping[str, Any]]) -> None:
    state: dict[str, int] = {}

    def visit(identifier: str, depth: int) -> None:
        if depth > MAX_GRAPH_BODIES:
            raise CapsuleValidationError("preference graph exceeds the v1 depth limit")
        current = state.get(identifier, 0)
        if current == 1:
            raise CapsuleValidationError("preference graph contains a parent cycle")
        if current == 2:
            return
        state[identifier] = 1
        for parent_id in bodies[identifier]["parents"]:
            if parent_id not in bodies:
                raise CapsuleValidationError(
                    "preference graph is not closed over every parent"
                )
            visit(parent_id, depth + 1)
        state[identifier] = 2

    for identifier in sorted(bodies):
        visit(identifier, 1)


def _nondominated_ranks(points: Mapping[str, tuple[int, ...]]) -> dict[str, int]:
    remaining = set(points)
    ranks: dict[str, int] = {}
    rank = 0
    while remaining:
        front = sorted(
            candidate
            for candidate in remaining
            if not any(
                other != candidate
                and _dominates(points[other], points[candidate])
                for other in remaining
            )
        )
        if not front:
            raise CapsuleValidationError("closed cohort has no nondominated front")
        for candidate in front:
            ranks[candidate] = rank
            remaining.remove(candidate)
        rank += 1
    return ranks


def _exact_crowding(
    points: Mapping[str, tuple[int, ...]], ranks: Mapping[str, int]
) -> dict[str, dict[str, str]]:
    """Return deterministic value-symmetric NSGA-II crowding receipts."""

    result: dict[str, dict[str, str]] = {}
    for rank in sorted(set(ranks.values())):
        front = sorted(candidate for candidate, value in ranks.items() if value == rank)
        if not front:
            raise CapsuleValidationError("closed cohort has an empty Pareto front")
        if len(front) == 1:
            result[front[0]] = {"kind": "boundary"}
            continue

        distances = {candidate: Fraction(0, 1) for candidate in front}
        boundary: set[str] = set()
        objective_count = len(points[front[0]])
        for objective in range(objective_count):
            groups: dict[int, list[str]] = {}
            for candidate in front:
                groups.setdefault(points[candidate][objective], []).append(candidate)
            ordered_values = sorted(groups)
            low = ordered_values[0]
            high = ordered_values[-1]
            if low == high:
                continue
            boundary.update(groups[low])
            boundary.update(groups[high])
            span = high - low
            for index, value in enumerate(ordered_values[1:-1], start=1):
                contribution = Fraction(
                    ordered_values[index + 1] - ordered_values[index - 1], span
                )
                for candidate in groups[value]:
                    distances[candidate] += contribution

        for candidate in front:
            if candidate in boundary:
                result[candidate] = {"kind": "boundary"}
                continue
            distance = distances[candidate]
            numerator = str(distance.numerator)
            denominator = str(distance.denominator)
            if (
                len(numerator) > MAX_RATIONAL_DIGITS
                or len(denominator) > MAX_RATIONAL_DIGITS
            ):
                raise CapsuleValidationError(
                    "computed crowding exceeds the v1 rational digit bound"
                )
            result[candidate] = {
                "denominator": denominator,
                "kind": "finite",
                "numerator": numerator,
            }
    return result


def _contains_secret_like(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _SHARED_SECRET_PATTERNS)


def _reject_secret_strings(raw: Any, path: str) -> None:
    if type(raw) is str:
        if _contains_secret_like(raw):
            raise CapsuleValidationError(f"{path} contains secret-like content")
        return
    if type(raw) is dict:
        for key, value in raw.items():
            _reject_secret_strings(key, f"{path}.<key>")
            _reject_secret_strings(value, f"{path}.{key}")
        return
    if type(raw) is list:
        for index, value in enumerate(raw):
            _reject_secret_strings(value, f"{path}[{index}]")


def _object(raw: Any, path: str) -> Mapping[str, Any]:
    if type(raw) is not dict:
        raise CapsuleValidationError(f"{path} must be an object")
    return raw


def _array(raw: Any, path: str, *, maximum: int) -> list[Any]:
    if type(raw) is not list:
        raise CapsuleValidationError(f"{path} must be an array")
    if len(raw) > maximum:
        raise CapsuleValidationError(f"{path} exceeds its maximum of {maximum} items")
    return raw


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise CapsuleValidationError(
            f"{path} fields do not match profile; missing={missing}, extra={extra}"
        )


def _identifier(raw: Any, path: str) -> str:
    if type(raw) is not str or not _IDENTIFIER_RE.fullmatch(raw):
        raise CapsuleValidationError(f"{path} is not a valid identifier")
    if _contains_secret_like(raw):
        raise CapsuleValidationError(f"{path} contains secret-like content")
    return raw


def _capsule_id(raw: Any, path: str) -> str:
    if type(raw) is not str or not _CAPSULE_ID_RE.fullmatch(raw):
        raise CapsuleValidationError(f"{path} is not a valid capsule id")
    return raw


def _positive_safe_int(raw: Any, path: str) -> int:
    if type(raw) is not int or not 1 <= raw <= MAX_SAFE_INTEGER:
        raise CapsuleValidationError(f"{path} must be a positive safe integer")
    return raw


def _nonnegative_safe_int(raw: Any, path: str) -> int:
    if type(raw) is not int or not 0 <= raw <= MAX_SAFE_INTEGER:
        raise CapsuleValidationError(f"{path} must be a nonnegative safe integer")
    return raw


def _sorted_unique(values: list[Any], path: str) -> None:
    if values != sorted(values) or len(values) != len(set(values)):
        raise CapsuleValidationError(f"{path} must be unique and canonically sorted")
