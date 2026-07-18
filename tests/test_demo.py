"""End-to-end assertions for the Build Week demo story."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main() -> int:
    subprocess.run(
        [sys.executable, "scripts/run_demo.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads((ARTIFACTS / "drift-report.json").read_text(encoding="utf-8"))
    ledger = read_jsonl(ARTIFACTS / "demo-ledger.jsonl")
    proposals = read_jsonl(ARTIFACTS / "proposal-log.jsonl")

    baseline_reviews = [
        event["ts"]
        for event in ledger
        if event["ev"] == "review" and int(event["id"].split("-")[1]) <= 4
    ]
    candidate_registers = [
        event["ts"]
        for event in ledger
        if event["ev"] == "register" and int(event["id"].split("-")[1]) >= 5
    ]

    assert max(baseline_reviews) < proposals[0]["ts"]
    assert proposals[1]["ev"] == "apply"
    assert proposals[1]["ts"] < min(candidate_registers)
    assert report["proposal_verification"]["baseline"] == 0.75
    assert report["proposal_verification"]["current_value"] == 0.0
    assert report["proposal_verification"]["actual_delta"] == -0.75
    assert report["summary"]["decision"] == "rollback_and_pause_lessons"
    assert report["integrity"]["ledger"]["valid"]
    assert report["integrity"]["proposal_log"]["valid"]
    assert report["integrity"]["ledger"]["events_verified"] == 32

    subprocess.run(
        [sys.executable, "scripts/run_demo.py", "--scenario", "keep"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    keep_report = json.loads(
        (ARTIFACTS / "drift-report.json").read_text(encoding="utf-8")
    )
    assert keep_report["proposal_verification"]["status"] == "verified"
    assert keep_report["proposal_verification"]["actual_delta"] == 0.25
    assert keep_report["health"]["healthy"]
    assert keep_report["drift"]["status"] == "stable"
    assert keep_report["summary"]["decision"] == "keep_change"

    subprocess.run(
        [sys.executable, "scripts/run_demo.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    print("end-to-end demo checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
