"""End-to-end assertions for the generic rollout evaluator.

Run: python3 tests/test_rollout.py   (no pytest needed)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit import RolloutPaths, evaluate_rollout  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def split_rows(path: Path) -> tuple[list[dict], list[dict]]:
    rows = read_jsonl(path)
    baseline = [row for row in rows if row["phase"] == "baseline"]
    candidate = [row for row in rows if row["phase"] == "candidate"]
    return baseline, candidate


def paths(tmp: Path, name: str) -> RolloutPaths:
    out = tmp / name
    return RolloutPaths(
        ledger=out / "ledger.jsonl",
        proposal_log=out / "proposal-log.jsonl",
        report=out / "drift-report.json",
        decision=out / "decision.md",
        dashboard_data=out / "dashboard-data.js",
    )


def assert_chronology(paths_: RolloutPaths) -> None:
    ledger = read_jsonl(paths_.ledger)
    proposals = read_jsonl(paths_.proposal_log)
    baseline_reviews = [
        event["ts"]
        for event in ledger
        if event["ev"] == "review" and event["id"] in {"agent-001", "agent-002", "agent-003", "agent-004"}
    ]
    candidate_registers = [
        event["ts"]
        for event in ledger
        if event["ev"] == "register" and event["id"] in {"agent-005", "agent-006", "agent-007", "agent-008"}
    ]
    assert max(baseline_reviews) < proposals[0]["ts"]
    assert proposals[1]["ev"] == "apply"
    assert proposals[1]["ts"] < min(candidate_registers)


def test_generic_rollback(tmp: Path) -> None:
    proposal = read_json(ROOT / "fixtures" / "proposal.json")
    baseline, candidate = split_rows(ROOT / "fixtures" / "agent_runs.jsonl")
    out = paths(tmp, "rollback")
    report = evaluate_rollout(
        proposal=proposal,
        baseline_rows=baseline,
        candidate_rows=candidate,
        paths=out,
        scenario="fixture_rollback",
    )
    assert report["summary"]["decision"] == "rollback_and_pause_lessons"
    assert report["experiment"]["evidence_level"] == "post_hoc"
    assert report["experiment"]["limitations"]
    assert report["proposal_verification"]["actual_delta"] == -0.75
    assert report["integrity"]["ledger"]["valid"]
    assert report["integrity"]["proposal_log"]["valid"]
    assert out.report and out.report.exists()
    assert out.decision and "rollback_and_pause_lessons" in out.decision.read_text(encoding="utf-8")
    assert_chronology(out)


def test_generic_keep(tmp: Path) -> None:
    proposal = read_json(ROOT / "fixtures" / "proposal.json")
    baseline, candidate = split_rows(ROOT / "fixtures" / "agent_runs_keep.jsonl")
    out = paths(tmp, "keep")
    report = evaluate_rollout(
        proposal=proposal,
        baseline_rows=baseline,
        candidate_rows=candidate,
        paths=out,
        scenario="fixture_keep",
    )
    assert report["summary"]["decision"] == "keep_change"
    assert report["proposal_verification"]["status"] == "verified"
    assert report["proposal_verification"]["actual_delta"] == 0.25
    assert report["health"]["healthy"]
    assert report["drift"]["status"] == "stable"
    assert_chronology(out)


def test_cli_real_rollout_entrypoint(tmp: Path) -> None:
    proposal = ROOT / "fixtures" / "proposal.json"
    baseline_rows, candidate_rows = split_rows(ROOT / "fixtures" / "agent_runs.jsonl")
    baseline_path = tmp / "baseline.jsonl"
    candidate_path = tmp / "candidate.jsonl"
    baseline_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in baseline_rows) + "\n",
        encoding="utf-8",
    )
    candidate_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in candidate_rows) + "\n",
        encoding="utf-8",
    )
    out_dir = tmp / "cli-out"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_rollout.py",
            "--proposal",
            str(proposal),
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--out-dir",
            str(out_dir),
            "--scenario",
            "cli_shadow_eval",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "rollback_and_pause_lessons" in completed.stdout
    report = read_json(out_dir / "drift-report.json")
    assert report["summary"]["scenario"] == "cli_shadow_eval"
    assert report["summary"]["decision"] == "rollback_and_pause_lessons"

    refused = subprocess.run(
        [
            sys.executable,
            "scripts/run_rollout.py",
            "--proposal",
            str(proposal),
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "refusing to overwrite" in refused.stderr


def test_locked_baseline_must_match_measured_baseline(tmp: Path) -> None:
    proposal = read_json(ROOT / "fixtures" / "proposal.json")
    proposal["baseline"] = 0.5
    baseline, candidate = split_rows(ROOT / "fixtures" / "agent_runs.jsonl")
    try:
        evaluate_rollout(
            proposal=proposal,
            baseline_rows=baseline,
            candidate_rows=candidate,
            paths=paths(tmp, "baseline-mismatch"),
            scenario="baseline_mismatch",
        )
        raised = False
    except ValueError as exc:
        raised = "proposal baseline does not match" in str(exc)
    assert raised


def test_small_samples_pause_instead_of_keep(tmp: Path) -> None:
    proposal = {
        "id": "small-sample-change",
        "change": {"prompt": "candidate"},
        "metric": "agent_task_hit_rate",
        "baseline": 0.5,
        "predicted_delta": 0.25,
        "previous_config": {"prompt": "baseline"},
    }
    baseline = [
        {
            "run_id": "small-base-1",
            "kind": "agent_task",
            "machine_verifiable": True,
            "actual_pass": True,
            "prob": 0.7,
        },
        {
            "run_id": "small-base-2",
            "kind": "agent_task",
            "machine_verifiable": True,
            "actual_pass": False,
            "prob": 0.4,
            "miss_reason": "baseline_failure",
        },
    ]
    candidate = [
        {
            "run_id": "small-cand-1",
            "kind": "agent_task",
            "machine_verifiable": True,
            "actual_pass": True,
            "prob": 0.8,
        },
        {
            "run_id": "small-cand-2",
            "kind": "agent_task",
            "machine_verifiable": True,
            "actual_pass": True,
            "prob": 0.8,
        },
    ]
    report = evaluate_rollout(
        proposal=proposal,
        baseline_rows=baseline,
        candidate_rows=candidate,
        paths=paths(tmp, "small-sample"),
        scenario="small_sample",
        min_metric_samples=3,
    )
    assert report["proposal_verification"]["status"] == "verified"
    assert report["sample_gate"]["passed"] is False
    assert report["summary"]["decision"] == "pause_for_more_evidence"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_generic_rollback(tmp)
        test_generic_keep(tmp)
        test_cli_real_rollout_entrypoint(tmp)
        test_locked_baseline_must_match_measured_baseline(tmp)
        test_small_samples_pause_instead_of_keep(tmp)
    print("generic rollout checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
