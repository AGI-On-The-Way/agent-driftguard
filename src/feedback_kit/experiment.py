"""Authoritative execution for evidence-gated agent changes.

Unlike post-hoc rollout analysis, this module owns the experiment lifecycle.
It runs the baseline, locks a manifest, applies the candidate config, starts
candidate processes, evaluates paired outcomes, and executes the final
keep/restore policy through injected adapters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .evalset import build_outcome_rows
from .eventlog import ChainedEventLog
from .ledger import Ledger
from .proposals import ProposalLog
from .rollout import RolloutPaths, RunOutcomeAdapter, decide, evaluate_rollout
from .verdict import review_pending


@dataclass(frozen=True)
class ExperimentPaths:
    experiment_log: Path
    ledger: Path
    comparison_ledger: Path
    proposal_log: Path
    baseline_outputs: Path
    control_outputs: Path
    candidate_outputs: Path
    report: Path
    decision: Path
    dashboard_data: Path

    @classmethod
    def for_dir(cls, out_dir: str | Path) -> "ExperimentPaths":
        root = Path(out_dir)
        return cls(
            experiment_log=root / "experiment-log.jsonl",
            ledger=root / "ledger.jsonl",
            comparison_ledger=root / "comparison-ledger.jsonl",
            proposal_log=root / "proposal-log.jsonl",
            baseline_outputs=root / "baseline-outputs.jsonl",
            control_outputs=root / "control-outputs.jsonl",
            candidate_outputs=root / "candidate-outputs.jsonl",
            report=root / "drift-report.json",
            decision=root / "decision.md",
            dashboard_data=root / "dashboard-data.js",
        )

    def all_paths(self) -> list[Path]:
        return [
            self.experiment_log,
            self.ledger,
            self.comparison_ledger,
            self.proposal_log,
            self.baseline_outputs,
            self.control_outputs,
            self.candidate_outputs,
            self.report,
            self.decision,
            self.dashboard_data,
        ]


@dataclass(frozen=True)
class ExperimentPolicy:
    min_metric_samples: int = 20
    min_development_samples: int | None = None
    min_absolute_delta: float = 0.05
    alpha: float = 0.05
    bootstrap_samples: int = 2000
    anchor_kind: str | None = "anchor_task"
    health_window: int | None = None
    hit_drop_tol: float = 0.1
    drift_gap_tol: float = 0.05
    comparison_mode: str = "interleaved_control"

    def validate(self) -> None:
        if self.min_metric_samples < 1:
            raise ValueError("min_metric_samples must be at least 1")
        if self.min_development_samples is not None and self.min_development_samples < 1:
            raise ValueError("min_development_samples must be at least 1")
        if self.min_absolute_delta < 0.0 or self.min_absolute_delta > 1.0:
            raise ValueError("min_absolute_delta must be in [0, 1]")
        if self.alpha <= 0.0 or self.alpha > 1.0:
            raise ValueError("alpha must be in (0, 1]")
        if self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        if self.comparison_mode not in {"sequential", "interleaved_control"}:
            raise ValueError(
                "comparison_mode must be 'sequential' or 'interleaved_control'"
            )


@runtime_checkable
class AgentRunner(Protocol):
    name: str

    def run(
        self,
        task: Mapping[str, Any],
        *,
        phase: str,
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return at least `output`; optional fields are `prob`, `note`, and `metadata`."""


