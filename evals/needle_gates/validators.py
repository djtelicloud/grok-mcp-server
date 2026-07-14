"""Deterministic Needle gate validators.

Every function here is mechanical: it reads committed artifacts, applies
fixed rules, and returns a typed JSON payload that includes the SHA-256 of
every input it consumed. No model output, agent prose, or wall-clock state
enters a payload — identical inputs always produce identical payloads, so
receipts are reproducible and pinnable by digest.

Gate truth is the typed ``ok``/``violations`` fields computed below.
Agents may run these validators and quote their receipts; they may not
decide the values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evals.needle_gates.receipts import seal_receipt, sha256_file

PREFLIGHT_SCHEMA = "needle-preflight/v1"
CORPUS_VETO_SCHEMA = "needle-corpus-veto/v1"
LANE_VITALS_SCHEMA = "needle-lane-vitals/v1"
ARM_RECORDS_SCHEMA = "needle-arm-records/v1"
ARM_METRICS_SCHEMA = "needle-arm-metrics/v1"

# Default pins mirror the audited PR #64 research packet. Live packets
# override these through their Codex-approved manifest, never through prose.
DEFAULT_CHECKPOINT_PREFIX = "40a32e91d1d4197b"
DEFAULT_SPLIT_POLICY_PATTERNS = (r"_per_tool_split", r"seed\s*42")
DEFAULT_ENV_PIN_PATTERNS = (
    r"(?i)python\s*[=:]?\s*\d+\.\d+",
    r"(?i)jax\s*[=:]?\s*\d+",
    r"(?i)flax\s*[=:]?\s*\d+",
)

# Committed packet metadata (never agent prose) marks a violation as "known".
_KNOWN_MARKERS = ("voided", "quarantine", "research control", "known-contaminated")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("xai-key", re.compile(r"xai-[A-Za-z0-9]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_EVAL_FILE_MARKERS = ("_sealed", "_ood")

_CLASS_KEYS = ("route", "class", "label", "category")


def _packet_manifest_path(packet_root: Path) -> Path | None:
    for candidate in (packet_root / "data" / "manifest.json", packet_root / "manifest.json"):
        if candidate.is_file():
            return candidate
    return None


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(manifest_path.read_text())
    if not isinstance(raw, list):
        raise ValueError("packet manifest must be a JSON array of file entries")
    entries = []
    for entry in raw:
        if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
            raise ValueError("manifest entry missing path/sha256")
        entries.append(entry)
    return entries


def _resolve_entry_file(packet_root: Path, entry_path: str) -> Path:
    """Manifest paths are repo-relative; resolve by basename inside the packet."""
    name = Path(entry_path).name
    for candidate in (packet_root / "data" / name, packet_root / name):
        if candidate.is_file():
            return candidate
    return packet_root / "data" / name


def _entry_is_known_quarantined(entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(entry.get(key, "")) for key in ("generator", "note", "status")
    ).lower()
    return any(marker in text for marker in _KNOWN_MARKERS)


def validate_preflight(
    packet_root: Path,
    *,
    expected_checkpoint_prefix: str = DEFAULT_CHECKPOINT_PREFIX,
    split_policy_patterns: tuple[str, ...] = DEFAULT_SPLIT_POLICY_PATTERNS,
    env_pin_patterns: tuple[str, ...] = DEFAULT_ENV_PIN_PATTERNS,
) -> dict[str, Any]:
    """Packet integrity: manifest digests, split policy, checkpoint & env pins."""
    violations: list[str] = []
    facts: dict[str, Any] = {}
    artifact_digests: dict[str, str] = {}

    packet_root = Path(packet_root)
    manifest_path = _packet_manifest_path(packet_root)
    if manifest_path is None:
        violations.append("packet manifest missing (data/manifest.json)")
        entries: list[dict[str, Any]] = []
    else:
        artifact_digests[str(manifest_path)] = sha256_file(manifest_path)
        try:
            entries = _load_manifest(manifest_path)
        except (ValueError, json.JSONDecodeError) as exc:
            violations.append(f"packet manifest unreadable: {exc}")
            entries = []

    listed_names: set[str] = set()
    for entry in sorted(entries, key=lambda e: str(e["path"])):
        file_path = _resolve_entry_file(packet_root, str(entry["path"]))
        listed_names.add(file_path.name)
        if not file_path.is_file():
            violations.append(f"manifest file missing on disk: {entry['path']}")
            continue
        actual = sha256_file(file_path)
        artifact_digests[str(file_path)] = actual
        if actual != entry["sha256"]:
            violations.append(
                f"sha256 mismatch for {entry['path']}: "
                f"manifest {entry['sha256']}, disk {actual}"
            )

    data_dir = packet_root / "data"
    if data_dir.is_dir():
        for file_path in sorted(data_dir.iterdir()):
            if file_path.name == "manifest.json" or not file_path.is_file():
                continue
            if file_path.suffix in (".jsonl", ".json") and file_path.name not in listed_names:
                violations.append(f"unlisted data file: data/{file_path.name}")

    readme_path = packet_root / "README.md"
    readme_text = readme_path.read_text() if readme_path.is_file() else ""
    if readme_path.is_file():
        artifact_digests[str(readme_path)] = sha256_file(readme_path)

    missing_split = [p for p in split_policy_patterns if not re.search(p, readme_text)]
    if missing_split:
        violations.append(
            "split policy not pinned before corpus assembly "
            f"(missing patterns: {', '.join(missing_split)})"
        )
    if expected_checkpoint_prefix and expected_checkpoint_prefix not in readme_text:
        violations.append(
            f"base checkpoint pin {expected_checkpoint_prefix} not recorded in README"
        )
    env_pins_found = [p for p in env_pin_patterns if re.search(p, readme_text)]
    facts["env_pins_found"] = len(env_pins_found)
    if not env_pins_found:
        violations.append("environment pins unrecorded (python/jax/flax)")

    return {
        "schema": PREFLIGHT_SCHEMA,
        "packet": str(packet_root),
        "ok": not violations,
        "violations": violations,
        "facts": facts,
        "artifact_digests": artifact_digests,
    }


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("query", "")), str(row.get("answers", "")))


def _row_class(row: dict[str, Any]) -> str:
    answers = row.get("answers", "")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except json.JSONDecodeError:
            return ""
    candidates = answers if isinstance(answers, list) else [answers]
    for item in candidates:
        if isinstance(item, dict):
            for key in _CLASS_KEYS:
                value = item.get(key)
                if isinstance(value, str):
                    return value
    return ""


def validate_corpus(packet_root: Path) -> dict[str, Any]:
    """Hard corpus gates: leakage, exact duplicates, balance facts, secrets.

    A violation carries the ``(known, quarantined)`` marker only when the
    committed packet manifest itself declares the file voided/quarantined —
    that marker never comes from agent prose.
    """
    violations: list[str] = []
    facts: dict[str, Any] = {"duplicates": {}, "class_balance": {}, "leakage": {}}
    artifact_digests: dict[str, str] = {}

    packet_root = Path(packet_root)
    data_dir = packet_root / "data"
    manifest_path = _packet_manifest_path(packet_root)
    entries_by_name: dict[str, dict[str, Any]] = {}
    if manifest_path is not None:
        artifact_digests[str(manifest_path)] = sha256_file(manifest_path)
        try:
            for entry in _load_manifest(manifest_path):
                entries_by_name[Path(str(entry["path"])).name] = entry
        except (ValueError, json.JSONDecodeError):
            violations.append("packet manifest unreadable during corpus veto")

    jsonl_files = sorted(data_dir.glob("*.jsonl")) if data_dir.is_dir() else []
    if not jsonl_files:
        violations.append("no corpus JSONL files found under data/")

    eval_rows: dict[tuple[str, str], str] = {}
    train_files: list[tuple[Path, list[dict[str, Any]]]] = []

    for file_path in jsonl_files:
        artifact_digests[str(file_path)] = sha256_file(file_path)
        try:
            rows = _read_jsonl_rows(file_path)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            violations.append(f"unparseable corpus file data/{file_path.name}: {exc}")
            continue

        seen: set[tuple[str, str]] = set()
        dups = 0
        for row in rows:
            identity = _row_identity(row)
            if identity in seen:
                dups += 1
            seen.add(identity)
        facts["duplicates"][file_path.name] = dups
        if dups:
            entry = entries_by_name.get(file_path.name, {})
            marker = " (known, quarantined)" if _entry_is_known_quarantined(entry) else ""
            violations.append(
                f"{dups} exact duplicates in data/{file_path.name}{marker}"
            )

        balance: dict[str, int] = {}
        for row in rows:
            label = _row_class(row)
            if label:
                balance[label] = balance.get(label, 0) + 1
        facts["class_balance"][file_path.name] = dict(sorted(balance.items()))

        if any(marker in file_path.name for marker in _EVAL_FILE_MARKERS):
            for row in rows:
                eval_rows.setdefault(_row_identity(row), file_path.name)
        else:
            train_files.append((file_path, rows))

    for file_path, rows in train_files:
        leaked = sum(1 for row in rows if _row_identity(row) in eval_rows)
        facts["leakage"][file_path.name] = leaked
        if leaked:
            entry = entries_by_name.get(file_path.name, {})
            marker = (
                " (known research control — excluded from any training corpus)"
                if _entry_is_known_quarantined(entry)
                else ""
            )
            violations.append(
                f"{leaked} eval rows leaked into data/{file_path.name}{marker}"
            )

    for file_path in jsonl_files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            violations.append(f"unreadable during secret scan: data/{file_path.name}: {exc}")
            continue
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(
                    f"secret pattern {name} found in data/{file_path.name}"
                )

    new_violations = [v for v in violations if "(known" not in v]
    return {
        "schema": CORPUS_VETO_SCHEMA,
        "packet": str(packet_root),
        "ok": not new_violations,
        "violations": violations,
        "new_violations": new_violations,
        "facts": facts,
        "artifact_digests": artifact_digests,
    }


_STEPS_PATTERNS = (
    re.compile(r"(?i)total[ _]steps[:=]?\s*(\d+)"),
    re.compile(r"(?i)\bstep[:= ]\s*(\d+)"),
)
_COMPLETED_PATTERN = re.compile(
    r"(?i)(training complete|run complete|completed|best checkpoint saved|finished)"
)
_NAN_PATTERN = re.compile(r"(?i)(?<![a-z])nan(?![a-z])")
_WALL_PATTERN = re.compile(r"(?i)wall[ _-]?(?:time|seconds)[:=]?\s*([0-9.]+)")


def validate_lane_vitals(log_path: Path, *, arm: str = "", seed: int = 0) -> dict[str, Any]:
    """F7-class vitals from a training log: steps > 0, completed, no NaN."""
    log_path = Path(log_path)
    violations: list[str] = []
    total_steps = 0
    completed = False
    nan_found = False
    wall_seconds: float | None = None
    digest = ""

    if not log_path.is_file():
        violations.append(f"training log missing: {log_path}")
    else:
        digest = sha256_file(log_path)
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for pattern in _STEPS_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                total_steps = max(int(m) for m in matches)
                break
        completed = bool(_COMPLETED_PATTERN.search(text))
        nan_found = bool(_NAN_PATTERN.search(text))
        wall_match = _WALL_PATTERN.search(text)
        if wall_match:
            wall_seconds = float(wall_match.group(1))
        if total_steps <= 0:
            violations.append("vitals: total steps not > 0")
        if not completed:
            violations.append("vitals: no completion marker in log")
        if nan_found:
            violations.append("vitals: NaN observed in log")

    return {
        "schema": LANE_VITALS_SCHEMA,
        "arm": arm,
        "seed": seed,
        "log": str(log_path),
        "log_sha256": digest,
        "ok": not violations,
        "vitals_veto": "ok" if not violations else "; ".join(violations),
        "total_steps": total_steps,
        "completed": completed,
        "nan_found": nan_found,
        "wall_seconds": wall_seconds,
        "violations": violations,
    }


def _alias_key(name: str) -> str:
    """Case/punctuation-insensitive form used only to *reject* near-duplicates."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _load_arm_records(
    packet_root: Path,
    violations: list[str],
    artifact_digests: dict[str, str],
) -> list[dict[str, Any]]:
    """Load frozen arm candidate records with exact-identity discipline.

    Each record binds an arm to its exact results identity (``results_name``,
    defaulting to the arm name) and its dataset digest (``dataset_hash``,
    required). Duplicate or aliased arm names — two records whose names
    differ only in case or punctuation — are rejected so no downstream
    consumer can ever join arms by a fuzzy or partial key.
    """
    candidates_path = packet_root / "data" / "arm_candidates.json"
    arms: list[dict[str, Any]] = []

    if not candidates_path.is_file():
        violations.append("data/arm_candidates.json missing")
        return arms
    artifact_digests[str(candidates_path)] = sha256_file(candidates_path)
    try:
        raw = json.loads(candidates_path.read_text())
    except json.JSONDecodeError as exc:
        violations.append(f"arm_candidates.json unparseable: {exc}")
        return arms
    if not isinstance(raw, list):
        violations.append("arm_candidates.json is not a JSON array")
        return arms

    seen_names: set[str] = set()
    seen_aliases: dict[str, str] = {}
    seen_results: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict) or "arm" not in entry:
            violations.append("arm candidate entry missing 'arm'")
            continue
        name = str(entry["arm"])
        if name in seen_names:
            violations.append(f"duplicate arm name in arm_candidates.json: {name}")
            continue
        alias = _alias_key(name)
        if alias in seen_aliases:
            violations.append(
                f"aliased arm names in arm_candidates.json: {name!r} collides "
                f"with {seen_aliases[alias]!r}"
            )
            continue
        dataset_hash = str(entry.get("dataset_hash", ""))
        if not dataset_hash:
            violations.append(f"arm {name}: dataset_hash missing — every arm record "
                              "must bind its exact dataset digest")
        results_name = str(entry.get("results_name", name))
        if results_name in seen_results:
            violations.append(
                f"arm {name}: results identity {results_name!r} already claimed "
                f"by arm {seen_results[results_name]!r}"
            )
            continue
        seen_names.add(name)
        seen_aliases[alias] = name
        seen_results[results_name] = name
        arms.append(
            {
                "arm": name,
                "results_name": results_name,
                "dataset_hash": dataset_hash,
                "log": str(entry.get("log", "")),
                "n": int(entry.get("n", 0)),
                "recipe": str(entry.get("recipe", "")),
                "seed": int(entry.get("seed", 0)),
            }
        )
    return arms


