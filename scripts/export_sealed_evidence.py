#!/usr/bin/env python3
"""Export a whitelist-only aggregate from a private authoritative run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit.sealed_evidence import (  # noqa: E402
    aggregate_response_metadata,
    build_sealed_evidence,
    sha256_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--benchmark-id",
        default="private-representative-holdout",
        help="non-sensitive public identifier",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    digest_path = args.out.with_suffix(args.out.suffix + ".sha256")
    existing = [path for path in (args.out, digest_path) if path.exists()]
    if existing and not args.overwrite:
        print(
            "error: refusing to overwrite sealed evidence: "
            + ", ".join(str(path) for path in existing),
            file=sys.stderr,
        )
        return 1
    report_path = args.run_dir / "drift-report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise TypeError("drift report must be a JSON object")
        sealed = build_sealed_evidence(
            report,
            benchmark_id=args.benchmark_id,
            source_report_sha256=sha256_file(report_path),
            response_metadata=aggregate_response_metadata(args.run_dir),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(sealed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        digest = sha256_file(args.out)
        digest_path.write_text(f"{digest}  {args.out.name}\n", encoding="ascii")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Sealed evidence exported")
    print(f"- summary: {args.out}")
    print(f"- sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
