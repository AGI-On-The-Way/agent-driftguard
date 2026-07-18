"""Generic rollout evaluation for evidence-gated agent self-improvement.

The Build Week demo is intentionally synthetic, but the useful module is the
rollout evaluator: feed it a locked proposal plus real baseline/candidate
outcomes, and it produces the same append-only evidence, metrics, and keep or
rollback decision. The caller owns domain-specific task design; this module
owns chronology, verification, and fail-closed gating.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .calibration import brier_score, hit_rate, reliability
from .health import drift_check, health_check
from .ledger import Ledger
from .lessons import distill
from .proposals import ProposalLog
from .verdict import Outcome, Verdict, review_pending


@dataclass(frozen=True)
class RolloutPaths:
    """Output files for a rollout evaluation.

    The evaluator writes hash-chained evidence to `ledger` and `proposal_log`.
    `report`, `decision`, and `dashboard_data` are optional derived artifacts.
    """

    ledger: Path
    proposal_log: Path
    report: Path | None = None
    decision: Path | None = None
    dashboard_data: Path | None = None

    def all_paths(self) -> list[Path]:
        return [
            path
            for path in (
                self.ledger,
                self.proposal_log,
                self.report,
                self.decision,
                self.dashboard_data,
            )
            if path is not None
        ]


class RunOutcomeAdapter:
    """Adapter for normalized run rows.

    A row is deliberately small and domain-neutral:
    `run_id`, `kind`, `phase`, `machine_verifiable`, `actual_pass`, optional
    `prob`, `miss_reason`, and `note`.
    """

    name = "run-outcome-jsonl"

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        self.rows = [dict(row) for row in rows]
        self.kinds = {str(row["kind"]) for row in self.rows}
        self.by_id = {str(row["run_id"]): dict(row) for row in self.rows}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self.by_id

    def verdict(self, record: dict[str, Any], snapshot: dict[str, dict[str, Any]]) -> Verdict:
        run = snapshot[record["payload"]["run_id"]]
        if run["actual_pass"]:
            return Verdict(
                Outcome.HIT,
                machine_verifiable=run["machine_verifiable"],
                confidence=1.0,
                detail={"note": run.get("note", "")},
            )
        return Verdict(
            Outcome.MISS,
            machine_verifiable=run["machine_verifiable"],
            confidence=1.0,
            attribution=run.get("miss_reason") or "unspecified_miss",
            attribution_machine_verifiable=run["machine_verifiable"],
            detail={"note": run.get("note", "")},
        )


def evaluate_rollout(
    *,
    proposal: Mapping[str, Any],
    baseline_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    paths: RolloutPaths,
    scenario: str = "rollout",
    agent_kind: str = "agent_task",
    anchor_kind: str | None = "anchor_task",
    lesson_kind: str | None = None,
    health_window: int | None = None,
    min_metric_samples: int = 4,
    hit_drop_tol: float = 0.1,
    drift_gap_tol: float = 0.05,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Analyze evidence for a proposed self-improvement.

    The generated ledger uses baseline evidence, locked proposal, candidate
    evidence, then verification. Because caller-supplied rows may already exist,
    this function cannot prove source chronology or execute an external config
    action. Existing output files are rejected unless the caller opts into
    `overwrite`.
    """

    proposal_data = _validate_proposal(proposal)
    baseline = _normalize_rows(baseline_rows, phase="baseline")
    candidate = _normalize_rows(candidate_rows, phase="candidate")
    _validate_unique_run_ids(baseline + candidate)
    _prepare_outputs(paths, overwrite=overwrite)

    metric_kind = _metric_kind(proposal_data["metric"], default_kind=agent_kind)
    active_lesson_kind = lesson_kind or metric_kind
    active_health_window = health_window or _infer_health_window(
        baseline,
        candidate,
        kind=metric_kind,
    )

    ledger = Ledger(paths.ledger)
    _register_runs(ledger, baseline)
    baseline_resolved = review_pending(ledger, RunOutcomeAdapter(baseline))
    baseline_metric = phase_hit_rate(ledger, "baseline", kind=metric_kind)
    measured_baseline = baseline_metric["hit_rate"]
    if measured_baseline is None:
        raise ValueError(f"no machine-verifiable baseline rows for kind={metric_kind!r}")
    if not math.isclose(measured_baseline, proposal_data["baseline"], abs_tol=0.001):
        raise ValueError(
            "proposal baseline does not match measured machine-verifiable baseline: "
            f"locked={proposal_data['baseline']}, measured={measured_baseline}"
        )

    proposals = ProposalLog(paths.proposal_log)
    pid = proposals.propose(
        id=proposal_data.get("id"),
        change=proposal_data["change"],
        metric=proposal_data["metric"],
        predicted_delta=proposal_data["predicted_delta"],
        baseline=proposal_data["baseline"],
        description=proposal_data.get("description", ""),
    )
    proposals.apply(pid, prev_config=proposal_data["previous_config"])

    _register_runs(ledger, candidate)
    candidate_resolved = review_pending(ledger, RunOutcomeAdapter(candidate))
    candidate_metric = phase_hit_rate(ledger, "candidate", kind=metric_kind)
    current_value = candidate_metric["hit_rate"]
    if current_value is None:
        raise ValueError(f"no machine-verifiable candidate rows for kind={metric_kind!r}")
    verification = proposals.verify(pid, current_value)
    sample_gate = _sample_gate(
        baseline_metric,
        candidate_metric,
        minimum_per_phase=min_metric_samples,
    )

    health = health_check(
        ledger,
        kind=metric_kind,
        window=active_health_window,
        hit_drop_tol=hit_drop_tol,
    )
    drift = _drift_report(ledger, anchor_kind=anchor_kind, gap_tol=drift_gap_tol)
    lessons = distill(ledger, kind=active_lesson_kind)

    report = {
        "summary": {
            "scenario": scenario,
            "records_registered": len(baseline) + len(candidate),
            "records_resolved": len(baseline_resolved) + len(candidate_resolved),
            "decision": None,
        },
        "experiment": {
            "scenario": scenario,
            "evidence_level": "post_hoc",
            "limitations": [
                "source output chronology is not controlled by this evaluator",
                "the evaluator does not apply or restore an external config",
            ],
            "order": ["baseline", "proposal_locked", "candidate", "verification"],
            "baseline_run_ids": [row["run_id"] for row in baseline],
            "candidate_run_ids": [row["run_id"] for row in candidate],
            "metric_kind": metric_kind,
            "anchor_kind": anchor_kind,
        },
        "proposal_verification": verification,
        "sample_gate": sample_gate,
        "metrics": {
            "agent_hit_rate": hit_rate(ledger, kind=metric_kind),
            "baseline_hit_rate": baseline_metric,
            "candidate_hit_rate": candidate_metric,
            "agent_brier": brier_score(ledger, kind=metric_kind),
            "anchor_brier": brier_score(ledger, kind=anchor_kind) if anchor_kind else None,
            "overall_brier": brier_score(ledger),
            "agent_reliability": reliability(ledger, kind=metric_kind),
        },
        "health": asdict(health),
        "drift": drift,
        "lessons": {
            "distilled": lessons,
            "gate": "blocked_below_min_samples_or_confidence" if not lessons else "passed",
            "minimum_samples": 8,
            "minimum_confidence": 0.6,
        },
        "integrity": {
            "ledger": ledger.verify_integrity(),
            "proposal_log": proposals.verify_integrity(),
        },
        "artifacts": _artifact_map(paths),
    }

    decision = decide(report)
    report["summary"]["decision"] = decision["action"]
    report["decision"] = decision
    _write_outputs(paths, report, proposal_data, ledger, proposals, build_decision(decision))
    return report


