#!/usr/bin/env python3
"""Small external agent used to exercise the real command-runner seam."""

from __future__ import annotations

import json
import sys


def answer(task_input: dict, mode: str):
    operation = task_input["operation"]
    if operation == "ping":
        return "pong"
    if operation == "route":
        text = task_input["text"].lower()
        if mode == "baseline":
            return {"route": "technical" if "error" in text else "general"}
        if "invoice" in text or "charge" in text:
            return {"route": "billing"}
        if "error" in text or "crash" in text:
            return {"route": "technical"}
        return {"route": "account"}
    if operation == "extract":
        record = task_input["record"]
        return {"name": record["name"]} if mode == "baseline" else dict(record)
    if operation == "code":
        topic = task_input["topic"]
        if "paths" in topic:
            return "Use os.path.join(root, child)."
        if mode == "baseline":
            return "Use eval(payload) after checking the source."
        return "Use json.loads(payload) and handle json.JSONDecodeError."
    if operation == "risk":
        if task_input["mode"] == "category":
            return {"category": "low"}
        return {"confidence": 0.55 if mode == "baseline" else 0.84}
    raise ValueError(f"unsupported operation: {operation}")


def main() -> int:
    request = json.load(sys.stdin)
    task = request["task"]
    response = {
        "output": answer(task["input"], request["config"]["mode"]),
        "prob": 0.8,
        "note": f"external process handled {task['task_id']}",
        "metadata": {
            "saw_check": "check" in task,
            "saw_expected_value": "value" in task,
        },
    }
    json.dump(response, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
