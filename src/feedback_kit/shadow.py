"""Blind, unlabeled shadow comparisons for real agent changes.

This workflow is deliberately separate from the authoritative experiment gate:
it gathers blinded human preferences before a representative labeled holdout
exists, and it never mutates the production agent's active configuration.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .eventlog import ChainedEventLog
from .experiment import AgentRunner, json_hash


@dataclass(frozen=True)
class ShadowPaths:
    events: Path
    outputs: Path
    mapping: Path
    packet: Path
    integrity: Path

    @classmethod
    def for_dir(cls, out_dir: str | Path) -> "ShadowPaths":
        root = Path(out_dir)
        return cls(
            events=root / "shadow-events.jsonl",
            outputs=root / "role-outputs.jsonl",
            mapping=root / "blind-mapping.json",
            packet=root / "blind-adjudication.md",
            integrity=root / "integrity.json",
        )

    def all_paths(self) -> list[Path]:
        return [self.events, self.outputs, self.mapping, self.packet, self.integrity]


def run_blind_shadow_pilot(
    *,
    pilot_id: str,
    tasks: Iterable[Mapping[str, Any]],
    runner: AgentRunner,
    baseline_config: Mapping[str, Any],
    candidate_config: Mapping[str, Any],
    paths: ShadowPaths,
    seed: str | None = None,
) -> dict[str, Any]:
    """Run isolated control/candidate calls and build a blinded A/B packet."""

    normalized_id = pilot_id.strip()
    if not normalized_id:
        raise ValueError("pilot_id must not be empty")
    normalized_tasks = _normalize_tasks(tasks)
    _prepare_paths(paths)

    secret_seed = seed or secrets.token_hex(32)
    seed_commitment = _sha256_text(secret_seed)
    baseline = dict(baseline_config)
    candidate = dict(candidate_config)
    if json_hash(baseline) == json_hash(candidate):
        raise ValueError("baseline and candidate configs must differ")

    event_log = ChainedEventLog(paths.events)
    event_log.append(
        {
            "ev": "shadow_pilot_started",
            "ts": time.time(),
            "pilot_id": normalized_id,
            "task_count": len(normalized_tasks),
            "task_manifest_hash": json_hash(_task_manifest(normalized_tasks)),
            "baseline_config_hash": json_hash(baseline),
            "candidate_config_hash": json_hash(candidate),
            "seed_commitment": seed_commitment,
            "config_mode": "isolated_shadow",
        }
    )

    schedule, display_mapping = _build_schedule(normalized_tasks, secret_seed)
    event_log.append(
        {
            "ev": "shadow_schedule_locked",
            "ts": time.time(),
            "schedule_hash": json_hash(schedule),
            "mapping_hash": json_hash(display_mapping),
        }
    )

    configs = {"baseline": baseline, "candidate": candidate}
    results: dict[tuple[str, str], dict[str, Any]] = {}
    output_rows: list[dict[str, Any]] = []
    for position, item in enumerate(schedule, start=1):
        task = normalized_tasks[item["task_index"]]
        role = item["role"]
        event_log.append(
            {
                "ev": "shadow_call_started",
                "ts": time.time(),
                "position": position,
                "task_id": task["task_id"],
                "role": role,
                "config_hash": json_hash(configs[role]),
            }
        )
        result = dict(
            runner.run(
                task,
                phase="control" if role == "baseline" else "candidate",
                config=configs[role],
            )
        )
        if "output" not in result:
            raise ValueError(f"runner result missing output for task {task['task_id']!r}")
        row = {
            "task_id": task["task_id"],
            "display_name": task["display_name"],
            "role": role,
            "output": result["output"],
            "note": result.get("note"),
            "metadata": result.get("metadata", {}),
        }
        output_rows.append(row)
        results[(task["task_id"], role)] = row
        event_log.append(
            {
                "ev": "shadow_call_completed",
                "ts": time.time(),
                "position": position,
                "task_id": task["task_id"],
                "role": role,
                "output_hash": json_hash(result["output"]),
                "response_id": _response_id(row),
            }
        )

    mapping_document = {
        "schema_version": 1,
        "pilot_id": normalized_id,
        "seed": secret_seed,
        "seed_commitment": seed_commitment,
        "warning": "Keep sealed until blinded human adjudication is frozen.",
        "records": display_mapping,
    }
    packet = _build_packet(normalized_id, normalized_tasks, display_mapping, results)
    _write_jsonl(paths.outputs, output_rows)
    _write_json(paths.mapping, mapping_document)
    paths.packet.write_text(packet, encoding="utf-8")

    artifact_hashes = {
        "role_outputs_sha256": _sha256_file(paths.outputs),
        "blind_mapping_sha256": _sha256_file(paths.mapping),
        "blind_adjudication_sha256": _sha256_file(paths.packet),
    }
    event_log.append(
        {
            "ev": "shadow_pilot_completed",
            "ts": time.time(),
            "pilot_id": normalized_id,
            **artifact_hashes,
        }
    )
    chain = event_log.verify()
    response_ids = [value for value in (_response_id(row) for row in output_rows) if value]
    integrity = {
        "schema_version": 1,
        "pilot_id": normalized_id,
        "evidence_level": "blind_shadow_pilot_unadjudicated",
        "production_config_mutated": False,
        "task_count": len(normalized_tasks),
        "call_count": len(output_rows),
        "expected_call_count": len(normalized_tasks) * 2,
        "baseline_config_hash": json_hash(baseline),
        "candidate_config_hash": json_hash(candidate),
        "task_manifest_hash": json_hash(_task_manifest(normalized_tasks)),
        "seed_commitment": seed_commitment,
        "execution_order_counts": {
            "baseline_first": sum(
                1
                for index in range(0, len(schedule), 2)
                if schedule[index]["role"] == "baseline"
            ),
            "candidate_first": sum(
                1
                for index in range(0, len(schedule), 2)
                if schedule[index]["role"] == "candidate"
            ),
        },
        "response_id_count": len(response_ids),
        "unique_response_id_count": len(set(response_ids)),
        "event_chain": chain,
        "artifacts": artifact_hashes,
    }
    _write_json(paths.integrity, integrity)
    return integrity


def _normalize_tasks(tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(tasks, start=1):
        task_id = str(raw.get("task_id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        if not task_id or not display_name:
            raise ValueError(f"shadow task {index} requires task_id and display_name")
        if task_id in seen:
            raise ValueError(f"duplicate shadow task_id: {task_id}")
        seen.add(task_id)
        if "input" not in raw:
            raise ValueError(f"shadow task {task_id!r} requires input")
        if "check" in raw or "expected" in raw:
            raise ValueError("shadow tasks must not include labels or checks")
        normalized.append(
            {
                "task_id": task_id,
                "display_name": display_name,
                "kind": str(raw.get("kind") or "shadow_task"),
                "input": raw["input"],
                "source_sha256": raw.get("source_sha256"),
            }
        )
    if len(normalized) < 2:
        raise ValueError("a blind shadow pilot requires at least two tasks")
    return normalized


def _task_manifest(tasks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task["task_id"],
            "display_name": task["display_name"],
            "kind": task["kind"],
            "input_hash": json_hash(task["input"]),
            "source_sha256": task.get("source_sha256"),
        }
        for task in tasks
    ]


def _build_schedule(
    tasks: list[Mapping[str, Any]], seed: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(_sha256_text(seed))
    task_indices = list(range(len(tasks)))
    rng.shuffle(task_indices)
    baseline_first_count = (len(tasks) + 1) // 2
    first_roles = ["baseline"] * baseline_first_count + ["candidate"] * (
        len(tasks) - baseline_first_count
    )
    rng.shuffle(first_roles)

    schedule: list[dict[str, Any]] = []
    for task_index, first_role in zip(task_indices, first_roles):
        second_role = "candidate" if first_role == "baseline" else "baseline"
        schedule.extend(
            [
                {"task_index": task_index, "role": first_role},
                {"task_index": task_index, "role": second_role},
            ]
        )

    a_baseline_count = (len(tasks) + 1) // 2
    a_roles = ["baseline"] * a_baseline_count + ["candidate"] * (
        len(tasks) - a_baseline_count
    )
    rng.shuffle(a_roles)
    mapping: list[dict[str, Any]] = []
    for task, a_role in zip(tasks, a_roles):
        b_role = "candidate" if a_role == "baseline" else "baseline"
        mapping.append(
            {
                "task_id": task["task_id"],
                "display_name": task["display_name"],
                "A": a_role,
                "B": b_role,
            }
        )
    return schedule, mapping


def _build_packet(
    pilot_id: str,
    tasks: list[Mapping[str, Any]],
    mapping: list[Mapping[str, Any]],
    results: Mapping[tuple[str, str], Mapping[str, Any]],
) -> str:
    mapping_by_id = {str(item["task_id"]): item for item in mapping}
    lines = [
        f"# Blind Shadow Adjudication: {pilot_id}",
        "",
        "Choose `A`, `B`, `Tie`, or `Both fail` for reviewer usefulness. Judge:",
        "evidence fidelity and exact location, weakest-link precision, decision-support efficiency, "
        "and whether the output avoids invented facts, scores, or verdicts.",
        "",
        "Record one material error, if any. Do not open `blind-mapping.json` before all choices are frozen.",
        "",
    ]
    for index, task in enumerate(tasks, start=1):
        task_id = str(task["task_id"])
        item = mapping_by_id[task_id]
        lines.extend(
            [
                f"## {index}. {task['display_name']}",
                "",
                "**Choice:** [ ] A  [ ] B  [ ] Tie  [ ] Both fail",
                "",
                "**Material error / reason:**",
                "",
            ]
        )
        for side in ("A", "B"):
            role = str(item[side])
            output = results[(task_id, role)]["output"]
            lines.extend([f"### Output {side}", "", _render_output(output), ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_output(output: Any) -> str:
    if isinstance(output, str):
        return output.strip()
    return "```json\n" + json.dumps(output, ensure_ascii=False, indent=2) + "\n```"


def _prepare_paths(paths: ShadowPaths) -> None:
    existing = [path for path in paths.all_paths() if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite existing shadow evidence: "
            + ", ".join(str(path) for path in existing)
        )
    for path in paths.all_paths():
        path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _response_id(row: Mapping[str, Any]) -> str | None:
    metadata = row.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("response_id")
    return str(value) if value else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
