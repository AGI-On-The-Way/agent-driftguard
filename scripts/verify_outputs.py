#!/usr/bin/env python3
"""Verify agent outputs against an evalset and write rollout outcome rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from feedback_kit import build_outcome_rows  # noqa: E402


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


def write_jsonl(path: Path, rows: list[dict[str, Any]], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing output file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evalset", required=True, type=Path, help="task evalset JSONL")
    parser.add_argument("--outputs", required=True, type=Path, help="agent outputs JSONL")
    parser.add_argument("--phase", required=True, choices=["baseline", "candidate"])
    parser.add_argument("--out", required=True, type=Path, help="rollout outcome JSONL to write")
    parser.add_argument("--run-id-prefix", default=None, help="default run_id prefix")
    parser.add_argument("--overwrite", action="store_true", help="replace --out if it exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rows = build_outcome_rows(
            evalset=read_jsonl(args.evalset),
            outputs=read_jsonl(args.outputs),
            phase=args.phase,
            run_id_prefix=args.run_id_prefix,
        )
        write_jsonl(args.out, rows, overwrite=args.overwrite)
    except (OSError, ValueError, TypeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    hits = sum(row["actual_pass"] for row in rows)
    print("Agent DriftGuard output verification complete")
    print(f"- phase: {args.phase}")
    print(f"- rows: {len(rows)}")
    print(f"- hit_rate: {hits}/{len(rows)}" if rows else "- hit_rate: no rows")
    print(f"- out: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
