"""Machine-verifiable evalset checks for real rollout evidence.

Rollout rows are the stable evidence format, but real teams should not hand
write `actual_pass`. This module verifies agent outputs against a small,
domain-neutral evalset contract and produces rollout rows automatically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


MISSING_OUTPUT_REASON = "missing_output"


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def build_outcome_rows(
    *,
    evalset: Iterable[Mapping[str, Any]],
    outputs: Iterable[Mapping[str, Any]],
    phase: str,
    run_id_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Verify outputs and return normalized rows for `evaluate_rollout`.

    Required task fields: `task_id`, `kind`, and `check`.
    Required output fields: `task_id` and `output`.
    """

    tasks = _normalize_evalset(evalset)
    output_by_task = _normalize_outputs(outputs)
    rows: list[dict[str, Any]] = []
    prefix = run_id_prefix or phase
    for task in tasks:
        task_id = task["task_id"]
        output = output_by_task.get(task_id)
        if output is None:
            result = CheckResult(False, MISSING_OUTPUT_REASON)
            output_note = ""
            prob = None
            run_id = f"{prefix}-{task_id}"
        else:
            result = verify_task_output(task, output)
            output_note = str(output.get("note", ""))
            prob = output.get("prob")
            run_id = str(output.get("run_id") or f"{prefix}-{task_id}")
        note_parts = [str(task.get("note", "")), output_note]
        note = " | ".join(part for part in note_parts if part)
        if result.reason and not result.passed:
            note = f"{note} | {result.reason}" if note else result.reason
        row = {
            "task_id": task_id,
            "run_id": run_id,
            "kind": task["kind"],
            "phase": phase,
            "machine_verifiable": True,
            "actual_pass": result.passed,
            "miss_reason": None if result.passed else task.get("miss_reason") or result.reason,
            "note": note,
        }
        if prob is not None:
            row["prob"] = _validate_prob(prob, task_id=task_id)
        rows.append(row)
    return rows


def verify_task_output(
    task: Mapping[str, Any],
    output_row: Mapping[str, Any],
) -> CheckResult:
    checks = task.get("check")
    if checks is None:
        raise ValueError(f"task {task.get('task_id')!r} missing required field 'check'")
    if isinstance(checks, Mapping):
        check_list = [checks]
    elif isinstance(checks, list):
        check_list = checks
    else:
        raise TypeError(f"task {task.get('task_id')!r} field 'check' must be object or list")

    output_value = output_row.get("output")
    for check in check_list:
        result = _run_check(check, output_value)
        if not result.passed:
            return result
    return CheckResult(True)


