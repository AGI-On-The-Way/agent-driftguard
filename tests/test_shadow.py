"""Offline checks for blinded, unlabeled shadow comparisons."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit import ShadowPaths, run_blind_shadow_pilot  # noqa: E402


class FakeRunner:
    name = "fake-shadow-runner"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def run(
        self,
        task: Mapping[str, Any],
        *,
        phase: str,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"task_id": str(task["task_id"]), "phase": phase})
        return {
            "output": f"{config['marker']}:{task['input']['report_text']}",
            "metadata": {"response_id": f"r-{len(self.calls)}"},
        }


def tasks() -> list[dict[str, Any]]:
    return [
        {
            "task_id": f"opaque-{index}",
            "display_name": f"Report {index}",
            "kind": "review",
            "input": {"report_text": f"body-{index}"},
            "source_sha256": str(index) * 64,
        }
        for index in range(1, 6)
    ]


def test_blind_shadow_is_balanced_and_tamper_evident(tmp: Path) -> None:
    runner = FakeRunner()
    paths = ShadowPaths.for_dir(tmp / "pilot")
    report = run_blind_shadow_pilot(
        pilot_id="pilot-v1",
        tasks=tasks(),
        runner=runner,
        baseline_config={"marker": "one"},
        candidate_config={"marker": "two"},
        paths=paths,
        seed="fixed-private-seed",
    )

    assert report["production_config_mutated"] is False
    assert report["call_count"] == 10
    assert report["unique_response_id_count"] == 10
    assert report["event_chain"]["valid"] is True
    assert abs(
        report["execution_order_counts"]["baseline_first"]
        - report["execution_order_counts"]["candidate_first"]
    ) <= 1

    packet = paths.packet.read_text(encoding="utf-8")
    mapping = json.loads(paths.mapping.read_text(encoding="utf-8"))
    assert "baseline" not in packet
    assert "candidate" not in packet
    assert len(mapping["records"]) == 5
    assert all({item["A"], item["B"]} == {"baseline", "candidate"} for item in mapping["records"])

    try:
        run_blind_shadow_pilot(
            pilot_id="pilot-v1",
            tasks=tasks(),
            runner=FakeRunner(),
            baseline_config={"marker": "one"},
            candidate_config={"marker": "two"},
            paths=paths,
        )
        raised = False
    except FileExistsError:
        raised = True
    assert raised


def test_shadow_rejects_labels(tmp: Path) -> None:
    labeled = tasks()
    labeled[0]["check"] = {"value": "leak"}
    try:
        run_blind_shadow_pilot(
            pilot_id="pilot-v1",
            tasks=labeled,
            runner=FakeRunner(),
            baseline_config={"marker": "one"},
            candidate_config={"marker": "two"},
            paths=ShadowPaths.for_dir(tmp / "labeled"),
        )
        raised = False
    except ValueError as exc:
        raised = "must not include labels" in str(exc)
    assert raised


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_blind_shadow_is_balanced_and_tamper_evident(root)
        test_shadow_rejects_labels(root)
    print("Blind shadow pilot checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
