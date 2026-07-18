#!/usr/bin/env python3
"""Evaluate a real agent rollout with DriftGuard gates.

This is the non-demo entry point. Provide a locked proposal plus baseline and
candidate outcome JSONL files; the script writes an evidence ledger, proposal
log, drift report, and deterministic decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from feedback_kit import RolloutPaths, evaluate_rollout  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL row") from exc
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", required=True, type=Path, help="locked proposal JSON")
    parser.add_argument("--baseline", required=True, type=Path, help="baseline outcome JSONL")
    parser.add_argument("--candidate", required=True, type=Path, help="candidate outcome JSONL")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for generated artifacts")
    parser.add_argument("--scenario", default="real_rollout", help="label stored in the report")
    parser.add_argument("--agent-kind", default="agent_task", help="default kind for metric='hit_rate'")
    parser.add_argument(
        "--anchor-kind",
        default="anchor_task",
        help="optional canary kind for drift detection; use '' to disable",
    )
    parser.add_argument("--health-window", type=int, default=None, help="override health comparison window")
    parser.add_argument(
        "--min-metric-samples",
        type=int,
        default=4,
        help="minimum machine-verifiable rows required per baseline/candidate phase",
    )
    parser.add_argument("--hit-drop-tol", type=float, default=0.1, help="rollback tolerance for hit-rate drop")
    parser.add_argument("--drift-gap-tol", type=float, default=0.05, help="pause tolerance for anchor drift gap")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing generated artifacts in --out-dir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out_dir
    paths = RolloutPaths(
        ledger=out_dir / "ledger.jsonl",
        proposal_log=out_dir / "proposal-log.jsonl",
        report=out_dir / "drift-report.json",
        decision=out_dir / "decision.md",
        dashboard_data=out_dir / "dashboard-data.js",
    )
    try:
        report = evaluate_rollout(
            proposal=read_json(args.proposal),
            baseline_rows=read_jsonl(args.baseline),
            candidate_rows=read_jsonl(args.candidate),
            paths=paths,
            scenario=args.scenario,
            agent_kind=args.agent_kind,
            anchor_kind=args.anchor_kind or None,
            health_window=args.health_window,
            min_metric_samples=args.min_metric_samples,
            hit_drop_tol=args.hit_drop_tol,
            drift_gap_tol=args.drift_gap_tol,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, TypeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Agent DriftGuard rollout evaluation complete")
    print(f"- scenario: {report['summary']['scenario']}")
    print(f"- decision: {report['summary']['decision']}")
    print(f"- ledger: {paths.ledger}")
    print(f"- proposal log: {paths.proposal_log}")
    print(f"- report: {paths.report}")
    print(f"- decision file: {paths.decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
