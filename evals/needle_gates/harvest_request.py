"""Typed ``next_harvest_request`` builder — a request, never a trigger.

Evaluation (E000n) produces this document to *describe* what the next
harvesting generation (H000n+1) should target: confusion cells that stayed
weak, cells that must be retained, and the exact digests of the evidence
that justified each entry. Emitting it starts nothing: it authorizes no
generation, no provider call, and no training. A separate Codex-approved
harvesting manifest is required before any harvester may act on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.needle_gates.validators import validate_arm_metrics

HARVEST_REQUEST_SCHEMA = "needle-next-harvest-request/v1"
DEFAULT_WEAK_RECALL_THRESHOLD = 0.5


def build_next_harvest_request(
    packet_root: Path,
    *,
    campaign_id: str,
    source_dataset_id: str,
    target_dataset_id: str,
    weak_recall_threshold: float = DEFAULT_WEAK_RECALL_THRESHOLD,
    arm_metrics_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the typed harvest request from verified arm metrics.

    ``arm_metrics_payload`` may be supplied when the caller already holds a
    verified arm-metrics payload; otherwise the metrics validator runs here.
    The result is deterministic for identical inputs.
    """
    if source_dataset_id == target_dataset_id:
        raise ValueError(
            "target dataset must differ from source dataset "
            "(harvesting never modifies the dataset being trained)"
        )
    metrics = arm_metrics_payload or validate_arm_metrics(Path(packet_root))

    weak_cells: list[dict[str, Any]] = []
    retention_cells: list[dict[str, Any]] = []
    for arm in metrics.get("arms", []):
        name = str(arm.get("results_name", ""))
        for cell, recall in sorted((arm.get("class_recalls") or {}).items()):
            recall = float(recall)
            entry = {
                "arm": name,
                "cell": cell,
                "recall": recall,
            }
            if recall < weak_recall_threshold:
                weak_cells.append(entry)
            else:
                retention_cells.append(entry)
        retention = str(arm.get("retention", ""))
        if retention:
            retention_cells.append(
                {"arm": name, "cell": "retention_probe", "status": retention}
            )

    weak_cells.sort(key=lambda c: (c["cell"], c["arm"]))
    retention_cells.sort(key=lambda c: (c["cell"], c["arm"]))

    return {
        "schema": HARVEST_REQUEST_SCHEMA,
        "campaign_id": campaign_id,
        "source_dataset_id": source_dataset_id,
        "target_dataset_id": target_dataset_id,
        "weak_recall_threshold": weak_recall_threshold,
        "weak_confusion_cells": weak_cells,
        "retention_cells": retention_cells,
        "evidence_digests": dict(sorted(metrics.get("artifact_digests", {}).items())),
        "request_only": True,
        "authorizes_generation": False,
        "authorizes_training": False,
        "requires": "Codex-approved harvesting manifest before any generation",
    }