@runtime_checkable
class ConfigAdapter(Protocol):
    name: str

    def snapshot(self) -> Mapping[str, Any]:
        ...

    def apply(self, change: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def restore(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class JsonFileConfigAdapter:
    """Atomic adapter for a JSON object used as durable agent config."""

    name = "json-file-config"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def snapshot(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"config is not valid JSON: {self.path}") from exc
        if not isinstance(value, dict):
            raise TypeError("JSON config must contain an object")
        return value

    def apply(self, change: Mapping[str, Any]) -> dict[str, Any]:
        before = self.snapshot()
        after = _merge_patch(before, dict(change))
        self._write(after)
        return self._receipt("apply", before, self.snapshot())

    def restore(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        before = self.snapshot()
        after = copy.deepcopy(dict(snapshot))
        self._write(after)
        return self._receipt("restore", before, self.snapshot())

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _receipt(
        self,
        action: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "action": action,
            "adapter": self.name,
            "path": str(self.path),
            "before_hash": json_hash(before),
            "after_hash": json_hash(after),
            "ts": time.time(),
        }


class CommandAgentRunner:
    """Run one external process per task using a JSON stdin/stdout contract.

    The process receives only the task id, kind, input, optional runner metadata,
    phase, and active config. Eval checks and expected values stay inside
    DriftGuard and are never sent to the process.
    """

    name = "command-agent-runner"

    def __init__(self, command: Sequence[str] | str, *, timeout_seconds: float = 120.0):
        argv = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
        if not argv:
            raise ValueError("runner command must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("runner timeout must be positive")
        self.command = argv
        self.timeout_seconds = float(timeout_seconds)

    def run(
        self,
        task: Mapping[str, Any],
        *,
        phase: str,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        public_task = {
            "task_id": str(task["task_id"]),
            "kind": str(task["kind"]),
            "input": copy.deepcopy(task.get("input")),
        }
        if "runner_metadata" in task:
            public_task["metadata"] = copy.deepcopy(task["runner_metadata"])
        request = {
            "task": public_task,
            "phase": phase,
            "config": copy.deepcopy(dict(config)),
        }
        started = time.time()
        completed = subprocess.run(
            self.command,
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        finished = time.time()
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-500:] or "no stderr"
            raise RuntimeError(
                f"runner command failed for task {task['task_id']!r} "
                f"with exit {completed.returncode}: {detail}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"runner command returned invalid JSON for task {task['task_id']!r}"
            ) from exc
        if not isinstance(response, dict) or "output" not in response:
            raise ValueError(
                f"runner command response for task {task['task_id']!r} must contain 'output'"
            )
        result = dict(response)
        result["metadata"] = {
            **(dict(result.get("metadata", {})) if isinstance(result.get("metadata"), Mapping) else {}),
            "runner": self.name,
            "command_hash": json_hash(self.command),
            "started_at": started,
            "finished_at": finished,
            "duration_ms": round((finished - started) * 1000, 3),
        }
        return result


def run_experiment(
    *,
    proposal: Mapping[str, Any],
    evalset: Iterable[Mapping[str, Any]],
    holdout_evalset: Iterable[Mapping[str, Any]] | None = None,
    runner: AgentRunner,
    config: ConfigAdapter,
    paths: ExperimentPaths,
    policy: ExperimentPolicy | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Execute and gate one baseline-to-candidate agent experiment."""

    active_policy = policy or ExperimentPolicy()
    active_policy.validate()
    proposal_data = _validate_experiment_proposal(proposal)
    tasks = _normalize_experiment_evalset(evalset)
    holdout_mode = holdout_evalset is not None
    confirmation_tasks = (
        _normalize_experiment_evalset(holdout_evalset)
        if holdout_evalset is not None
        else tasks
    )
    task_id_overlap = (
        sorted(
            {str(task["task_id"]) for task in tasks}
            & {str(task["task_id"]) for task in confirmation_tasks}
        )
        if holdout_mode
        else []
    )
    if task_id_overlap:
        raise ValueError(
            "development and holdout task IDs must be disjoint: "
            f"overlap={task_id_overlap}"
        )
    _prepare_paths(paths, overwrite=overwrite)

    event_log = ChainedEventLog(paths.experiment_log)
    baseline_config = copy.deepcopy(dict(config.snapshot()))
    baseline_config_hash = json_hash(baseline_config)
    if json_hash(proposal_data["previous_config"]) != baseline_config_hash:
        raise ValueError("proposal previous_config does not match the active baseline config")

    evalset_hash = json_hash(tasks)
    confirmation_evalset_hash = json_hash(confirmation_tasks)
    event_log.append(
        {
            "ev": "baseline_started",
            "ts": time.time(),
            "runner": runner.name,
            "config_adapter": config.name,
            "evalset_hash": evalset_hash,
            "task_count": len(tasks),
        }
    )
    baseline_outputs = _run_phase(runner, tasks, phase="baseline", config=baseline_config)
    _write_jsonl(paths.baseline_outputs, baseline_outputs)
    baseline_outputs_hash = json_hash(baseline_outputs)
    event_log.append(
        {
            "ev": "baseline_completed",
            "ts": time.time(),
            "outputs_hash": baseline_outputs_hash,
            "output_count": len(baseline_outputs),
        }
    )

    baseline_rows = build_outcome_rows(
        evalset=tasks,
        outputs=baseline_outputs,
        phase="baseline",
    )
    metric_kind = _metric_kind(proposal_data["metric"])
    measured_baseline = _phase_rate(baseline_rows, metric_kind)
    if measured_baseline["hit_rate"] is None:
        raise ValueError(f"no machine-verifiable baseline rows for kind={metric_kind!r}")
    if proposal_data["baseline"] in {None, "measured"}:
        proposal_data["baseline"] = float(measured_baseline["hit_rate"])
    elif not math.isclose(
        float(measured_baseline["hit_rate"]), float(proposal_data["baseline"]), abs_tol=0.001
    ):
        raise ValueError(
            "proposal baseline does not match measured machine-verifiable baseline: "
            f"locked={proposal_data['baseline']}, measured={measured_baseline['hit_rate']}"
        )

    manifest = {
        "proposal_hash": json_hash(_public_proposal(proposal_data)),
        "evalset_hash": evalset_hash,
        "baseline_evalset_hash": evalset_hash,
        "confirmation_evalset_hash": confirmation_evalset_hash,
        "holdout_mode": holdout_mode,
        "task_id_overlap": task_id_overlap,
        "baseline_outputs_hash": baseline_outputs_hash,
        "baseline_config_hash": baseline_config_hash,
        "metric_kind": metric_kind,
        "runner": runner.name,
        "config_adapter": config.name,
    }
    manifest_hash = json_hash(manifest)
    event_log.append(
        {
            "ev": "proposal_locked",
            "ts": time.time(),
            "proposal_id": proposal_data.get("id"),
            "manifest": manifest,
            "manifest_hash": manifest_hash,
        }
    )

    comparison_schedule: list[dict[str, Any]] = []
    comparison_schedule_hash: str | None = None
    control_outputs: list[dict[str, Any]] | None = None
    control_outputs_hash: str | None = None
    apply_attempted = False
    try:
        apply_attempted = True
        apply_receipt = _normalize_receipt(
            config.apply(proposal_data["change"]),
            action="apply",
            before_hash=baseline_config_hash,
            after_hash=json_hash(config.snapshot()),
        )
        event_log.append(
            {
                "ev": "config_applied",
                "ts": time.time(),
                "receipt": apply_receipt,
            }
        )
        candidate_config = copy.deepcopy(dict(config.snapshot()))
        candidate_config_hash = json_hash(candidate_config)
        if apply_receipt["after_hash"] != candidate_config_hash:
            raise RuntimeError("config apply receipt does not match active candidate config")

        if active_policy.comparison_mode == "interleaved_control":
            comparison_schedule = _confirmation_schedule(
                confirmation_tasks,
                manifest_hash=manifest_hash,
            )
            comparison_schedule_hash = json_hash(comparison_schedule)
            event_log.append(
                {
                    "ev": "confirmation_schedule_locked",
                    "ts": time.time(),
                    "schedule": comparison_schedule,
                    "schedule_hash": comparison_schedule_hash,
                }
            )

        event_log.append(
            {
                "ev": "candidate_started",
                "ts": time.time(),
                "manifest_hash": manifest_hash,
                "candidate_config_hash": candidate_config_hash,
                "comparison_mode": active_policy.comparison_mode,
                "schedule_hash": comparison_schedule_hash,
            }
        )
        if active_policy.comparison_mode == "interleaved_control":
            control_outputs, candidate_outputs = _run_interleaved_confirmation(
                runner,
                confirmation_tasks,
                schedule=comparison_schedule,
                control_config=baseline_config,
                candidate_config=candidate_config,
            )
            _write_jsonl(paths.control_outputs, control_outputs)
            control_outputs_hash = json_hash(control_outputs)
        else:
            candidate_outputs = _run_phase(
                runner,
                confirmation_tasks,
                phase="candidate",
                config=candidate_config,
            )
        _write_jsonl(paths.candidate_outputs, candidate_outputs)
        candidate_outputs_hash = json_hash(candidate_outputs)
        event_log.append(
            {
                "ev": "candidate_completed",
                "ts": time.time(),
                "outputs_hash": candidate_outputs_hash,
                "output_count": len(candidate_outputs),
                "control_outputs_hash": control_outputs_hash,
                "control_output_count": len(control_outputs) if control_outputs is not None else 0,
            }
        )
    except Exception as exc:
        event_log.append(
            {
                "ev": "experiment_failed",
                "ts": time.time(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        if apply_attempted:
            before_restore_hash = json_hash(config.snapshot())
            raw_restore_receipt = config.restore(baseline_config)
            restore_receipt = _normalize_receipt(
                raw_restore_receipt,
                action="restore",
                before_hash=before_restore_hash,
                after_hash=json_hash(config.snapshot()),
            )
            restore_receipt["after_hash"] = json_hash(config.snapshot())
            event_log.append(
                {
                    "ev": "config_restored",
                    "ts": time.time(),
                    "receipt": restore_receipt,
                    "reason": "experiment_failed",
                }
            )
        raise

    candidate_rows = build_outcome_rows(
        evalset=confirmation_tasks,
        outputs=candidate_outputs,
        phase="candidate",
    )
    if control_outputs is not None:
        effect_baseline_rows = build_outcome_rows(
            evalset=confirmation_tasks,
            outputs=control_outputs,
            phase="control",
        )
        effect_baseline_phase = "control"
    else:
        effect_baseline_rows = baseline_rows
        effect_baseline_phase = "baseline"
    effect_gate = paired_effect_gate(
        effect_baseline_rows,
        candidate_rows,
        evalset=confirmation_tasks,
        kind=metric_kind,
        policy=active_policy,
    )

    analysis_proposal = dict(proposal_data)
    analysis_proposal["previous_config"] = {"config_hash": baseline_config_hash}
    report = evaluate_rollout(
        proposal=analysis_proposal,
        baseline_rows=baseline_rows,
        candidate_rows=candidate_rows,
        paths=RolloutPaths(
            ledger=paths.ledger,
            proposal_log=paths.proposal_log,
        ),
        scenario=str(proposal_data.get("id") or "authoritative_experiment"),
        anchor_kind=active_policy.anchor_kind,
        health_window=active_policy.health_window,
        min_metric_samples=(
            active_policy.min_development_samples
            if active_policy.min_development_samples is not None
            else active_policy.min_metric_samples
        ),
        hit_drop_tol=active_policy.hit_drop_tol,
        drift_gap_tol=active_policy.drift_gap_tol,
    )
    if control_outputs is not None:
        comparison_rows = _rows_in_confirmation_order(
            effect_baseline_rows,
            candidate_rows,
            comparison_schedule,
        )
        _append_rows_to_ledger(paths.comparison_ledger, comparison_rows)
        report["integrity"]["comparison_ledger"] = Ledger(
            paths.comparison_ledger
        ).verify_integrity()

    if holdout_mode:
        report["health"] = _comparison_health_report(
            effect_baseline_rows,
            candidate_rows,
            kind=metric_kind,
            hit_drop_tol=active_policy.hit_drop_tol,
        )
        refreshed_decision = decide(report)
        report["summary"]["decision"] = refreshed_decision["action"]
        report["decision"] = refreshed_decision

    base_action = report["summary"]["decision"]
    if base_action == "rollback_and_pause_lessons":
        final_action = base_action
    elif base_action in {"pause_for_integrity_failure", "pause_for_more_evidence"}:
        final_action = base_action
    elif not effect_gate["passed"]:
        final_action = "pause_for_unproven_increment"
    else:
        final_action = "keep_change"

    reasons = list(report["decision"]["reasons"])
    learning_action = (
        "pause_lesson_injection"
        if report["drift"].get("recommend") or not report["lessons"]["distilled"]
        else "allow_lesson_injection"
    )
    if final_action == "keep_change":
        reasons = [reason for reason in reasons if not reason.startswith("drift sensor recommends")]
    reasons.extend(effect_gate["reasons"])
    event_log.append(
        {
            "ev": "decision_made",
            "ts": time.time(),
            "action": final_action,
            "effect_gate_passed": effect_gate["passed"],
        }
    )

    if final_action == "keep_change":
        final_config_hash = json_hash(config.snapshot())
        policy_execution = {
            "action": "keep_candidate",
            "learning_action": learning_action,
            "receipt": {
                "action": "keep",
                "adapter": config.name,
                "after_hash": final_config_hash,
                "ts": time.time(),
            },
        }
        event_log.append(
            {
                "ev": "config_kept",
                "ts": time.time(),
                "config_hash": final_config_hash,
            }
        )
    else:
        before_restore_hash = json_hash(config.snapshot())
        raw_receipt = config.restore(baseline_config)
        receipt = _normalize_receipt(
            raw_receipt,
            action="restore",
            before_hash=before_restore_hash,
            after_hash=json_hash(config.snapshot()),
        )
        receipt["after_hash"] = json_hash(config.snapshot())
        if receipt["after_hash"] != baseline_config_hash:
            raise RuntimeError("config restore did not reproduce the baseline config")
        policy_execution = {
            "action": "restore_baseline",
            "learning_action": "pause_lesson_injection",
            "receipt": receipt,
        }
        event_log.append(
            {
                "ev": "config_restored",
                "ts": time.time(),
                "receipt": receipt,
                "reason": final_action,
            }
        )

    report["summary"]["decision"] = final_action
    report["decision"] = {
        "action": final_action,
        "reasons": _dedupe(reasons),
        "restored_config": None,
    }
    report["effect_gate"] = effect_gate
    report["comparison"] = {
        "mode": active_policy.comparison_mode,
        "effect_baseline_phase": effect_baseline_phase,
        "schedule": comparison_schedule,
        "schedule_hash": comparison_schedule_hash,
        "measured_order_counts": _schedule_order_counts(
            comparison_schedule,
            confirmation_tasks,
            kind=metric_kind,
        ),
        "scope": "holdout" if holdout_mode else "shared_evalset",
        "evalset_hash": confirmation_evalset_hash,
        "control_outputs_hash": control_outputs_hash,
        "records_registered": len(effect_baseline_rows) + len(candidate_rows),
        "records_resolved": len(effect_baseline_rows) + len(candidate_rows),
        "control_metric": _phase_rate(effect_baseline_rows, metric_kind),
        "candidate_metric": _phase_rate(candidate_rows, metric_kind),
    }
    report["policy_execution"] = policy_execution
    report["experiment"].update(
        {
            "evidence_level": "orchestrated_holdout" if holdout_mode else "orchestrated",
            "order": (
                [
                    "development_baseline",
                    "proposal_locked",
                    "config_applied",
                    "holdout_control_candidate",
                    "verification",
                ]
                if holdout_mode
                else [
                    "baseline",
                    "proposal_locked",
                    "config_applied",
                    "control_candidate" if control_outputs is not None else "candidate",
                    "verification",
                ]
            ),
            "limitations": [
                (
                    "one disjoint holdout does not establish production generalization or remove service drift"
                    if holdout_mode
                    else "a single interleaved pass does not remove service drift, carryover, or shared-task overfitting"
                    if active_policy.comparison_mode == "interleaved_control"
                    else "sequential pre/post execution does not remove temporal confounding for stochastic or changing services"
                ),
            ],
            "manifest_hash": manifest_hash,
            "evalset_hash": evalset_hash,
            "baseline_evalset_hash": evalset_hash,
            "confirmation_evalset_hash": confirmation_evalset_hash,
            "holdout_mode": holdout_mode,
            "task_id_overlap": task_id_overlap,
            "baseline_task_count": len(tasks),
            "confirmation_task_count": len(confirmation_tasks),
            "baseline_outputs_hash": baseline_outputs_hash,
            "control_outputs_hash": control_outputs_hash,
            "candidate_outputs_hash": candidate_outputs_hash,
            "baseline_config_hash": baseline_config_hash,
            "candidate_config_hash": candidate_config_hash,
            "runner": runner.name,
            "config_adapter": config.name,
            "comparison_mode": active_policy.comparison_mode,
            "comparison_schedule_hash": comparison_schedule_hash,
        }
    )
    report["integrity"]["experiment_log"] = event_log.verify()
    report["artifacts"] = {
        "experiment_log": str(paths.experiment_log),
        "ledger": str(paths.ledger),
        "proposal_log": str(paths.proposal_log),
        "baseline_outputs": str(paths.baseline_outputs),
        "candidate_outputs": str(paths.candidate_outputs),
        "report": str(paths.report),
        "decision": str(paths.decision),
        "dashboard_data": str(paths.dashboard_data),
    }
    if control_outputs is not None:
        report["artifacts"]["control_outputs"] = str(paths.control_outputs)
        report["artifacts"]["comparison_ledger"] = str(paths.comparison_ledger)
    _write_authoritative_artifacts(paths, report, _public_proposal(proposal_data))
    return report


def paired_effect_gate(
    baseline_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    *,
    evalset: Iterable[Mapping[str, Any]],
    kind: str,
    policy: ExperimentPolicy,
) -> dict[str, Any]:
    """Evaluate paired binary lift with an exact one-sided discordant-pair test."""

    baseline = _paired_rows(baseline_rows, kind=kind, phase="baseline")
    candidate = _paired_rows(candidate_rows, kind=kind, phase="candidate")
    baseline_ids = set(baseline)
    candidate_ids = set(candidate)
    task_match = baseline_ids == candidate_ids
    reasons: list[str] = []
    if not task_match:
        reasons.append(
            "paired cohort mismatch: "
            f"baseline_only={sorted(baseline_ids - candidate_ids)}, "
            f"candidate_only={sorted(candidate_ids - baseline_ids)}"
        )
        return {
            "passed": False,
            "task_match": False,
            "paired_n": 0,
            "actual_delta": None,
            "improvements": 0,
            "regressions": 0,
            "exact_p_value": None,
            "confidence_interval": None,
            "critical_regressions": [],
            "reasons": reasons,
        }

    task_ids = sorted(baseline_ids)
    deltas = [int(candidate[task_id]) - int(baseline[task_id]) for task_id in task_ids]
    paired_n = len(task_ids)
    improvements = sum(delta == 1 for delta in deltas)
    regressions = sum(delta == -1 for delta in deltas)
    actual_delta = sum(deltas) / paired_n if paired_n else 0.0
    exact_p_value = _exact_improvement_p_value(improvements, regressions)
    interval = _bootstrap_interval(
        deltas,
        alpha=policy.alpha,
        samples=policy.bootstrap_samples,
    )

    critical_ids = {
        str(task["task_id"])
        for task in evalset
        if task.get("critical") is True and str(task.get("kind")) == kind
    }
    critical_regressions = [
        task_id
        for task_id in task_ids
        if task_id in critical_ids and baseline[task_id] and not candidate[task_id]
    ]

    if paired_n < policy.min_metric_samples:
        reasons.append(
            f"paired sample gate failed: n={paired_n}, minimum={policy.min_metric_samples}"
        )
    if actual_delta < policy.min_absolute_delta:
        reasons.append(
            "minimum lift gate failed: "
            f"actual={actual_delta:+.3f}, minimum={policy.min_absolute_delta:+.3f}"
        )
    if exact_p_value > policy.alpha:
        reasons.append(
            "paired confidence gate failed: "
            f"p={format_p_value(exact_p_value)}, alpha={policy.alpha:.6f}"
        )
    if critical_regressions:
        reasons.append(f"critical task regressions: {', '.join(critical_regressions)}")

    passed = not reasons
    if passed:
        reasons.append(
            "paired increment verified: "
            f"n={paired_n}, delta={actual_delta:+.3f}, "
            f"p={format_p_value(exact_p_value)}"
        )
    return {
        "passed": passed,
        "task_match": task_match,
        "paired_n": paired_n,
        "actual_delta": round(actual_delta, 4),
        "improvements": improvements,
        "regressions": regressions,
        "exact_p_value": exact_p_value,
        "confidence_interval": interval,
        "critical_regressions": critical_regressions,
        "minimum_samples": policy.min_metric_samples,
        "minimum_absolute_delta": policy.min_absolute_delta,
        "alpha": policy.alpha,
        "reasons": reasons,
    }


def json_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_p_value(value: float) -> str:
    """Format exact probabilities without rounding small nonzero values to zero."""

    return f"{value:.3e}" if 0.0 < value < 0.000001 else f"{value:.6f}"


def _run_phase(
    runner: AgentRunner,
    tasks: Iterable[Mapping[str, Any]],
    *,
    phase: str,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        _run_task(runner, task, phase=phase, config=config)
        for task in tasks
    ]


def _append_rows_to_ledger(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    row_list = [dict(row) for row in rows]
    ledger = Ledger(path)
    for row in row_list:
        prediction = {"prob": row["prob"]} if row.get("prob") is not None else {}
        ledger.register(
            str(row["kind"]),
            {
                "run_id": str(row["run_id"]),
                "phase": str(row["phase"]),
                "note": str(row.get("note", "")),
                "machine_verifiable": bool(row["machine_verifiable"]),
            },
            id=str(row["run_id"]),
            **prediction,
        )
    review_pending(ledger, RunOutcomeAdapter(row_list))


def _rows_in_confirmation_order(
    control_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    schedule: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_phase_and_task = {
        (str(row["phase"]), str(row["task_id"])): dict(row)
        for row in [*control_rows, *candidate_rows]
    }
    return [
        by_phase_and_task[(str(phase), str(item["task_id"]))]
        for item in schedule
        for phase in item["order"]
    ]


def _run_interleaved_confirmation(
    runner: AgentRunner,
    tasks: Iterable[Mapping[str, Any]],
    *,
    schedule: Iterable[Mapping[str, Any]],
    control_config: Mapping[str, Any],
    candidate_config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_by_id = {str(task["task_id"]): task for task in tasks}
    outputs: dict[str, list[dict[str, Any]]] = {"control": [], "candidate": []}
    for item in schedule:
        task_id = str(item["task_id"])
        task = task_by_id[task_id]
        for phase in item["order"]:
            config = control_config if phase == "control" else candidate_config
            outputs[phase].append(_run_task(runner, task, phase=phase, config=config))
    return outputs["control"], outputs["candidate"]


def _run_task(
    runner: AgentRunner,
    task: Mapping[str, Any],
    *,
    phase: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    response = runner.run(task, phase=phase, config=config)
    if not isinstance(response, Mapping) or "output" not in response:
        raise ValueError(
            f"runner {runner.name!r} response for task {task['task_id']!r} must contain 'output'"
        )
    output = {
        "task_id": str(task["task_id"]),
        "output": copy.deepcopy(response["output"]),
    }
    for field in ("prob", "note", "metadata"):
        if field in response:
            output[field] = copy.deepcopy(response[field])
    return output


def _confirmation_schedule(
    tasks: Iterable[Mapping[str, Any]],
    *,
    manifest_hash: str,
) -> list[dict[str, Any]]:
    task_list = list(tasks)
    ordered = [str(task["task_id"]) for task in task_list]
    kind_by_id = {str(task["task_id"]): str(task["kind"]) for task in task_list}
    seed = int(
        json_hash(
            {
                "manifest_hash": manifest_hash,
                "purpose": "balanced_interleaved_confirmation",
            }
        )[:16],
        16,
    )
    random.Random(seed).shuffle(ordered)
    kind_counts: dict[str, int] = {}
    schedule: list[dict[str, Any]] = []
    for task_id in ordered:
        kind = kind_by_id[task_id]
        kind_index = kind_counts.get(kind, 0)
        kind_counts[kind] = kind_index + 1
        schedule.append(
            {
                "task_id": task_id,
                "order": ["control", "candidate"]
                if kind_index % 2 == 0
                else ["candidate", "control"],
            }
        )
    return schedule


def _schedule_order_counts(
    schedule: Iterable[Mapping[str, Any]],
    tasks: Iterable[Mapping[str, Any]],
    *,
    kind: str,
) -> dict[str, int]:
    measured_ids = {
        str(task["task_id"])
        for task in tasks
        if str(task["kind"]) == kind
    }
    first_phases = [
        str(item["order"][0])
        for item in schedule
        if str(item["task_id"]) in measured_ids
    ]
    return {
        "control_first": sum(phase == "control" for phase in first_phases),
        "candidate_first": sum(phase == "candidate" for phase in first_phases),
    }


def _normalize_experiment_evalset(
    evalset: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tasks = [copy.deepcopy(dict(task)) for task in evalset]
    if not tasks:
        raise ValueError("evalset must contain at least one task")
    seen: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        for field in ("task_id", "kind", "check", "input"):
            if field not in task:
                raise ValueError(f"evalset row {index} missing required field {field!r}")
        task_id = str(task["task_id"])
        if task_id in seen:
            raise ValueError(f"duplicate task_id {task_id!r} in evalset")
        seen.add(task_id)
        task["task_id"] = task_id
        task["kind"] = str(task["kind"])
    return tasks


def _validate_experiment_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(dict(proposal))
    for field in ("change", "metric", "predicted_delta", "baseline", "previous_config"):
        if field not in data:
            raise ValueError(f"proposal missing required field {field!r}")
    if not isinstance(data["change"], Mapping):
        raise TypeError("proposal field 'change' must be an object")
    if not isinstance(data["previous_config"], Mapping):
        raise TypeError("proposal field 'previous_config' must be an object")
    data["metric"] = str(data["metric"])
    data["predicted_delta"] = float(data["predicted_delta"])
    if data["baseline"] not in {None, "measured"}:
        data["baseline"] = float(data["baseline"])
    return data


def _public_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in proposal.items() if key != "previous_config"}


def _metric_kind(metric: str) -> str:
    if metric == "hit_rate":
        return "agent_task"
    if metric.endswith("_hit_rate") and metric != "_hit_rate":
        return metric[: -len("_hit_rate")]
    raise ValueError(
        f"unsupported metric {metric!r}; supported metrics are 'hit_rate' or '<kind>_hit_rate'"
    )


def _phase_rate(rows: Iterable[Mapping[str, Any]], kind: str) -> dict[str, Any]:
    measured = [
        row
        for row in rows
        if row["kind"] == kind and row["machine_verifiable"] and isinstance(row["actual_pass"], bool)
    ]
    hits = sum(row["actual_pass"] for row in measured)
    return {
        "hit_rate": round(hits / len(measured), 3) if measured else None,
        "hits": hits,
        "n": len(measured),
    }


def _comparison_health_report(
    control_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    hit_drop_tol: float,
) -> dict[str, Any]:
    control = _phase_rate(control_rows, kind)
    candidate = _phase_rate(candidate_rows, kind)
    prior = control["hit_rate"]
    recent = candidate["hit_rate"]
    signals: list[dict[str, Any]] = []
    recommend_rollback = False
    if prior is not None and recent is not None:
        delta = float(recent) - float(prior)
        degraded = -delta > hit_drop_tol
        signals.append(
            {
                "name": "hit_rate",
                "prior": prior,
                "recent": recent,
                "delta": round(delta, 3),
                "degraded": degraded,
            }
        )
        recommend_rollback = degraded
    signals.append({"name": "pending_ratio", "value": 0.0, "degraded": False})
    return {
        "scope": "holdout_control_candidate",
        "healthy": not any(signal["degraded"] for signal in signals),
        "recommend_rollback": recommend_rollback,
        "signals": signals,
    }


def _paired_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    phase: str,
) -> dict[str, bool]:
    paired: dict[str, bool] = {}
    for row in rows:
        if row.get("kind") != kind or not row.get("machine_verifiable"):
            continue
        if "task_id" not in row:
            raise ValueError(f"{phase} row missing task_id required for paired increment")
        task_id = str(row["task_id"])
        if task_id in paired:
            raise ValueError(f"duplicate {phase} task_id {task_id!r}")
        paired[task_id] = bool(row["actual_pass"])
    return paired


def _exact_improvement_p_value(improvements: int, regressions: int) -> float:
    discordant = improvements + regressions
    if discordant == 0 or improvements <= regressions:
        return 1.0
    numerator = sum(math.comb(discordant, count) for count in range(improvements, discordant + 1))
    return numerator / (2 ** discordant)


def _bootstrap_interval(
    deltas: list[int],
    *,
    alpha: float,
    samples: int,
) -> dict[str, Any] | None:
    if not deltas:
        return None
    seed = int(json_hash(deltas)[:16], 16)
    rng = random.Random(seed)
    n = len(deltas)
    estimates = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(samples)
    )
    lower_index = max(0, min(samples - 1, int((alpha / 2) * samples)))
    upper_index = max(0, min(samples - 1, int((1 - alpha / 2) * samples) - 1))
    return {
        "confidence": round(1.0 - alpha, 6),
        "lower": round(estimates[lower_index], 4),
        "upper": round(estimates[upper_index], 4),
        "method": "deterministic_paired_bootstrap",
        "samples": samples,
    }


def _normalize_receipt(
    receipt: Mapping[str, Any],
    *,
    action: str,
    before_hash: str,
    after_hash: str,
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(receipt))
    normalized.setdefault("action", action)
    normalized.setdefault("before_hash", before_hash)
    normalized.setdefault("after_hash", after_hash)
    normalized.setdefault("ts", time.time())
    return normalized


def _merge_patch(target: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(target))
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _prepare_paths(paths: ExperimentPaths, *, overwrite: bool) -> None:
    existing = [path for path in paths.all_paths() if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing experiment artifacts: {names}")
    for path in paths.all_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and overwrite:
            path.unlink()


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_authoritative_artifacts(
    paths: ExperimentPaths,
    report: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> None:
    paths.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reasons = "\n".join(f"- {reason}" for reason in report["decision"]["reasons"])
    receipt = json.dumps(
        report["policy_execution"]["receipt"],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    paths.decision.write_text(
        f"# {str(report['summary']['decision']).replace('_', ' ').title()}\n\n"
        f"Decision: {report['summary']['decision']}.\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Policy execution receipt:\n\n```json\n{receipt}\n```\n",
        encoding="utf-8",
    )
    analysis_ledger = list(Ledger(paths.ledger).events())
    comparison_ledger = (
        list(Ledger(paths.comparison_ledger).events())
        if paths.comparison_ledger.exists()
        else []
    )
    dashboard = {
        "report": report,
        "proposal": proposal,
        "ledger": comparison_ledger or analysis_ledger,
        "analysis_ledger": analysis_ledger,
        "proposal_events": list(ProposalLog(paths.proposal_log).events()),
        "experiment_events": list(ChainedEventLog(paths.experiment_log).events()),
        "decision_markdown": paths.decision.read_text(encoding="utf-8"),
    }
    paths.dashboard_data.write_text(
        "window.DRIFTGUARD_DATA = "
        + json.dumps(dashboard, ensure_ascii=False, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