def _normalize_evalset(evalset: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, task in enumerate(evalset, start=1):
        item = dict(task)
        for field_name in ("task_id", "kind", "check"):
            if field_name not in item:
                raise ValueError(f"evalset row {idx} missing required field {field_name!r}")
        task_id = str(item["task_id"])
        if task_id in seen:
            raise ValueError(f"duplicate task_id {task_id!r} in evalset")
        seen.add(task_id)
        item["task_id"] = task_id
        item["kind"] = str(item["kind"])
        tasks.append(item)
    return tasks


def _normalize_outputs(outputs: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    output_by_task: dict[str, dict[str, Any]] = {}
    for idx, output in enumerate(outputs, start=1):
        item = dict(output)
        if "task_id" not in item:
            raise ValueError(f"output row {idx} missing required field 'task_id'")
        if "output" not in item:
            raise ValueError(f"output row {idx} missing required field 'output'")
        task_id = str(item["task_id"])
        if task_id in output_by_task:
            raise ValueError(f"duplicate output for task_id {task_id!r}")
        output_by_task[task_id] = item
    return output_by_task


def _run_check(check: Mapping[str, Any], output_value: Any) -> CheckResult:
    check_type = check.get("type")
    if not check_type:
        raise ValueError("check missing required field 'type'")
    if check_type == "equals":
        _require_fields(check, "value")
        expected = check.get("value")
        passed = output_value == expected
        return CheckResult(
            passed,
            None if passed else "equals_mismatch",
            {"expected": expected, "actual": output_value},
        )
    if check_type == "contains":
        _require_fields(check, "value")
        expected = str(check.get("value", ""))
        actual = str(output_value)
        if not check.get("case_sensitive", True):
            expected = expected.lower()
            actual = actual.lower()
        passed = expected in actual
        return CheckResult(
            passed,
            None if passed else "contains_mismatch",
            {"expected": check.get("value"), "actual": output_value},
        )
    if check_type == "regex":
        _require_fields(check, "pattern")
        pattern = str(check.get("pattern", ""))
        flags = re.IGNORECASE if not check.get("case_sensitive", True) else 0
        passed = re.search(pattern, str(output_value), flags=flags) is not None
        return CheckResult(
            passed,
            None if passed else "regex_mismatch",
            {"pattern": pattern, "actual": output_value},
        )
    if check_type == "json_path_equals":
        _require_fields(check, "path", "value")
        path = str(check.get("path", ""))
        try:
            document = _json_document(output_value)
            actual = _get_path(document, path)
        except ValueError as exc:
            return CheckResult(
                False,
                "json_path_unavailable",
                {"path": path, "error": str(exc)},
            )
        expected = check.get("value")
        passed = actual == expected
        return CheckResult(
            passed,
            None if passed else "json_path_equals_mismatch",
            {"path": path, "expected": expected, "actual": actual},
        )
    if check_type == "json_path_in":
        _require_fields(check, "path", "values")
        path = str(check.get("path", ""))
        try:
            document = _json_document(output_value)
            actual = _get_path(document, path)
        except ValueError as exc:
            return CheckResult(
                False,
                "json_path_unavailable",
                {"path": path, "error": str(exc)},
            )
        values = list(check.get("values", []))
        passed = actual in values
        return CheckResult(
            passed,
            None if passed else "json_path_in_mismatch",
            {"path": path, "values": values, "actual": actual},
        )
    if check_type == "number_range":
        if "min" not in check and "max" not in check:
            raise ValueError("number_range check requires 'min' or 'max'")
        path = str(check.get("path", ""))
        try:
            document = _json_document(output_value)
            actual = _get_path(document, path) if path else output_value
        except ValueError as exc:
            return CheckResult(
                False,
                "number_range_unavailable",
                {"path": path, "error": str(exc)},
            )
        try:
            number = float(actual)
        except (TypeError, ValueError):
            return CheckResult(False, "number_range_not_numeric", {"path": path, "actual": actual})
        min_value = check.get("min")
        max_value = check.get("max")
        passed = True
        if min_value is not None:
            passed = passed and number >= float(min_value)
        if max_value is not None:
            passed = passed and number <= float(max_value)
        return CheckResult(
            passed,
            None if passed else "number_range_mismatch",
            {"path": path, "min": min_value, "max": max_value, "actual": actual},
        )
    raise ValueError(f"unsupported check type {check_type!r}")


def _require_fields(check: Mapping[str, Any], *fields: str) -> None:
    missing = [field for field in fields if field not in check]
    if missing:
        joined = ", ".join(repr(field) for field in missing)
        raise ValueError(f"{check.get('type')} check missing required field(s): {joined}")


def _json_document(output_value: Any) -> Any:
    if isinstance(output_value, str):
        try:
            return json.loads(output_value)
        except json.JSONDecodeError as exc:
            raise ValueError("output is not valid JSON") from exc
    return output_value


def _get_path(document: Any, path: str) -> Any:
    current = document
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"list path segment {part!r} not found") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise ValueError(f"object path segment {part!r} not found")
            current = current[part]
        else:
            raise ValueError(f"path segment {part!r} reached non-container value")
    return current


def _validate_prob(value: Any, *, task_id: str) -> float:
    prob = float(value)
    if prob < 0.0 or prob > 1.0:
        raise ValueError(f"output for task_id {task_id!r} has prob outside [0, 1]")
    return prob