def validate_arm_records(packet_root: Path) -> dict[str, Any]:
    """Replay the frozen arm candidate records — never invent an arm."""
    packet_root = Path(packet_root)
    violations: list[str] = []
    artifact_digests: dict[str, str] = {}
    arms = _load_arm_records(packet_root, violations, artifact_digests)

    return {
        "schema": ARM_RECORDS_SCHEMA,
        "packet": str(packet_root),
        "ok": not violations,
        "violations": violations,
        "arms": sorted(arms, key=lambda a: a["arm"]),
        "artifact_digests": artifact_digests,
    }


_LEGACY_KEY_MAP = {
    # results file legacy key -> typed metric name
    "sealed": "dev_ood",  # adaptive OOD dev set (legacy name)
    "dev_ood": "secondary_ood",
    "forgetting": "retention",
}

# The trivial deterministic baseline every learned arm must beat, in addition
# to the A_control comparison. Dropping it silently would let a learned arm
# be promoted for merely beating an untrained control.
DEFAULT_BASELINE_NAME = "tfidf_baseline"


def _typed_metrics_row(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "results_name": name,
        "dataset_hash": str(entry.get("dataset_hash", "")),
        "in_template": float(entry.get("in_template", -1)),
        "dev_ood": float(entry.get("sealed", -1)),
        "secondary_ood": float(entry.get("dev_ood", -1)),
        "retention": str(entry.get("forgetting", "")),
        "worst_class": entry.get("worst_class", []),
        "class_recalls": dict(sorted((entry.get("sealed_recalls") or {}).items())),
    }


