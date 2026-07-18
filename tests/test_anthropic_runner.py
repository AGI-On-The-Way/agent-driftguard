"""Offline contract checks for the Anthropic Messages command runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "anthropic_messages_runner.py"
SPEC = importlib.util.spec_from_file_location("anthropic_messages_runner", RUNNER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extract_and_parse_output() -> None:
    response = {
        "content": [
            {"type": "thinking", "thinking": "hidden reasoning"},
            {"type": "text", "text": '```json\n{"route":"billing"}\n```'},
        ]
    }
    text = MODULE.extract_output_text(response)
    assert MODULE.parse_agent_output(text) == {"route": "billing"}


def test_request_uses_only_task_input_and_active_config() -> None:
    payload = MODULE.build_request(
        {
            "task": {
                "task_id": "route-billing",
                "input": {"operation": "route", "text": "duplicate charge"},
                "check": {"value": "billing"},
            },
            "config": {
                "model": "deepseek-v4-flash",
                "system_prompt": "Return only the result.",
                "max_output_tokens": 128,
            },
        }
    )
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["system"] == "Return only the result."
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0
    assert "duplicate charge" in payload["messages"][0]["content"]
    assert "billing" not in payload["messages"][0]["content"]


def test_request_renders_config_owned_report_template() -> None:
    payload = MODULE.build_request(
        {
            "task": {
                "task_id": "report-opaque",
                "input": {"report_text": "Revenue rises because volume grows."},
                "check": {"value": "hidden-label"},
            },
            "config": {
                "model": "deepseek-v4-flash",
                "system_prompt": "",
                "user_prompt_template": "PREP\n{{REPORT_TEXT}}",
                "max_output_tokens": 256,
            },
        }
    )
    assert payload["system"] == ""
    assert payload["messages"][0]["content"] == (
        "PREP\nRevenue rises because volume grows."
    )
    assert "hidden-label" not in payload["messages"][0]["content"]


def main() -> int:
    test_extract_and_parse_output()
    test_request_uses_only_task_input_and_active_config()
    test_request_renders_config_owned_report_template()
    print("Anthropic Messages runner contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