def phase_hit_rate(ledger: Ledger, phase: str, *, kind: str) -> dict[str, Any]:
    records = [
        record
        for record in ledger.records().values()
        if record["kind"] == kind
        and record["payload"].get("phase") == phase
        and record["machine_verifiable"]
        and record["outcome"] in {"hit", "miss"}
    ]
    hits = sum(record["outcome"] == "hit" for record in records)
    return {
        "hit_rate": round(hits / len(records), 3) if records else None,
        "hits": hits,
        "n": len(records),
    }


def decide(report: Mapping[str, Any]) -> dict[str, Any]:
    proposal = report["proposal_verification"]
    sample_gate = report.get("sample_gate")
    drift = report["drift"]
    health = report["health"]
    lessons = report["lessons"]
    integrity = report["integrity"]

    reasons: list[str] = []
    if not all(item["valid"] for item in integrity.values()):
        return {
            "action": "pause_for_integrity_failure",
            "reasons": ["evidence hash-chain verification failed"],
            "restored_config": None,
        }
    if sample_gate and not sample_gate["passed"]:
        return {
            "action": "pause_for_more_evidence",
            "reasons": [
                "sample gate failed: "
                f"baseline n={sample_gate['baseline_n']}, "
                f"candidate n={sample_gate['candidate_n']}, "
                f"minimum per phase={sample_gate['minimum_per_phase']}"
            ],
            "restored_config": None,
        }
    if proposal["status"] == "rolled_back":
        reasons.append(
            f"locked prediction failed: expected {proposal['predicted_delta']:+.2f}, "
            f"actual {proposal['actual_delta']:+.2f}"
        )
    elif proposal["status"] == "verified":
        reasons.append(
            f"locked prediction verified: expected {proposal['predicted_delta']:+.2f}, "
            f"actual {proposal['actual_delta']:+.2f}"
        )
    if health["recommend_rollback"]:
        reasons.append("health sensor recommends rollback")
    if drift.get("recommend"):
        reasons.append(f"drift sensor recommends {drift['recommend']}")
    if not lessons["distilled"]:
        reasons.append("lesson gate produced no lesson because sample/confidence threshold was not met")

    if proposal["status"] == "rolled_back" or health["recommend_rollback"]:
        action = "rollback_and_pause_lessons"
        restored = proposal["prev_config"]
    elif drift.get("recommend"):
        action = "pause_lesson_injection"
        restored = None
    else:
        action = "keep_change"
        restored = None
    return {"action": action, "reasons": reasons, "restored_config": restored}