def _find_arm_log(logs_dir: Path, record: dict[str, Any]) -> Path | None:
    """Resolve an arm's training log by exact identity — never by a letter.

    Precedence: the record's explicit ``log`` filename, then a filename that
    contains the exact ``results_name``. Anything fuzzier (single-letter
    matching) has been removed: it silently joined unrelated artifacts.
    """
    explicit = str(record.get("log", ""))
    if explicit:
        candidate = logs_dir / Path(explicit).name
        return candidate if candidate.is_file() else None
    results_name = str(record.get("results_name", ""))
    if not logs_dir.is_dir() or not results_name:
        return None
    for candidate in sorted(logs_dir.iterdir()):
        if candidate.is_file() and results_name in candidate.name:
            return candidate
    return None


def validate_arm_metrics(
    packet_root: Path,
    *,
    control_name: str = "A_control",
    baseline_name: str = DEFAULT_BASELINE_NAME,
) -> dict[str, Any]:
    """Typed per-arm metrics bound to frozen arm records, plus lane vitals.

    Applies the audited legacy key map (results ``sealed`` -> ``dev_ood``,
    results ``dev_ood`` -> ``secondary_ood``, ``forgetting`` -> ``retention``)
    in code, so no agent has to remember or restate it.

    Binding discipline: every emitted arm row is a frozen arm-candidate
    record joined to its results row by the record's exact ``results_name``
    and cross-checked against its ``dataset_hash``. Results rows that no
    record claims (other than the control and the trivial baseline) are
    violations, as are duplicate results identities and dataset-digest
    mismatches. The trivial ``tfidf_baseline`` row is required: it is a
    deterministic gate every learned arm must beat alongside the control.
    """
    packet_root = Path(packet_root)
    results_path = packet_root / "data" / "arm_results.json"
    logs_dir = packet_root / "logs"
    violations: list[str] = []
    arms: list[dict[str, Any]] = []
    control: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    artifact_digests: dict[str, str] = {}

    records = _load_arm_records(packet_root, violations, artifact_digests)

    if not results_path.is_file():
        violations.append("data/arm_results.json missing")
        raw: list[Any] = []
    else:
        artifact_digests[str(results_path)] = sha256_file(results_path)
        try:
            raw = json.loads(results_path.read_text())
        except json.JSONDecodeError as exc:
            violations.append(f"arm_results.json unparseable: {exc}")
            raw = []
        if not isinstance(raw, list):
            violations.append("arm_results.json is not a JSON array")
            raw = []

    results_by_name: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict) or "arm" not in entry:
            violations.append("arm result entry missing 'arm'")
            continue
        name = str(entry["arm"])
        if name in results_by_name:
            violations.append(f"duplicate results identity in arm_results.json: {name}")
            continue
        results_by_name[name] = entry

    if control_name in results_by_name:
        control = _typed_metrics_row(control_name, results_by_name.pop(control_name))
    else:
        violations.append(f"control arm {control_name} missing from arm_results.json")

    if baseline_name in results_by_name:
        baseline = _typed_metrics_row(baseline_name, results_by_name.pop(baseline_name))
    else:
        violations.append(
            f"trivial baseline {baseline_name} missing from arm_results.json — "
            "learned arms must be compared against the deterministic baseline"
        )

    for record in sorted(records, key=lambda r: r["arm"]):
        results_name = record["results_name"]
        entry = results_by_name.pop(results_name, None)
        if entry is None:
            violations.append(
                f"arm {record['arm']}: no results row with exact identity "
                f"{results_name!r} — arms are never joined by partial names"
            )
            continue
        typed = _typed_metrics_row(results_name, entry)
        typed["arm"] = record["arm"]
        result_hash = typed["dataset_hash"]
        if result_hash and record["dataset_hash"] and result_hash != record["dataset_hash"]:
            violations.append(
                f"arm {record['arm']}: dataset_hash mismatch — record "
                f"{record['dataset_hash']} vs results {result_hash}"
            )
            continue
        typed["dataset_hash"] = record["dataset_hash"] or result_hash

        log_path = _find_arm_log(logs_dir, record)
        if log_path is None:
            vitals = {
                "vitals_veto": f"vitals: no training log found for arm {record['arm']}",
                "total_steps": 0,
                "completed": False,
                "nan_found": False,
                "wall_seconds": None,
                "log": "",
                "log_sha256": "",
                "ok": False,
            }
        else:
            lane = validate_lane_vitals(log_path, arm=record["arm"])
            artifact_digests[str(log_path)] = lane["log_sha256"]
            vitals = {
                "vitals_veto": lane["vitals_veto"],
                "total_steps": lane["total_steps"],
                "completed": lane["completed"],
                "nan_found": lane["nan_found"],
                "wall_seconds": lane["wall_seconds"],
                "log": lane["log"],
                "log_sha256": lane["log_sha256"],
                "ok": lane["ok"],
            }
        typed["vitals"] = vitals
        arms.append(typed)

    for orphan in sorted(results_by_name):
        violations.append(
            f"results row {orphan!r} is not bound to any frozen arm record"
        )

    return {
        "schema": ARM_METRICS_SCHEMA,
        "packet": str(packet_root),
        "ok": not violations,
        "violations": violations,
        "control": control,
        "baseline": baseline,
        "arms": sorted(arms, key=lambda a: a["results_name"]),
        "artifact_digests": artifact_digests,
    }


def build_receipt(validator: str, payload: dict[str, Any]) -> dict[str, Any]:
    return seal_receipt(validator, payload)
