"""Checks for evalset-driven real increment evidence.

Run: python3 tests/test_evalset.py   (no pytest needed)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit import build_outcome_rows, evaluate_rollout, RolloutPaths  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_evalset_builds_rollout_rows(tmp: Path) -> None:
    evalset = read_jsonl(ROOT / "fixtures" / "real_increment_evalset.jsonl")
    baseline_outputs = read_jsonl(ROOT / "fixtures" / "real_increment_baseline_outputs.jsonl")
    candidate_outputs = read_jsonl(ROOT / "fixtures" / "real_increment_candidate_outputs.jsonl")

    baseline = build_outcome_rows(
        evalset=evalset,
        outputs=baseline_outputs,
        phase="baseline",
    )
    candidate = build_outcome_rows(
        evalset=evalset,
        outputs=candidate_outputs,
        phase="candidate",
    )

    assert all("actual_pass" not in row for row in baseline_outputs + candidate_outputs)
    assert [row["task_id"] for row in baseline] == [row["task_id"] for row in candidate]
    baseline_agent = [row for row in baseline if row["kind"] == "agent_task"]
    candidate_agent = [row for row in candidate if row["kind"] == "agent_task"]
    assert sum(row["actual_pass"] for row in baseline_agent) == 2
    assert sum(row["actual_pass"] for row in candidate_agent) == 4
    assert {row["miss_reason"] for row in baseline_agent if not row["actual_pass"]} == {
        "bad_extraction",
        "missing_required_method",
    }

    report = evaluate_rollout(
        proposal=read_json(ROOT / "fixtures" / "real_increment_proposal.json"),
        baseline_rows=baseline,
        candidate_rows=candidate,
        paths=RolloutPaths(
            ledger=tmp / "ledger.jsonl",
            proposal_log=tmp / "proposal-log.jsonl",
            report=tmp / "drift-report.json",
            decision=tmp / "decision.md",
            dashboard_data=tmp / "dashboard-data.js",
        ),
        scenario="evalset_real_increment",
    )
    assert report["summary"]["decision"] == "keep_change"
    assert report["proposal_verification"]["actual_delta"] == 0.5
    assert report["metrics"]["baseline_hit_rate"]["hit_rate"] == 0.5
    assert report["metrics"]["candidate_hit_rate"]["hit_rate"] == 1.0


def test_json_path_failures_are_misses_not_crashes() -> None:
    rows = build_outcome_rows(
        evalset=[
            {
                "task_id": "json-required",
                "kind": "agent_task",
                "check": {"type": "json_path_equals", "path": "route", "value": "billing"},
            }
        ],
        outputs=[{"task_id": "json-required", "output": "not json", "prob": 0.8}],
        phase="candidate",
    )
    assert len(rows) == 1
    assert rows[0]["actual_pass"] is False
    assert rows[0]["miss_reason"] == "json_path_unavailable"


def test_misconfigured_checks_fail_closed() -> None:
    try:
        build_outcome_rows(
            evalset=[
                {
                    "task_id": "bad-regex",
                    "kind": "agent_task",
                    "check": {"type": "regex"},
                }
            ],
            outputs=[{"task_id": "bad-regex", "output": "anything"}],
            phase="baseline",
        )
        raised = False
    except ValueError as exc:
        raised = "missing required field" in str(exc)
    assert raised


def test_cli_verify_then_rollout(tmp: Path) -> None:
    baseline_rows = tmp / "baseline-rows.jsonl"
    candidate_rows = tmp / "candidate-rows.jsonl"

    for phase, outputs, out_path in [
        ("baseline", "real_increment_baseline_outputs.jsonl", baseline_rows),
        ("candidate", "real_increment_candidate_outputs.jsonl", candidate_rows),
    ]:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/verify_outputs.py",
                "--evalset",
                str(ROOT / "fixtures" / "real_increment_evalset.jsonl"),
                "--outputs",
                str(ROOT / "fixtures" / outputs),
                "--phase",
                phase,
                "--out",
                str(out_path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "output verification complete" in completed.stdout
        assert out_path.exists()

    out_dir = tmp / "rollout"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_rollout.py",
            "--proposal",
            str(ROOT / "fixtures" / "real_increment_proposal.json"),
            "--baseline",
            str(baseline_rows),
            "--candidate",
            str(candidate_rows),
            "--out-dir",
            str(out_dir),
            "--scenario",
            "cli_evalset_real_increment",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "keep_change" in completed.stdout
    report = read_json(out_dir / "drift-report.json")
    assert report["summary"]["decision"] == "keep_change"
    assert report["proposal_verification"]["status"] == "verified"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_evalset_builds_rollout_rows(tmp)
        test_json_path_failures_are_misses_not_crashes()
        test_misconfigured_checks_fail_closed()
        test_cli_verify_then_rollout(tmp)
    print("evalset checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
