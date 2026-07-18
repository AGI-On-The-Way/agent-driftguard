#!/usr/bin/env python3
"""Run a private, unlabeled, blind A/B shadow pilot over local DOCX reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from feedback_kit import (  # noqa: E402
    CommandAgentRunner,
    ShadowPaths,
    extract_docx_body_text,
    run_blind_shadow_pilot,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def build_tasks(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    pilot_id = str(manifest.get("pilot_id") or "").strip()
    records = manifest.get("records")
    if not pilot_id or not isinstance(records, list) or len(records) < 2:
        raise ValueError("source manifest requires pilot_id and at least two records")
    tasks: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise TypeError(f"source manifest record {index} must be an object")
        source = Path(str(record.get("source_docx") or "")).expanduser().resolve()
        display_name = str(record.get("display_name") or "").strip()
        if not source.is_file() or not display_name:
            raise ValueError(f"source manifest record {index} is incomplete")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        declared_hash = record.get("source_sha256")
        if declared_hash and declared_hash != source_hash:
            raise ValueError(f"source hash mismatch: {source}")
        task_id = hashlib.sha256(
            f"{pilot_id}\0{source_hash}".encode("utf-8")
        ).hexdigest()[:20]
        tasks.append(
            {
                "task_id": task_id,
                "display_name": display_name,
                "kind": "analyst_review_prep",
                "input": {"report_text": extract_docx_body_text(source)},
                "source_sha256": source_hash,
            }
        )
    return tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--baseline-config", required=True, type=Path)
    parser.add_argument("--candidate-config", required=True, type=Path)
    parser.add_argument("--runner-command", required=True)
    parser.add_argument("--runner-timeout", type=float, default=240.0)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seed", help="optional private deterministic seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = read_json(args.manifest)
        report = run_blind_shadow_pilot(
            pilot_id=str(manifest["pilot_id"]),
            tasks=build_tasks(manifest),
            runner=CommandAgentRunner(
                shlex.split(args.runner_command),
                timeout_seconds=args.runner_timeout,
            ),
            baseline_config=read_json(args.baseline_config),
            candidate_config=read_json(args.candidate_config),
            paths=ShadowPaths.for_dir(args.out_dir),
            seed=args.seed,
        )
    except (OSError, ValueError, TypeError, RuntimeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Agent DriftGuard blind shadow pilot complete")
    print(f"- evidence: {report['evidence_level']}")
    print(f"- tasks: {report['task_count']}")
    print(f"- calls: {report['call_count']}")
    print(f"- production config mutated: {report['production_config_mutated']}")
    print(f"- event chain valid: {report['event_chain']['valid']}")
    print(f"- adjudication packet: {args.out_dir / 'blind-adjudication.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
