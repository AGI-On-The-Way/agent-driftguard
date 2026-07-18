"""Build opaque, verifier-labeled evalsets from private DOCX reviews."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
VALID_SPLITS = ("development", "holdout")


def extract_docx_body_text(path: str | Path) -> str:
    """Extract body text without comments, metadata, or external relationships."""

    source = Path(path)
    if source.suffix.lower() != ".docx":
        raise ValueError(f"source document must be .docx: {source}")
    try:
        with zipfile.ZipFile(source) as archive:
            document_xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"unable to read DOCX body: {source}") from exc

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid DOCX document XML: {source}") from exc

    blocks: list[str] = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD_NS}t"))
        normalized = " ".join(text.split())
        if normalized:
            blocks.append(normalized)
    result = "\n".join(blocks).strip()
    if len(result) < 200:
        raise ValueError(f"DOCX body is too short for representative review: {source}")
    return result


def parse_review_label(markdown: str) -> dict[str, Any]:
    """Read the final quality score and disposition from a scoring record."""

    score_match = re.search(
        r"^\|\s*\*\*Weighted Total\*\*\s*\|.*?\|\s*\*\*([0-9]+(?:\.[0-9]+)?)\*\*",
        markdown,
        flags=re.MULTILINE,
    )
    if not score_match:
        raise ValueError("scoring record missing Weighted Total")
    score = float(score_match.group(1))
    if not 0.0 <= score <= 10.0:
        raise ValueError(f"quality score outside [0, 10]: {score}")

    disposition_match = re.search(
        r"^\*\*Disposition:\s*(.+?)(?:\*\*|\s+—|$)",
        markdown,
        flags=re.MULTILINE,
    )
    if not disposition_match:
        raise ValueError("scoring record missing Disposition")
    raw_disposition = disposition_match.group(1).strip()
    return {
        "quality_score": score,
        "quality_band": quality_band(score),
        "disposition": normalize_disposition(raw_disposition),
    }


def quality_band(score: float) -> str:
    if score >= 6.5:
        return "strong"
    if score >= 5.5:
        return "adequate"
    if score >= 5.0:
        return "marginal"
    return "weak"


def normalize_disposition(value: str) -> str:
    normalized = " ".join(value.lower().replace("_", " ").split())
    if "ready to upload" in normalized or "ready to publish" in normalized:
        return "ready"
    if "consult with reviewer" in normalized:
        return "consult"
    if "minor revision" in normalized or "major revision" in normalized:
        return "revise"
    if "no need to publish" in normalized:
        return "do_not_publish"
    raise ValueError(f"unsupported disposition: {value!r}")


def build_private_review_benchmark(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Build development/holdout evalsets and a private source audit manifest."""

    benchmark_id = str(manifest.get("benchmark_id") or "").strip()
    if not benchmark_id:
        raise ValueError("manifest missing benchmark_id")
    exclusions = _load_holdout_exclusions(manifest)

    seen_sources: set[Path] = set()
    seen_labels: set[Path] = set()
    seen_source_hashes: set[str] = set()
    evalsets: dict[str, list[dict[str, Any]]] = {}
    audit_records: list[dict[str, Any]] = []
    development_history_source_overlap = 0
    development_history_label_overlap = 0

    for split in VALID_SPLITS:
        raw_records = manifest.get(split)
        if not isinstance(raw_records, list) or not raw_records:
            raise ValueError(f"manifest {split!r} must be a non-empty list")
        tasks: list[dict[str, Any]] = []
        for index, raw_record in enumerate(raw_records, start=1):
            if not isinstance(raw_record, Mapping):
                raise TypeError(f"{split} record {index} must be an object")
            source = _required_path(raw_record, "source_docx")
            label_path = _required_path(raw_record, "scoring_record")
            if source in seen_sources:
                raise ValueError(f"duplicate source document across benchmark: {source}")
            if label_path in seen_labels:
                raise ValueError(f"duplicate scoring record across benchmark: {label_path}")
            seen_sources.add(source)
            seen_labels.add(label_path)

            source_hash = _sha256_bytes(source.read_bytes())
            if source_hash in seen_source_hashes:
                raise ValueError("duplicate source document content across benchmark")
            seen_source_hashes.add(source_hash)
            label_bytes = label_path.read_bytes()
            label_hash = _sha256_bytes(label_bytes)
            if split == "holdout":
                if source_hash in exclusions["source_hashes"]:
                    raise ValueError(
                        "holdout source document already appeared in an excluded benchmark"
                    )
                if label_hash in exclusions["label_hashes"]:
                    raise ValueError(
                        "holdout scoring record already appeared in an excluded benchmark"
                    )
            else:
                development_history_source_overlap += int(
                    source_hash in exclusions["source_hashes"]
                )
                development_history_label_overlap += int(
                    label_hash in exclusions["label_hashes"]
                )

            report_text = extract_docx_body_text(source)
            label_text = label_bytes.decode("utf-8")
            try:
                label = parse_review_label(label_text)
            except ValueError as exc:
                raise ValueError(f"invalid scoring record {label_path}: {exc}") from exc
            task_id = _opaque_task_id(benchmark_id, split, source_hash)
            tasks.append(
                {
                    "task_id": task_id,
                    "kind": "analyst_review",
                    "input": {
                        "task_type": "report_review",
                        "report_text": report_text,
                    },
                    "check": [
                        {
                            "type": "json_path_equals",
                            "path": "quality_band",
                            "value": label["quality_band"],
                        },
                        {
                            "type": "json_path_equals",
                            "path": "disposition",
                            "value": label["disposition"],
                        },
                    ],
                    "miss_reason": "human_review_decision_mismatch",
                }
            )
            audit_records.append(
                {
                    "split": split,
                    "task_id": task_id,
                    "source_docx": str(source),
                    "scoring_record": str(label_path),
                    "source_sha256": source_hash,
                    "scoring_record_sha256": label_hash,
                    **label,
                }
            )
        tasks.extend(_anchor_tasks(benchmark_id, split))
        evalsets[split] = tasks

    development_ids = {task["task_id"] for task in evalsets["development"]}
    holdout_ids = {task["task_id"] for task in evalsets["holdout"]}
    if development_ids & holdout_ids:
        raise RuntimeError("opaque task ID collision across development and holdout")

    audit = {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "source_visibility": "private",
        "development_records": len(manifest["development"]),
        "holdout_records": len(manifest["holdout"]),
        "development_tasks": len(evalsets["development"]),
        "holdout_tasks": len(evalsets["holdout"]),
        "task_id_overlap": [],
        "holdout_exclusion_manifests": exclusions["manifests"],
        "excluded_source_hash_count": len(exclusions["source_hashes"]),
        "excluded_scoring_record_hash_count": len(exclusions["label_hashes"]),
        "development_history_source_overlap_count": development_history_source_overlap,
        "development_history_scoring_record_overlap_count": development_history_label_overlap,
        "records": audit_records,
    }
    return evalsets, audit


