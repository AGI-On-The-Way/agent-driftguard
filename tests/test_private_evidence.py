"""Checks for private benchmark construction and sealed evidence export.

Run: python3 tests/test_private_evidence.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from feedback_kit import (  # noqa: E402
    aggregate_response_metadata,
    build_private_review_benchmark,
    build_sealed_evidence,
    extract_docx_body_text,
    normalize_disposition,
    parse_review_label,
    quality_band,
    sealed_evidence_hash,
)


def make_docx(path: Path, text: str) -> None:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{part}</w:t></w:r></w:p>"
        for part in escaped.split("\n")
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def make_label(path: Path, score: float, disposition: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Private Company - Scoring Record",
                "",
                "| Dimension | Weight | Score | Notes |",
                "|---|---:|---:|---|",
                "| Logic | 50% | 7.0 | note |",
                "| Depth | 50% | 6.0 | note |",
                f"| **Weighted Total** | **100%** | **{score}** | final |",
                "",
                f"**Disposition: {disposition}**",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def private_manifest(tmp: Path) -> dict:
    records: dict[str, list[dict[str, str]]] = {"development": [], "holdout": []}
    for split, count in (("development", 2), ("holdout", 2)):
        for index in range(1, count + 1):
            source = tmp / f"SECRET_TICKER_{split}_{index}.docx"
            label = tmp / f"SECRET_ANALYST_{split}_{index}.md"
            make_docx(
                source,
                (
                    f"Representative private report {split} {index}.\n"
                    + "Causal chain, quantitative assumptions, scenario analysis, and publication form. "
                    * 8
                ),
            )
            score = 7.0 if index == 1 else 5.25
            disposition = "Ok - Ready to Upload" if index == 1 else "No Need to Publish"
            make_label(label, score, disposition)
            records[split].append(
                {"source_docx": str(source), "scoring_record": str(label)}
            )
    return {"benchmark_id": "private-review-test", **records}


def test_review_label_and_docx_parsing(tmp: Path) -> None:
    docx = tmp / "sample.docx"
    make_docx(docx, "Alpha paragraph " * 20 + "\nSecond paragraph")
    text = extract_docx_body_text(docx)
    assert "Alpha paragraph" in text and "Second paragraph" in text

    label = tmp / "score.md"
    make_label(label, 6.25, "Ok - Consult With Reviewer")
    parsed = parse_review_label(label.read_text(encoding="utf-8"))
    assert parsed == {
        "quality_score": 6.25,
        "quality_band": "adequate",
        "disposition": "consult",
    }
    assert quality_band(6.5) == "strong"
    assert quality_band(5.5) == "adequate"
    assert quality_band(5.0) == "marginal"
    assert quality_band(4.99) == "weak"
    assert normalize_disposition("Ok - Minor Revision") == "revise"


def test_private_benchmark_uses_opaque_disjoint_ids(tmp: Path) -> None:
    manifest = private_manifest(tmp)
    evalsets, audit = build_private_review_benchmark(manifest)
    development = evalsets["development"]
    holdout = evalsets["holdout"]
    assert len(development) == 4  # two reports + two anchors
    assert len(holdout) == 6  # two reports + four anchors
    dev_ids = {task["task_id"] for task in development}
    holdout_ids = {task["task_id"] for task in holdout}
    assert not dev_ids & holdout_ids
    assert all("SECRET" not in task_id for task_id in dev_ids | holdout_ids)
    measured = [task for task in development + holdout if task["kind"] == "analyst_review"]
    assert all(len(task["check"]) == 2 for task in measured)
    assert {check["path"] for task in measured for check in task["check"]} == {
        "quality_band",
        "disposition",
    }
    assert audit["task_id_overlap"] == []

    duplicate = copy.deepcopy(manifest)
    duplicate["holdout"][0] = dict(duplicate["development"][0])
    try:
        build_private_review_benchmark(duplicate)
        raised = False
    except ValueError as exc:
        raised = "duplicate source" in str(exc)
    assert raised


def test_followup_holdout_rejects_exposed_hashes(tmp: Path) -> None:
    first = private_manifest(tmp)
    _, first_audit = build_private_review_benchmark(first)
    history = tmp / "first-benchmark-manifest.json"
    history.write_text(json.dumps(first_audit), encoding="utf-8")

    fresh_holdout = []
    for index, score in enumerate((7.25, 5.75), start=1):
        source = tmp / f"fresh-source-{index}.docx"
        label = tmp / f"fresh-label-{index}.md"
        make_docx(
            source,
            f"Fresh prospective report {index}. "
            + "New causal evidence, scenarios, and independent verification. " * 8,
        )
        make_label(
            label,
            score,
            "Ok - Ready to Upload" if index == 1 else "Ok - Consult With Reviewer",
        )
        fresh_holdout.append(
            {"source_docx": str(source), "scoring_record": str(label)}
        )

    followup = {
        "benchmark_id": "private-review-followup",
        "exclude_holdout_benchmark_manifests": [str(history)],
        "development": copy.deepcopy(first["holdout"]),
        "holdout": fresh_holdout,
    }
    _, audit = build_private_review_benchmark(followup)
    assert audit["excluded_source_hash_count"] == 4
    assert audit["development_history_source_overlap_count"] == 2
    assert audit["development_history_scoring_record_overlap_count"] == 2

    reused_source = copy.deepcopy(followup)
    reused_source["holdout"][0] = copy.deepcopy(first["development"][0])
    try:
        build_private_review_benchmark(reused_source)
        source_raised = False
    except ValueError as exc:
        source_raised = "holdout source document already appeared" in str(exc)
    assert source_raised

    reused_label = copy.deepcopy(followup)
    reused_label["holdout"][0]["scoring_record"] = first["development"][0][
        "scoring_record"
    ]
    try:
        build_private_review_benchmark(reused_label)
        label_raised = False
    except ValueError as exc:
        label_raised = "holdout scoring record already appeared" in str(exc)
    assert label_raised

    malformed_history = tmp / "malformed-history.json"
    malformed_history.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_sha256": "not-a-hash",
                        "scoring_record_sha256": "b" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    malformed = copy.deepcopy(followup)
    malformed["exclude_holdout_benchmark_manifests"] = [str(malformed_history)]
    try:
        build_private_review_benchmark(malformed)
        malformed_raised = False
    except ValueError as exc:
        malformed_raised = "invalid source_sha256" in str(exc)
    assert malformed_raised


def write_output_files(run_dir: Path) -> None:
    for phase, filename, count in (
        ("baseline", "baseline-outputs.jsonl", 2),
        ("control", "control-outputs.jsonl", 3),
        ("candidate", "candidate-outputs.jsonl", 3),
    ):
        rows = []
        for index in range(count):
            rows.append(
                {
                    "task_id": f"SECRET_TICKER_{phase}_{index}",
                    "output": {"SECRET_REPORT_TEXT": "must never leave private run"},
                    "note": "SECRET_ANALYST_NAME",
                    "metadata": {
                        "response_id": f"response-{phase}-{index}",
                        "requested_model": "test-model",
                        "response_model": "test-model",
                        "stop_reason": "end_turn",
                        "duration_ms": 10.5,
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            )
        (run_dir / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )


def test_sealed_evidence_is_whitelist_only(tmp: Path) -> None:
    report = json.loads(
        (ROOT / "artifacts/deepseek-holdout-replication-run/drift-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["artifacts"]["private_source"] = "/private/SECRET_TICKER/report.docx"
    report["decision"]["reasons"].append("SECRET_ANALYST_NAME said so")
    write_output_files(tmp)
    metadata = aggregate_response_metadata(tmp)
    sealed = build_sealed_evidence(
        report,
        benchmark_id="representative-review-v1",
        source_report_sha256="a" * 64,
        response_metadata=metadata,
    )
    encoded = json.dumps(sealed, sort_keys=True)
    for forbidden in (
        "SECRET_TICKER",
        "SECRET_ANALYST",
        "SECRET_REPORT_TEXT",
        "/private/",
        "system_prompt",
        "expected",
    ):
        assert forbidden not in encoded
    assert sealed["visibility"]["public_reproducibility"] is False
    assert sealed["comparison"]["schedule_withheld"] is True
    assert sealed["response_metadata"]["responses"] == 8
    assert sealed["response_metadata"]["unique_response_ids"] == 8
    assert len(sealed_evidence_hash(sealed)) == 64

    invalid = copy.deepcopy(report)
    invalid["integrity"]["ledger"]["valid"] = False
    try:
        build_sealed_evidence(
            invalid,
            benchmark_id="representative-review-v1",
            source_report_sha256="a" * 64,
            response_metadata=metadata,
        )
        raised = False
    except ValueError as exc:
        raised = "every evidence chain" in str(exc)
    assert raised


def test_private_builder_cli(tmp: Path) -> None:
    manifest = private_manifest(tmp)
    manifest_path = tmp / "source-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out_dir = tmp / "benchmark"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_private_review_evalset.py",
            "--manifest",
            str(manifest_path),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Private review benchmark built" in completed.stdout
    assert (out_dir / "development-evalset.jsonl").is_file()
    assert (out_dir / "holdout-evalset.jsonl").is_file()
    assert (out_dir / "benchmark-manifest.json").is_file()


def test_sealed_export_cli_writes_file_digest(tmp: Path) -> None:
    run_dir = tmp / "private-run"
    run_dir.mkdir()
    source_report = ROOT / "artifacts/deepseek-holdout-replication-run/drift-report.json"
    (run_dir / "drift-report.json").write_text(
        source_report.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    write_output_files(run_dir)
    out = tmp / "public" / "evidence-summary.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_sealed_evidence.py",
            "--run-dir",
            str(run_dir),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Sealed evidence exported" in completed.stdout
    expected = hashlib.sha256(out.read_bytes()).hexdigest()
    assert out.with_suffix(".json.sha256").read_text(encoding="ascii") == (
        f"{expected}  evidence-summary.json\n"
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        test_review_label_and_docx_parsing(tmp)
        test_private_benchmark_uses_opaque_disjoint_ids(tmp)
        test_followup_holdout_rejects_exposed_hashes(tmp)
        test_sealed_evidence_is_whitelist_only(tmp)
        test_private_builder_cli(tmp)
        test_sealed_export_cli_writes_file_digest(tmp)
    print("private evidence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
