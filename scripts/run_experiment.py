#!/usr/bin/env python3
"""Run an authoritative baseline-lock-apply-candidate agent experiment."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from feedback_kit import (  # noqa: E402
    CommandAgentRunner,
    ExperimentPaths,
    ExperimentPolicy,
    JsonFileConfigAdapter,
    format_p_value,
    run_experiment,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row") from exc
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def initialize_config(source: Path, target: Path, *, overwrite: bool) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite active config: {target}")
    value = read_json(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def viewer_paths(out_dir: Path) -> list[Path]:
    return [out_dir / "index.html", out_dir / "app.js", out_dir / "styles.css"]


def publish_viewer(out_dir: Path, *, command: str) -> None:
    html = (WORKSPACE / "web" / "index.html").read_text(encoding="utf-8")
    html = html.replace("../artifacts/dashboard-data.js", "./dashboard-data.js")
    html = html.replace("../artifacts/drift-report.json", "./drift-report.json")
    html = html.replace("../artifacts/decision.md", "./decision.md")
    html = html.replace("../artifacts/demo-ledger.jsonl", "./ledger.jsonl")
    html = html.replace("../artifacts/comparison-ledger.jsonl", "./comparison-ledger.jsonl")
    html = html.replace("../artifacts/control-outputs.jsonl", "./control-outputs.jsonl")
    html = html.replace("../artifacts/proposal-log.jsonl", "./proposal-log.jsonl")
    out_dir.joinpath("index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(WORKSPACE / "web" / "app.js", out_dir / "app.js")
    shutil.copyfile(WORKSPACE / "web" / "styles.css", out_dir / "styles.css")
    dashboard_path = out_dir / "dashboard-data.js"
    existing = dashboard_path.read_text(encoding="utf-8").splitlines()
    existing = [
        line
        for line in existing
        if not line.startswith("window.DRIFTGUARD_DATA.command = ")
    ]
    existing.append(
        "window.DRIFTGUARD_DATA.command = "
        + json.dumps(command, ensure_ascii=False)
        + ";"
    )
    dashboard_path.write_text("\n".join(existing) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--evalset", required=True, type=Path)
    parser.add_argument(
        "--holdout-evalset",
        type=Path,
        help="disjoint confirmation tasks used only after proposal lock",
    )
    parser.add_argument("--runner-command", required=True, help="command parsed without a shell")
    parser.add_argument("--runner-timeout", type=float, default=120.0)
    parser.add_argument("--out-dir", required=True, type=Path)
    config = parser.add_mutually_exclusive_group(required=True)
    config.add_argument("--config", type=Path, help="existing active JSON config to gate")
    config.add_argument(
        "--initialize-config",
        type=Path,
        help="copy baseline JSON into <out-dir>/active-config.json before running",
    )
    parser.add_argument("--min-metric-samples", type=int, default=20)
    parser.add_argument(
        "--min-development-samples",
        type=int,
        default=None,
        help="optional development baseline threshold; paired gate still uses --min-metric-samples",
    )
    parser.add_argument("--min-absolute-delta", type=float, default=0.05)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--anchor-kind", default="anchor_task")
    parser.add_argument("--health-window", type=int, default=None)
    parser.add_argument("--hit-drop-tol", type=float, default=0.1)
    parser.add_argument("--drift-gap-tol", type=float, default=0.05)
    parser.add_argument(
        "--comparison-mode",
        choices=("interleaved_control", "sequential"),
        default="interleaved_control",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = ExperimentPaths.for_dir(args.out_dir)
        preexisting = [path for path in paths.all_paths() + viewer_paths(args.out_dir) if path.exists()]
        if preexisting and not args.overwrite:
            names = ", ".join(str(path) for path in preexisting)
            raise FileExistsError(f"refusing to overwrite existing experiment artifacts: {names}")
        if args.initialize_config:
            config_path = args.out_dir / "active-config.json"
            initialize_config(args.initialize_config, config_path, overwrite=args.overwrite)
        else:
            config_path = args.config
        assert config_path is not None

        report = run_experiment(
            proposal=read_json(args.proposal),
            evalset=read_jsonl(args.evalset),
            holdout_evalset=(
                read_jsonl(args.holdout_evalset) if args.holdout_evalset else None
            ),
            runner=CommandAgentRunner(
                args.runner_command,
                timeout_seconds=args.runner_timeout,
            ),
            config=JsonFileConfigAdapter(config_path),
            paths=paths,
            policy=ExperimentPolicy(
                min_metric_samples=args.min_metric_samples,
                min_development_samples=args.min_development_samples,
                min_absolute_delta=args.min_absolute_delta,
                alpha=args.alpha,
                bootstrap_samples=args.bootstrap_samples,
                anchor_kind=args.anchor_kind or None,
                health_window=args.health_window,
                hit_drop_tol=args.hit_drop_tol,
                drift_gap_tol=args.drift_gap_tol,
                comparison_mode=args.comparison_mode,
            ),
            overwrite=args.overwrite,
        )
        reproduced = [sys.executable, str(Path(__file__).relative_to(WORKSPACE)), *(argv or sys.argv[1:])]
        if "--overwrite" not in reproduced:
            reproduced.append("--overwrite")
        publish_viewer(args.out_dir, command=shlex.join(reproduced))
    except (OSError, ValueError, TypeError, RuntimeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    gate = report["effect_gate"]
    print("Agent DriftGuard authoritative experiment complete")
    print(f"- evidence: {report['experiment']['evidence_level']}")
    print(f"- comparison: {report['comparison']['mode']}")
    print(f"- decision: {report['summary']['decision']}")
    print(f"- paired tasks: {gate['paired_n']}")
    print(f"- actual delta: {gate['actual_delta']:+.3f}")
    print(f"- exact p-value: {format_p_value(gate['exact_p_value'])}")
    print(f"- config action: {report['policy_execution']['action']}")
    print(f"- active config: {config_path}")
    print(f"- report: {paths.report}")
    print(f"- experiment log: {paths.experiment_log}")
    print(f"- dashboard: {args.out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
