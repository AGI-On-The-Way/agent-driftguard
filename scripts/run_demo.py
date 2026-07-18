#!/usr/bin/env python3
"""Run the self-contained Agent DriftGuard Build Week demo.

The script uses only Python's standard library plus the bundled ``src``
package. It rebuilds small demo artifacts on each run so judges can inspect
the full ledger/report path without external services or secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from feedback_kit import (  # noqa: E402
    Ledger,
    Outcome,
    ProposalLog,
    Verdict,
    brier_score,
    distill,
    drift_check,
    health_check,
    hit_rate,
    reliability,
    review_pending,
)


FIXTURES = WORKSPACE / "fixtures"
ARTIFACTS = WORKSPACE / "artifacts"
LEDGER_PATH = ARTIFACTS / "demo-ledger.jsonl"
PROPOSALS_PATH = ARTIFACTS / "proposal-log.jsonl"
REPORT_PATH = ARTIFACTS / "drift-report.json"
DECISION_PATH = ARTIFACTS / "decision.md"
DASHBOARD_DATA_PATH = ARTIFACTS / "dashboard-data.js"
SCENARIO_FIXTURES = {
    "rollback": "agent_runs.jsonl",
    "keep": "agent_runs_keep.jsonl",
}


class AgentRunAdapter:
    name = "agent-driftguard-demo"
    kinds = {"agent_task", "anchor_task"}

    def __init__(self, rows: list[dict[str, Any]]):
        self.by_id = {row["run_id"]: row for row in rows}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self.by_id

    def verdict(self, record: dict[str, Any], snapshot: dict[str, dict[str, Any]]) -> Verdict:
        run = snapshot[record["payload"]["run_id"]]
        if run["actual_pass"]:
            return Verdict(
                Outcome.HIT,
                machine_verifiable=run["machine_verifiable"],
                confidence=1.0,
                detail={"note": run["note"]},
            )
        return Verdict(
            Outcome.MISS,
            machine_verifiable=run["machine_verifiable"],
            confidence=1.0,
            attribution=run["miss_reason"],
            attribution_machine_verifiable=True,
            detail={"note": run["note"]},
        )


def read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def rebuild_outputs() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    generated = (
        LEDGER_PATH,
        PROPOSALS_PATH,
        REPORT_PATH,
        DECISION_PATH,
        DASHBOARD_DATA_PATH,
        ARTIFACTS / "rollback-decision.md",
    )
    for path in generated:
        if path.exists():
            path.unlink()


def register_runs(ledger: Ledger, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        ledger.register(
            row["kind"],
            {
                "run_id": row["run_id"],
                "phase": row["phase"],
                "note": row["note"],
                "machine_verifiable": row["machine_verifiable"],
            },
            id=row["run_id"],
            prob=row["prob"],
        )


def phase_hit_rate(ledger: Ledger, phase: str) -> dict[str, Any]:
    records = [
        record
        for record in ledger.records().values()
        if record["kind"] == "agent_task"
        and record["payload"]["phase"] == phase
        and record["machine_verifiable"]
        and record["outcome"] in {"hit", "miss"}
    ]
    hits = sum(record["outcome"] == "hit" for record in records)
    return {
        "hit_rate": round(hits / len(records), 3) if records else None,
        "hits": hits,
        "n": len(records),
    }


def decide(report: dict[str, Any]) -> dict[str, Any]:
    proposal = report["proposal_verification"]
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
    if drift["recommend"]:
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


def build_decision(decision: dict[str, Any]) -> str:
    title = decision["action"].replace("_", " ").title()
    lines = [
        f"# {title}",
        "",
        f"Decision: {decision['action']}.",
        "",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in decision["reasons"])
    if decision["restored_config"] is not None:
        lines.extend([
            "",
            "Restored config:",
            "",
            "```json",
            json.dumps(decision["restored_config"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ])
    lines.extend([
        "",
        "This decision is deterministic: it is derived from hash-chained evidence, "
        "machine-verifiable outcomes, the locked proposal prediction, and statistical gates.",
        "",
    ])
    return "\n".join(lines)


def write_dashboard_data(
    report: dict[str, Any],
    proposal: dict[str, Any],
    ledger: Ledger,
    proposals: ProposalLog,
    decision_markdown: str,
) -> None:
    data = {
        "report": report,
        "proposal": proposal,
        "ledger": list(ledger.events()),
        "proposal_events": list(proposals.events()),
        "decision_markdown": decision_markdown,
    }
    DASHBOARD_DATA_PATH.write_text(
        "window.DRIFTGUARD_DATA = "
        + json.dumps(data, ensure_ascii=False, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIO_FIXTURES),
        default="rollback",
        help="evidence scenario to evaluate (default: rollback)",
    )
    args = parser.parse_args(argv)
    rebuild_outputs()

    proposal = read_json(FIXTURES / "proposal.json")
    rows = read_jsonl(FIXTURES / SCENARIO_FIXTURES[args.scenario])
    baseline_rows = [row for row in rows if row["phase"] == "baseline"]
    candidate_rows = [row for row in rows if row["phase"] == "candidate"]

    ledger = Ledger(LEDGER_PATH)
    register_runs(ledger, baseline_rows)
    baseline_resolved = review_pending(ledger, AgentRunAdapter(baseline_rows))
    baseline_hit_rate = phase_hit_rate(ledger, "baseline")
    measured_baseline = baseline_hit_rate["hit_rate"]
    if measured_baseline != proposal["baseline"]:
        raise ValueError(
            "proposal baseline does not match machine-verifiable baseline: "
            f"locked={proposal['baseline']}, measured={measured_baseline}"
        )

    proposals = ProposalLog(PROPOSALS_PATH)
    pid = proposals.propose(
        id=proposal["id"],
        change=proposal["change"],
        metric=proposal["metric"],
        predicted_delta=proposal["predicted_delta"],
        baseline=proposal["baseline"],
        description=proposal["description"],
    )
    proposals.apply(pid, prev_config=proposal["previous_config"])

    register_runs(ledger, candidate_rows)
    candidate_resolved = review_pending(ledger, AgentRunAdapter(candidate_rows))

    agent_hit_rate = hit_rate(ledger, kind="agent_task")
    candidate_hit_rate = phase_hit_rate(ledger, "candidate")
    current_value = candidate_hit_rate["hit_rate"]
    verification = proposals.verify(pid, current_value)

    lessons = distill(ledger, kind="agent_task")
    health = health_check(ledger, kind="agent_task", window=4, hit_drop_tol=0.1)
    drift = drift_check(ledger, anchor_kind="anchor_task", gap_tol=0.05)

    report = {
        "summary": {
            "scenario": args.scenario,
            "records_registered": len(rows),
            "records_resolved": len(baseline_resolved) + len(candidate_resolved),
            "decision": None,
        },
        "experiment": {
            "scenario": args.scenario,
            "order": ["baseline", "proposal_locked", "candidate", "verification"],
            "baseline_run_ids": [row["run_id"] for row in baseline_rows],
            "candidate_run_ids": [row["run_id"] for row in candidate_rows],
        },
        "proposal_verification": verification,
        "metrics": {
            "agent_hit_rate": agent_hit_rate,
            "baseline_hit_rate": baseline_hit_rate,
            "candidate_hit_rate": candidate_hit_rate,
            "agent_brier": brier_score(ledger, kind="agent_task"),
            "anchor_brier": brier_score(ledger, kind="anchor_task"),
            "overall_brier": brier_score(ledger),
            "agent_reliability": reliability(ledger, kind="agent_task"),
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
        "artifacts": {
            "ledger": str(LEDGER_PATH.relative_to(WORKSPACE)),
            "proposal_log": str(PROPOSALS_PATH.relative_to(WORKSPACE)),
            "report": str(REPORT_PATH.relative_to(WORKSPACE)),
            "decision": str(DECISION_PATH.relative_to(WORKSPACE)),
            "dashboard_data": str(DASHBOARD_DATA_PATH.relative_to(WORKSPACE)),
        },
    }

    decision = decide(report)
    report["summary"]["decision"] = decision["action"]
    report["decision"] = decision
    write_json(REPORT_PATH, report)
    decision_markdown = build_decision(decision)
    DECISION_PATH.write_text(decision_markdown, encoding="utf-8")
    write_dashboard_data(report, proposal, ledger, proposals, decision_markdown)

    print("Agent DriftGuard demo complete")
    print(f"- scenario: {args.scenario}")
    print(f"- ledger: {LEDGER_PATH.relative_to(WORKSPACE)}")
    print(f"- proposal log: {PROPOSALS_PATH.relative_to(WORKSPACE)}")
    print(f"- report: {REPORT_PATH.relative_to(WORKSPACE)}")
    print(f"- decision: {DECISION_PATH.relative_to(WORKSPACE)}")
    print("- dashboard: web/index.html")
    print(f"- decision summary: {report['summary']['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
