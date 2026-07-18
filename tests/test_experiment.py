"""Interface-level checks for the authoritative experiment lifecycle.

Run: python3 tests/test_experiment.py   (no pytest needed)
"""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit import (  # noqa: E402
    ExperimentPaths,
    ExperimentPolicy,
    JsonFileConfigAdapter,
    run_experiment,
)
from feedback_kit.eventlog import ChainedEventLog  # noqa: E402
from feedback_kit.experiment import paired_effect_gate  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def task_set(
    *,
    count: int,
    baseline_misses: set[int],
    candidate_misses: set[int] | None = None,
    critical: set[int] | None = None,
    prefix: str = "task",
) -> list[dict[str, Any]]:
    candidate_misses = candidate_misses or set()
    critical = critical or set()
    tasks = []
    for index in range(count):
        tasks.append(
            {
                "task_id": f"{prefix}-{index:02d}",
                "kind": "agent_task",
                "input": f"Return the expected marker for task {index}",
                "check": {"type": "equals", "value": "ok"},
                "critical": index in critical,
                "fixture_outputs": {
                    "baseline": "bad" if index in baseline_misses else "ok",
                    "candidate": "bad" if index in candidate_misses else "ok",
                },
            }
        )
    return tasks


class RecordingRunner:
    name = "recording-runner"

    def __init__(self, *, fail_candidate: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.fail_candidate = fail_candidate

    def run(
        self,
        task: Mapping[str, Any],
        *,
        phase: str,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        call = {
            "phase": phase,
            "task_id": task["task_id"],
            "config": copy.deepcopy(dict(config)),
        }
        self.calls.append(call)
        if self.fail_candidate and phase == "candidate":
            raise RuntimeError("candidate runner failed")
        mode = str(config["mode"])
        return {
            "output": task["fixture_outputs"][mode],
            "prob": 0.8,
            "note": f"{phase} output from {self.name}",
        }


class MemoryConfigAdapter:
    name = "memory-config"

    def __init__(self, initial: Mapping[str, Any]):
        self.current = copy.deepcopy(dict(initial))
        self.actions: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.current)

    def apply(self, change: Mapping[str, Any]) -> dict[str, Any]:
        self.actions.append("apply")
        before = self.snapshot()
        self.current.update(copy.deepcopy(dict(change)))
        return {"action": "apply", "before": before, "after": self.snapshot()}

    def restore(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        self.actions.append("restore")
        before = self.snapshot()
        self.current = copy.deepcopy(dict(snapshot))
        return {"action": "restore", "before": before, "after": self.snapshot()}


class MutatingFailConfigAdapter(MemoryConfigAdapter):
    def apply(self, change: Mapping[str, Any]) -> dict[str, Any]:
        self.actions.append("apply")
        self.current.update(copy.deepcopy(dict(change)))
        raise RuntimeError("apply failed after mutation")


def strict_policy(*, min_samples: int) -> ExperimentPolicy:
    return ExperimentPolicy(
        min_metric_samples=min_samples,
        min_absolute_delta=0.1,
        alpha=0.05,
        bootstrap_samples=500,
        anchor_kind=None,
        comparison_mode="sequential",
    )


def proposal(*, baseline: float, predicted_delta: float = 0.2) -> dict[str, Any]:
    return {
        "id": "prompt-candidate-v1",
        "description": "Use the candidate prompt mode.",
        "metric": "agent_task_hit_rate",
        "baseline": baseline,
        "predicted_delta": predicted_delta,
        "change": {"mode": "candidate"},
        "previous_config": {"mode": "baseline"},
    }


def test_keep_requires_locked_order_and_paired_confidence(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    report = run_experiment(
        proposal=proposal(baseline=0.375, predicted_delta=0.5),
        evalset=task_set(count=8, baseline_misses={0, 1, 2, 3, 4}),
        runner=runner,
        config=config,
        paths=ExperimentPaths.for_dir(tmp / "keep"),
        policy=strict_policy(min_samples=8),
    )

    assert report["summary"]["decision"] == "keep_change"
    assert report["effect_gate"]["paired_n"] == 8
    assert report["effect_gate"]["improvements"] == 5
    assert report["effect_gate"]["regressions"] == 0
    assert report["effect_gate"]["exact_p_value"] <= 0.05
    assert report["effect_gate"]["passed"] is True
    assert config.current == {"mode": "candidate"}
    assert config.actions == ["apply"]

    phases = [call["phase"] for call in runner.calls]
    assert phases == ["baseline"] * 8 + ["candidate"] * 8
    assert all(call["config"] == {"mode": "baseline"} for call in runner.calls[:8])
    assert all(call["config"] == {"mode": "candidate"} for call in runner.calls[8:])

    events = read_jsonl(tmp / "keep" / "experiment-log.jsonl")
    names = [event["ev"] for event in events]
    assert names == [
        "baseline_started",
        "baseline_completed",
        "proposal_locked",
        "config_applied",
        "candidate_started",
        "candidate_completed",
        "decision_made",
        "config_kept",
    ]
    assert report["integrity"]["experiment_log"]["valid"] is True

    tampered = read_jsonl(tmp / "keep" / "experiment-log.jsonl")
    tampered[0]["ev"] = "candidate_started"
    (tmp / "keep" / "experiment-log.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in tampered),
        encoding="utf-8",
    )
    assert ChainedEventLog(tmp / "keep" / "experiment-log.jsonl").verify()["valid"] is False


def test_config_mismatch_refuses_before_agent_execution(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "different-baseline"})
    try:
        run_experiment(
            proposal=proposal(baseline=0.5),
            evalset=task_set(count=4, baseline_misses={0, 1}),
            runner=runner,
            config=config,
            paths=ExperimentPaths.for_dir(tmp / "config-mismatch"),
            policy=strict_policy(min_samples=4),
        )
        raised = False
    except ValueError as exc:
        raised = "previous_config does not match" in str(exc)

    assert raised
    assert runner.calls == []
    assert config.actions == []


def test_measured_baseline_is_locked_before_candidate(tmp: Path) -> None:
    measured_proposal = proposal(baseline=0.0, predicted_delta=0.5)
    measured_proposal["baseline"] = "measured"
    report = run_experiment(
        proposal=measured_proposal,
        evalset=task_set(count=8, baseline_misses={0, 1, 2, 3, 4}),
        runner=RecordingRunner(),
        config=MemoryConfigAdapter({"mode": "baseline"}),
        paths=ExperimentPaths.for_dir(tmp / "measured-baseline"),
        policy=strict_policy(min_samples=8),
    )

    events = read_jsonl(tmp / "measured-baseline" / "experiment-log.jsonl")
    locked = next(event for event in events if event["ev"] == "proposal_locked")
    dashboard_text = (tmp / "measured-baseline" / "dashboard-data.js").read_text(
        encoding="utf-8"
    )
    assert report["proposal_verification"]["baseline"] == 0.375
    assert locked["manifest"]["proposal_hash"]
    assert '"baseline": 0.375' in dashboard_text


def test_interleaved_confirmation_uses_post_lock_control(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    paths = ExperimentPaths.for_dir(tmp / "interleaved")
    report = run_experiment(
        proposal=proposal(baseline=0.375, predicted_delta=0.5),
        evalset=task_set(count=8, baseline_misses={0, 1, 2, 3, 4}),
        runner=runner,
        config=config,
        paths=paths,
        policy=ExperimentPolicy(
            min_metric_samples=8,
            min_absolute_delta=0.1,
            alpha=0.05,
            bootstrap_samples=500,
            anchor_kind=None,
            comparison_mode="interleaved_control",
        ),
    )

    assert report["summary"]["decision"] == "keep_change"
    assert report["summary"]["records_registered"] == 16
    assert report["summary"]["records_resolved"] == 16
    assert report["comparison"]["records_registered"] == 16
    assert report["comparison"]["records_resolved"] == 16
    assert report["comparison"]["mode"] == "interleaved_control"
    assert report["comparison"]["effect_baseline_phase"] == "control"
    assert report["comparison"]["measured_order_counts"] == {
        "control_first": 4,
        "candidate_first": 4,
    }
    assert report["effect_gate"]["paired_n"] == 8
    assert paths.control_outputs.exists()
    assert paths.comparison_ledger.exists()
    assert report["integrity"]["ledger"]["events_verified"] == 32
    assert report["integrity"]["comparison_ledger"]["events_verified"] == 32

    baseline_calls = runner.calls[:8]
    confirmation_calls = runner.calls[8:]
    assert all(call["phase"] == "baseline" for call in baseline_calls)
    assert len(confirmation_calls) == 16
    observed_schedule = [
        {
            "task_id": confirmation_calls[index]["task_id"],
            "order": [
                confirmation_calls[index]["phase"],
                confirmation_calls[index + 1]["phase"],
            ],
        }
        for index in range(0, len(confirmation_calls), 2)
    ]
    assert observed_schedule == report["comparison"]["schedule"]
    assert all(
        set(item["order"]) == {"control", "candidate"}
        for item in observed_schedule
    )
    control_first = sum(item["order"][0] == "control" for item in observed_schedule)
    candidate_first = len(observed_schedule) - control_first
    assert abs(control_first - candidate_first) <= 1

    events = read_jsonl(paths.experiment_log)
    names = [event["ev"] for event in events]
    assert names.index("proposal_locked") < names.index("config_applied")
    assert names.index("config_applied") < names.index("confirmation_schedule_locked")
    assert names.index("confirmation_schedule_locked") < names.index("candidate_started")


def test_holdout_confirmation_is_disjoint_and_authoritative(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    paths = ExperimentPaths.for_dir(tmp / "holdout")
    development = task_set(
        count=8,
        baseline_misses={0, 1, 2, 3, 4},
        prefix="development",
    )
    holdout = task_set(
        count=8,
        baseline_misses={0, 1, 2, 3, 4},
        prefix="holdout",
    )
    report = run_experiment(
        proposal=proposal(baseline=0.375, predicted_delta=0.5),
        evalset=development,
        holdout_evalset=holdout,
        runner=runner,
        config=config,
        paths=paths,
        policy=ExperimentPolicy(
            min_metric_samples=8,
            min_absolute_delta=0.1,
            alpha=0.05,
            bootstrap_samples=500,
            anchor_kind=None,
            comparison_mode="interleaved_control",
        ),
    )

    assert report["summary"]["decision"] == "keep_change"
    assert report["experiment"]["evidence_level"] == "orchestrated_holdout"
    assert report["experiment"]["holdout_mode"] is True
    assert report["experiment"]["task_id_overlap"] == []
    assert report["experiment"]["baseline_task_count"] == 8
    assert report["experiment"]["confirmation_task_count"] == 8
    assert report["experiment"]["order"] == [
        "development_baseline",
        "proposal_locked",
        "config_applied",
        "holdout_control_candidate",
        "verification",
    ]
    assert report["experiment"]["baseline_evalset_hash"] != report["experiment"][
        "confirmation_evalset_hash"
    ]
    assert report["comparison"]["scope"] == "holdout"
    assert report["comparison"]["effect_baseline_phase"] == "control"
    assert report["effect_gate"]["paired_n"] == 8
    assert report["effect_gate"]["improvements"] == 5
    assert report["effect_gate"]["passed"] is True
    assert report["health"]["scope"] == "holdout_control_candidate"
    assert report["health"]["signals"][0] == {
        "name": "hit_rate",
        "prior": 0.375,
        "recent": 1.0,
        "delta": 0.625,
        "degraded": False,
    }

    baseline_calls = runner.calls[:8]
    confirmation_calls = runner.calls[8:]
    assert {call["task_id"] for call in baseline_calls} == {
        task["task_id"] for task in development
    }
    assert {call["task_id"] for call in confirmation_calls} == {
        task["task_id"] for task in holdout
    }
    assert all(call["phase"] == "baseline" for call in baseline_calls)
    assert len(confirmation_calls) == 16

    locked = next(
        event
        for event in read_jsonl(paths.experiment_log)
        if event["ev"] == "proposal_locked"
    )
    assert locked["manifest"]["holdout_mode"] is True
    assert locked["manifest"]["task_id_overlap"] == []
    assert locked["manifest"]["confirmation_evalset_hash"] == report["experiment"][
        "confirmation_evalset_hash"
    ]


def test_holdout_task_overlap_refuses_before_agent_execution(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    development = task_set(count=4, baseline_misses={0, 1}, prefix="shared")
    holdout = task_set(count=4, baseline_misses={0, 1}, prefix="shared")
    try:
        run_experiment(
            proposal=proposal(baseline=0.5),
            evalset=development,
            holdout_evalset=holdout,
            runner=runner,
            config=config,
            paths=ExperimentPaths.for_dir(tmp / "holdout-overlap"),
            policy=strict_policy(min_samples=4),
        )
        raised = False
    except ValueError as exc:
        raised = "development and holdout task IDs must be disjoint" in str(exc)

    assert raised
    assert runner.calls == []
    assert config.actions == []


def test_holdout_keeps_separate_development_and_paired_thresholds(tmp: Path) -> None:
    report = run_experiment(
        proposal=proposal(baseline=0.5, predicted_delta=0.5),
        evalset=task_set(
            count=4,
            baseline_misses={0, 1},
            prefix="small-development",
        ),
        holdout_evalset=task_set(
            count=8,
            baseline_misses={0, 1, 2, 3, 4},
            prefix="larger-holdout",
        ),
        runner=RecordingRunner(),
        config=MemoryConfigAdapter({"mode": "baseline"}),
        paths=ExperimentPaths.for_dir(tmp / "separate-thresholds"),
        policy=ExperimentPolicy(
            min_metric_samples=8,
            min_development_samples=4,
            min_absolute_delta=0.1,
            alpha=0.05,
            bootstrap_samples=500,
            anchor_kind=None,
            comparison_mode="interleaved_control",
        ),
    )

    assert report["summary"]["decision"] == "keep_change"
    assert report["sample_gate"] == {
        "passed": True,
        "baseline_n": 4,
        "candidate_n": 8,
        "minimum_per_phase": 4,
    }
    assert report["effect_gate"]["paired_n"] == 8
    assert report["effect_gate"]["minimum_samples"] == 8

    try:
        ExperimentPolicy(min_development_samples=0).validate()
        raised = False
    except ValueError as exc:
        raised = "min_development_samples" in str(exc)
    assert raised


def test_very_small_exact_p_value_is_not_rounded_to_zero() -> None:
    tasks = task_set(count=32, baseline_misses=set(range(32)))
    baseline_rows = [
        {
            "task_id": task["task_id"],
            "kind": task["kind"],
            "machine_verifiable": True,
            "actual_pass": False,
        }
        for task in tasks
    ]
    candidate_rows = [
        {
            "task_id": task["task_id"],
            "kind": task["kind"],
            "machine_verifiable": True,
            "actual_pass": True,
        }
        for task in tasks
    ]
    gate = paired_effect_gate(
        baseline_rows,
        candidate_rows,
        evalset=tasks,
        kind="agent_task",
        policy=ExperimentPolicy(
            min_metric_samples=20,
            min_absolute_delta=0.05,
            alpha=0.05,
            bootstrap_samples=500,
            anchor_kind=None,
        ),
    )

    assert gate["passed"] is True
    assert gate["exact_p_value"] > 0
    assert math.isclose(gate["exact_p_value"], 2 ** -32)
    assert "2.328e-10" in gate["reasons"][0]


def test_low_confidence_lift_restores_config(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    report = run_experiment(
        proposal=proposal(baseline=0.5, predicted_delta=0.25),
        evalset=task_set(count=4, baseline_misses={0, 1}),
        runner=runner,
        config=config,
        paths=ExperimentPaths.for_dir(tmp / "low-confidence"),
        policy=strict_policy(min_samples=4),
    )

    assert report["effect_gate"]["actual_delta"] == 0.5
    assert report["effect_gate"]["exact_p_value"] == 0.25
    assert report["effect_gate"]["passed"] is False
    assert report["summary"]["decision"] == "pause_for_unproven_increment"
    assert report["policy_execution"]["action"] == "restore_baseline"
    assert config.current == {"mode": "baseline"}
    assert config.actions == ["apply", "restore"]


def test_critical_regression_blocks_aggregate_lift(tmp: Path) -> None:
    runner = RecordingRunner()
    config = MemoryConfigAdapter({"mode": "baseline"})
    report = run_experiment(
        proposal=proposal(baseline=0.2, predicted_delta=0.4),
        evalset=task_set(
            count=10,
            baseline_misses={0, 1, 2, 3, 4, 5, 6, 7},
            candidate_misses={8},
            critical={8},
        ),
        runner=runner,
        config=config,
        paths=ExperimentPaths.for_dir(tmp / "critical"),
        policy=strict_policy(min_samples=10),
    )

    assert report["effect_gate"]["actual_delta"] > 0
    assert report["effect_gate"]["critical_regressions"] == ["task-08"]
    assert report["effect_gate"]["passed"] is False
    assert report["summary"]["decision"] == "pause_for_unproven_increment"
    assert config.current == {"mode": "baseline"}


def test_json_config_is_actually_restored_on_rollback(tmp: Path) -> None:
    config_path = tmp / "agent-config.json"
    config_path.write_text('{"mode":"baseline"}\n', encoding="utf-8")
    config = JsonFileConfigAdapter(config_path)
    report = run_experiment(
        proposal=proposal(baseline=1.0),
        evalset=task_set(
            count=8,
            baseline_misses=set(),
            candidate_misses=set(range(8)),
        ),
        runner=RecordingRunner(),
        config=config,
        paths=ExperimentPaths.for_dir(tmp / "rollback"),
        policy=strict_policy(min_samples=8),
    )

    restored = json.loads(config_path.read_text(encoding="utf-8"))
    receipt = report["policy_execution"]["receipt"]
    assert report["summary"]["decision"] == "rollback_and_pause_lessons"
    assert restored == {"mode": "baseline"}
    assert receipt["action"] == "restore"
    assert receipt["before_hash"] != receipt["after_hash"]
    assert receipt["after_hash"] == report["experiment"]["baseline_config_hash"]


def test_candidate_failure_restores_config_before_raising(tmp: Path) -> None:
    runner = RecordingRunner(fail_candidate=True)
    config = MemoryConfigAdapter({"mode": "baseline"})
    paths = ExperimentPaths.for_dir(tmp / "failure")
    try:
        run_experiment(
            proposal=proposal(baseline=0.5),
            evalset=task_set(count=4, baseline_misses={0, 1}),
            runner=runner,
            config=config,
            paths=paths,
            policy=strict_policy(min_samples=4),
        )
        raised = False
    except RuntimeError as exc:
        raised = str(exc) == "candidate runner failed"

    assert raised
    assert config.current == {"mode": "baseline"}
    assert config.actions == ["apply", "restore"]
    events = read_jsonl(paths.experiment_log)
    assert [event["ev"] for event in events][-2:] == ["experiment_failed", "config_restored"]


def test_interleaved_candidate_failure_restores_config(tmp: Path) -> None:
    runner = RecordingRunner(fail_candidate=True)
    config = MemoryConfigAdapter({"mode": "baseline"})
    paths = ExperimentPaths.for_dir(tmp / "interleaved-failure")
    try:
        run_experiment(
            proposal=proposal(baseline=0.5),
            evalset=task_set(count=4, baseline_misses={0, 1}),
            runner=runner,
            config=config,
            paths=paths,
            policy=ExperimentPolicy(
                min_metric_samples=4,
                min_absolute_delta=0.1,
                alpha=0.05,
                bootstrap_samples=500,
                anchor_kind=None,
                comparison_mode="interleaved_control",
            ),
        )
        raised = False
    except RuntimeError as exc:
        raised = str(exc) == "candidate runner failed"

    assert raised
    assert config.current == {"mode": "baseline"}
    assert config.actions == ["apply", "restore"]
    names = [event["ev"] for event in read_jsonl(paths.experiment_log)]
    assert "confirmation_schedule_locked" in names
    assert names[-2:] == ["experiment_failed", "config_restored"]


def test_partial_apply_failure_restores_baseline(tmp: Path) -> None:
    config = MutatingFailConfigAdapter({"mode": "baseline"})
    paths = ExperimentPaths.for_dir(tmp / "partial-apply")
    try:
        run_experiment(
            proposal=proposal(baseline=0.5),
            evalset=task_set(count=4, baseline_misses={0, 1}),
            runner=RecordingRunner(),
            config=config,
            paths=paths,
            policy=strict_policy(min_samples=4),
        )
        raised = False
    except RuntimeError as exc:
        raised = str(exc) == "apply failed after mutation"

    assert raised
    assert config.current == {"mode": "baseline"}
    assert config.actions == ["apply", "restore"]
    events = read_jsonl(paths.experiment_log)
    assert [event["ev"] for event in events][-2:] == ["experiment_failed", "config_restored"]


def test_cli_runs_external_agent_without_leaking_checks(tmp: Path) -> None:
    out_dir = tmp / "command-experiment"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_experiment.py",
            "--proposal",
            "fixtures/orchestrated_proposal.json",
            "--evalset",
            "fixtures/orchestrated_evalset.jsonl",
            "--initialize-config",
            "fixtures/orchestrated_config.json",
            "--runner-command",
            f"{sys.executable} fixtures/orchestrated_command_agent.py",
            "--out-dir",
            str(out_dir),
            "--min-absolute-delta",
            "0.1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "keep_change" in completed.stdout
    report = json.loads((out_dir / "drift-report.json").read_text(encoding="utf-8"))
    assert report["experiment"]["evidence_level"] == "orchestrated"
    assert report["summary"]["records_registered"] == 44
    assert report["summary"]["records_resolved"] == 44
    assert report["comparison"]["records_registered"] == 44
    assert report["comparison"]["records_resolved"] == 44
    assert report["integrity"]["ledger"]["events_verified"] == 88
    assert report["integrity"]["comparison_ledger"]["events_verified"] == 88
    assert report["comparison"]["mode"] == "interleaved_control"
    assert report["comparison"]["effect_baseline_phase"] == "control"
    assert report["comparison"]["measured_order_counts"] == {
        "control_first": 10,
        "candidate_first": 10,
    }
    assert report["effect_gate"]["passed"] is True
    assert report["effect_gate"]["paired_n"] == 20
    assert report["effect_gate"]["actual_delta"] == 0.6
    assert math.isclose(report["effect_gate"]["exact_p_value"], 2 ** -12)
    assert report["policy_execution"]["action"] == "keep_candidate"
    assert report["policy_execution"]["learning_action"] == "pause_lesson_injection"
    assert json.loads((out_dir / "active-config.json").read_text(encoding="utf-8")) == {
        "mode": "candidate"
    }
    assert (out_dir / "index.html").exists()
    viewer_html = (out_dir / "index.html").read_text(encoding="utf-8")
    dashboard_text = (out_dir / "dashboard-data.js").read_text(encoding="utf-8")
    assert './dashboard-data.js' in viewer_html
    assert 'id="baseline-note"' in viewer_html
    assert 'id="candidate-note"' in viewer_html
    assert dashboard_text.count("DRIFTGUARD_DATA.command") == 1
    outputs = read_jsonl(out_dir / "control-outputs.jsonl") + read_jsonl(
        out_dir / "candidate-outputs.jsonl"
    )
    assert all(row["metadata"]["saw_check"] is False for row in outputs)
    assert all(row["metadata"]["saw_expected_value"] is False for row in outputs)

    refused = subprocess.run(
        completed.args,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 1
    assert "refusing to overwrite existing experiment artifacts" in refused.stderr


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        test_keep_requires_locked_order_and_paired_confidence(tmp)
        test_config_mismatch_refuses_before_agent_execution(tmp)
        test_measured_baseline_is_locked_before_candidate(tmp)
        test_interleaved_confirmation_uses_post_lock_control(tmp)
        test_holdout_confirmation_is_disjoint_and_authoritative(tmp)
        test_holdout_task_overlap_refuses_before_agent_execution(tmp)
        test_holdout_keeps_separate_development_and_paired_thresholds(tmp)
        test_very_small_exact_p_value_is_not_rounded_to_zero()
        test_low_confidence_lift_restores_config(tmp)
        test_critical_regression_blocks_aggregate_lift(tmp)
        test_json_config_is_actually_restored_on_rollback(tmp)
        test_candidate_failure_restores_config_before_raising(tmp)
        test_interleaved_candidate_failure_restores_config(tmp)
        test_partial_apply_failure_restores_baseline(tmp)
        test_cli_runs_external_agent_without_leaking_checks(tmp)
    print("authoritative experiment checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