def _load_holdout_exclusions(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_paths = manifest.get("exclude_holdout_benchmark_manifests", [])
    if not isinstance(raw_paths, list):
        raise TypeError("exclude_holdout_benchmark_manifests must be a list")

    paths: list[str] = []
    seen_paths: set[Path] = set()
    source_hashes: set[str] = set()
    label_hashes: set[str] = set()
    for index, raw_path in enumerate(raw_paths, start=1):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(
                f"exclude_holdout_benchmark_manifests item {index} must be a path"
            )
        path = Path(raw_path).expanduser().resolve()
        if path in seen_paths:
            raise ValueError(f"duplicate holdout exclusion manifest: {path}")
        seen_paths.add(path)
        if not path.is_file():
            raise ValueError(f"holdout exclusion manifest does not exist: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid holdout exclusion manifest JSON: {path}") from exc
        if not isinstance(value, Mapping) or not isinstance(value.get("records"), list):
            raise ValueError(f"holdout exclusion manifest missing records list: {path}")
        for record_index, record in enumerate(value["records"], start=1):
            if not isinstance(record, Mapping):
                raise ValueError(
                    f"holdout exclusion manifest {path} record {record_index} must be an object"
                )
            source_hashes.add(
                _required_sha256(record, "source_sha256", path=path, index=record_index)
            )
            label_hashes.add(
                _required_sha256(
                    record,
                    "scoring_record_sha256",
                    path=path,
                    index=record_index,
                )
            )
        paths.append(str(path))
    return {
        "manifests": paths,
        "source_hashes": source_hashes,
        "label_hashes": label_hashes,
    }


def _required_sha256(
    record: Mapping[str, Any],
    key: str,
    *,
    path: Path,
    index: int,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(
            f"holdout exclusion manifest {path} record {index} has invalid {key}"
        )
    return value


def _anchor_tasks(benchmark_id: str, split: str) -> list[dict[str, Any]]:
    count = 2 if split == "development" else 4
    tasks: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        token = hashlib.sha256(
            f"{benchmark_id}\0{split}\0anchor\0{index}".encode("utf-8")
        ).hexdigest()[:16]
        tasks.append(
            {
                "task_id": f"{split[:3]}-anchor-{token}",
                "kind": "anchor_task",
                "input": {"task_type": "anchor", "anchor_token": token},
                "check": {
                    "type": "json_path_equals",
                    "path": "anchor_token",
                    "value": token,
                },
                "miss_reason": "anchor_contract_failed",
            }
        )
    return tasks


def _opaque_task_id(benchmark_id: str, split: str, source_hash: str) -> str:
    digest = hashlib.sha256(
        f"{benchmark_id}\0{split}\0{source_hash}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{split[:3]}-review-{digest}"


def _required_path(record: Mapping[str, Any], key: str) -> Path:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"benchmark record missing {key}")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"benchmark source does not exist: {path}")
    return path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    destination = Path(path)
    destination.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
