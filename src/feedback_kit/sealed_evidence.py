"""Whitelist-only aggregate exports for private authoritative experiments."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .experiment import json_hash


OUTPUT_FILES = {
    "development_baseline": "baseline-outputs.jsonl",
    "holdout_control": "control-outputs.jsonl",
    "holdout_candidate": "candidate-outputs.jsonl",
}
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def build_sealed_evidence(
    report: Mapping[str, Any],
    *,
    benchmark_id: str,
    source_report_sha256: str,
    response_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a content-free aggregate after validating authoritative evidence."""

    experiment = _mapping(report, "experiment")
    comparison = _mapping(report, "comparison")
    integrity = _mapping(report, "integrity")
    policy = _mapping(report, "policy_execution")
    effect = _mapping(report, "effect_gate")
    health = _mapping(report, "health")
    sample_gate = _mapping(report, "sample_gate")

    if experiment.get("evidence_level") != "orchestrated_holdout":
        raise ValueError("sealed export requires orchestrated_holdout evidence")
    if experiment.get("holdout_mode") is not True:
        raise ValueError("sealed export requires holdout_mode=true")
    if experiment.get("task_id_overlap") != []:
        raise ValueError("sealed export requires zero development/holdout task overlap")
    if comparison.get("scope") != "holdout":
        raise ValueError("sealed export requires a holdout-scoped comparison")
    if effect.get("task_match") is not True:
        raise ValueError("sealed export requires matched paired tasks")
    if not integrity or not all(
        isinstance(block, Mapping) and block.get("valid") is True
        for block in integrity.values()
    ):
        raise ValueError("sealed export requires every evidence chain to verify")
    if policy.get("action") not in {"keep_candidate", "restore_baseline"}:
        raise ValueError("sealed export requires a completed keep or restore action")

    receipt = policy.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("sealed export requires a policy receipt")

    sealed = {
        "schema_version": 1,
        "benchmark_id": str(benchmark_id),
        "visibility": {
            "level": "sealed_private_evalset",
            "raw_sources": "withheld",
            "evalsets_and_labels": "withheld",
            "configs_and_prompts": "withheld",
            "task_outputs_and_notes": "withheld",
            "event_logs": "withheld",
            "public_reproducibility": False,
        },
        "source_report_sha256": source_report_sha256,
        "experiment": {
            "evidence_level": experiment["evidence_level"],
            "manifest_hash": experiment.get("manifest_hash"),
            "baseline_evalset_hash": experiment.get("baseline_evalset_hash"),
            "confirmation_evalset_hash": experiment.get("confirmation_evalset_hash"),
            "baseline_config_hash": experiment.get("baseline_config_hash"),
            "candidate_config_hash": experiment.get("candidate_config_hash"),
            "baseline_outputs_hash": experiment.get("baseline_outputs_hash"),
            "control_outputs_hash": experiment.get("control_outputs_hash"),
            "candidate_outputs_hash": experiment.get("candidate_outputs_hash"),
            "comparison_schedule_hash": experiment.get("comparison_schedule_hash"),
            "baseline_task_count": experiment.get("baseline_task_count"),
            "confirmation_task_count": experiment.get("confirmation_task_count"),
            "task_id_overlap_count": 0,
            "runner": experiment.get("runner"),
            "config_adapter": experiment.get("config_adapter"),
        },
        "sample_gate": _pick(sample_gate, "passed", "baseline_n", "candidate_n", "minimum_per_phase"),
        "effect_gate": _pick(
            effect,
            "passed",
            "task_match",
            "paired_n",
            "actual_delta",
            "improvements",
            "regressions",
            "exact_p_value",
            "confidence_interval",
            "minimum_samples",
            "minimum_absolute_delta",
            "alpha",
        ),
        "comparison": {
            **_pick(
                comparison,
                "mode",
                "scope",
                "schedule_hash",
                "measured_order_counts",
                "records_registered",
                "records_resolved",
                "control_metric",
                "candidate_metric",
            ),
            "schedule_withheld": True,
        },
        "health": _pick(health, "healthy", "recommend_rollback", "scope", "signals"),
        "decision": {
            "action": report.get("summary", {}).get("decision")
            if isinstance(report.get("summary"), Mapping)
            else None,
            "config_action": policy.get("action"),
            "learning_action": policy.get("learning_action"),
            "receipt": _pick(
                receipt,
                "action",
                "adapter",
                "before_hash",
                "after_hash",
            ),
        },
        "integrity": {
            name: _pick(block, "valid", "algorithm", "events_verified", "head")
            for name, block in integrity.items()
        },
        "response_metadata": dict(response_metadata),
        "limitations": [
            "Raw reports, labels, configs, outputs, and event logs are withheld.",
            "This aggregate cannot be independently reproduced from the public package alone.",
            "One private holdout does not establish production generalization or eliminate service drift.",
            "The benchmark measures agreement with completed human review decisions, not company-fact correctness.",
        ],
    }
    return sealed


def aggregate_response_metadata(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    rows_by_phase: dict[str, list[dict[str, Any]]] = {}
    for phase, filename in OUTPUT_FILES.items():
        path = root / filename
        if not path.is_file():
            raise ValueError(f"missing authoritative output file: {filename}")
        rows_by_phase[phase] = _read_jsonl(path)

    all_rows = [row for rows in rows_by_phase.values() for row in rows]
    response_ids: list[str] = []
    requested_models: set[str] = set()
    response_models: set[str] = set()
    stop_reasons: set[str] = set()
    usage = {field: 0 for field in USAGE_FIELDS}
    duration_ms = 0.0

    for row in all_rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("output row missing response metadata")
        response_id = metadata.get("response_id")
        if response_id:
            response_ids.append(str(response_id))
        for key, target in (
            ("requested_model", requested_models),
            ("response_model", response_models),
            ("stop_reason", stop_reasons),
        ):
            value = metadata.get(key)
            if value is not None:
                target.add(str(value))
        raw_usage = metadata.get("usage")
        if isinstance(raw_usage, Mapping):
            for field in USAGE_FIELDS:
                value = raw_usage.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[field] += int(value)
        value = metadata.get("duration_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            duration_ms += float(value)

    return {
        "responses": len(all_rows),
        "responses_by_phase": {
            phase: len(rows) for phase, rows in rows_by_phase.items()
        },
        "response_id_coverage": len(response_ids),
        "unique_response_ids": len(set(response_ids)),
        "requested_models": sorted(requested_models),
        "response_models": sorted(response_models),
        "stop_reasons": sorted(stop_reasons),
        "usage": usage,
        "summed_call_duration_ms": round(duration_ms, 3),
    }


def sealed_evidence_hash(value: Mapping[str, Any]) -> str:
    return json_hash(value)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"report missing object {key!r}")
    return value


def _pick(source: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source.get(key) for key in keys}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {line_number} must be an object")
        rows.append(value)
    return rows