def build_decision(decision: Mapping[str, Any]) -> str:
    title = str(decision["action"]).replace("_", " ").title()
    lines = [
        f"# {title}",
        "",
        f"Decision: {decision['action']}.",
        "",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in decision["reasons"])
    if decision["restored_config"] is not None:
        lines.extend(
            [
                "",
                "Restored config:",
                "",
                "```json",
                json.dumps(decision["restored_config"], ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "This decision is deterministic: it is derived from hash-chained evidence, "
            "machine-verifiable outcomes, the locked proposal prediction, and statistical gates.",
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_rows(rows: Iterable[Mapping[str, Any]], *, phase: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        item = dict(row)
        for field in ("run_id", "kind", "machine_verifiable", "actual_pass"):
            if field not in item:
                raise ValueError(f"{phase} row {idx} missing required field {field!r}")
        if "phase" in item and item["phase"] != phase:
            raise ValueError(
                f"{phase} row {idx} has contradictory phase {item['phase']!r}"
            )
        item["phase"] = phase
        item["run_id"] = str(item["run_id"])
        item["kind"] = str(item["kind"])
        if not isinstance(item["machine_verifiable"], bool):
            raise TypeError(f"{phase} row {idx} field 'machine_verifiable' must be bool")
        if not isinstance(item["actual_pass"], bool):
            raise TypeError(f"{phase} row {idx} field 'actual_pass' must be bool")
        if "prob" in item and item["prob"] is not None:
            prob = float(item["prob"])
            if prob < 0.0 or prob > 1.0:
                raise ValueError(f"{phase} row {idx} field 'prob' must be in [0, 1]")
            item["prob"] = prob
        item.setdefault("miss_reason", None)
        item.setdefault("note", "")
        normalized.append(item)
    return normalized


def _validate_unique_run_ids(rows: Iterable[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        run_id = str(row["run_id"])
        if run_id in seen:
            raise ValueError(f"duplicate run_id {run_id!r}; ledger ids must be unique")
        seen.add(run_id)


def _validate_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(proposal)
    for field in ("change", "metric", "predicted_delta", "baseline", "previous_config"):
        if field not in data:
            raise ValueError(f"proposal missing required field {field!r}")
    data["metric"] = str(data["metric"])
    data["predicted_delta"] = float(data["predicted_delta"])
    data["baseline"] = float(data["baseline"])
    return data


def _metric_kind(metric: str, *, default_kind: str) -> str:
    if metric == "hit_rate":
        return default_kind
    suffix = "_hit_rate"
    if metric.endswith(suffix):
        kind = metric[: -len(suffix)]
        if kind:
            return kind
    raise ValueError(
        f"unsupported metric {metric!r}; supported metrics are 'hit_rate' or '<kind>_hit_rate'"
    )


def _infer_health_window(
    baseline: Iterable[Mapping[str, Any]],
    candidate: Iterable[Mapping[str, Any]],
    *,
    kind: str,
) -> int:
    baseline_n = sum(1 for row in baseline if row["kind"] == kind and row["machine_verifiable"])
    candidate_n = sum(1 for row in candidate if row["kind"] == kind and row["machine_verifiable"])
    return max(1, min(baseline_n, candidate_n) or max(baseline_n, candidate_n, 1))


def _sample_gate(
    baseline_metric: Mapping[str, Any],
    candidate_metric: Mapping[str, Any],
    *,
    minimum_per_phase: int,
) -> dict[str, Any]:
    baseline_n = int(baseline_metric["n"])
    candidate_n = int(candidate_metric["n"])
    return {
        "minimum_per_phase": minimum_per_phase,
        "baseline_n": baseline_n,
        "candidate_n": candidate_n,
        "passed": baseline_n >= minimum_per_phase and candidate_n >= minimum_per_phase,
    }


def _prepare_outputs(paths: RolloutPaths, *, overwrite: bool) -> None:
    existing = [path for path in paths.all_paths() if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing rollout artifacts: {names}")
    for path in paths.all_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and overwrite:
            path.unlink()


def _register_runs(ledger: Ledger, rows: Iterable[Mapping[str, Any]]) -> None:
    for row in rows:
        pred = {}
        if row.get("prob") is not None:
            pred["prob"] = row["prob"]
        ledger.register(
            row["kind"],
            {
                "run_id": row["run_id"],
                "phase": row["phase"],
                "note": row.get("note", ""),
                "machine_verifiable": row["machine_verifiable"],
            },
            id=row["run_id"],
            **pred,
        )


def _drift_report(ledger: Ledger, *, anchor_kind: str | None, gap_tol: float) -> dict[str, Any]:
    if not anchor_kind:
        return {"status": "not_configured", "recommend": None}
    has_anchor = any(record["kind"] == anchor_kind for record in ledger.records().values())
    if not has_anchor:
        return {"status": "not_configured", "anchor_kind": anchor_kind, "recommend": None}
    return drift_check(ledger, anchor_kind=anchor_kind, gap_tol=gap_tol)


def _artifact_map(paths: RolloutPaths) -> dict[str, str]:
    out = {
        "ledger": str(paths.ledger),
        "proposal_log": str(paths.proposal_log),
    }
    if paths.report:
        out["report"] = str(paths.report)
    if paths.decision:
        out["decision"] = str(paths.decision)
    if paths.dashboard_data:
        out["dashboard_data"] = str(paths.dashboard_data)
    return out


def _write_outputs(
    paths: RolloutPaths,
    report: Mapping[str, Any],
    proposal: Mapping[str, Any],
    ledger: Ledger,
    proposals: ProposalLog,
    decision_markdown: str,
) -> None:
    if paths.report:
        paths.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if paths.decision:
        paths.decision.write_text(decision_markdown, encoding="utf-8")
    if paths.dashboard_data:
        data = {
            "report": report,
            "proposal": dict(proposal),
            "ledger": list(ledger.events()),
            "proposal_events": list(proposals.events()),
            "decision_markdown": decision_markdown,
        }
        paths.dashboard_data.write_text(
            "window.DRIFTGUARD_DATA = "
            + json.dumps(data, ensure_ascii=False, sort_keys=True)
            + ";\n",
            encoding="utf-8",
        )
