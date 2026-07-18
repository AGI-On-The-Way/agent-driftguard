#!/usr/bin/env python3
"""Build private report-review development and holdout evalsets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit.private_eval import build_private_review_benchmark, write_jsonl  # noqa: E402


OUTPUTS = (
    "development-evalset.jsonl",
    "holdout-evalset.jsonl",
    "benchmark-manifest.json",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    destinations = [args.out_dir / name for name in OUTPUTS]
    existing = [path for path in destinations if path.exists()]
    if existing and not args.overwrite:
        print(
            "error: refusing to overwrite private benchmark outputs: "
            + ", ".join(str(path) for path in existing),
            file=sys.stderr,
        )
        return 1
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError("source manifest must be a JSON object")
        evalsets, audit = build_private_review_benchmark(manifest)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(destinations[0], evalsets["development"])
        write_jsonl(destinations[1], evalsets["holdout"])
        destinations[2].write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Private review benchmark built")
    print(f"- development records: {audit['development_records']}")
    print(f"- holdout records: {audit['holdout_records']}")
    print(f"- excluded historical sources: {audit['excluded_source_hash_count']}")
    print(
        "- development historical overlap: "
        f"{audit['development_history_source_overlap_count']}"
    )
    print(f"- output: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
