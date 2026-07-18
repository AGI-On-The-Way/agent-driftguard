"""Offline contract checks for the optional OpenAI Responses command runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "openai_responses_runner.py"
SPEC = importlib.util.spec_from_file_location("openai_responses_runner", RUNNER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extract_and_parse_output() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '```json\n{"route":"billing"}\n```',
                    }
                ],
            }
        ]
    }
    text = MODULE.extract_output_text(response)
    assert MODULE.parse_agent_output(text) == {"route": "billing"}


def test_request_uses_gpt56_responses_fields() -> None:
    request = MODULE.build_request(
        {
            "task": {"input": {"operation": "ping"}},
            "config": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "system_prompt": "Return the result only.",
            },
        }
    )
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning"] == {"effort": "low"}
    assert request["instructions"] == "Return the result only."
    assert request["store"] is False
    assert "operation" in request["input"]


def main() -> int:
    test_extract_and_parse_output()
    test_request_uses_gpt56_responses_fields()
    print("OpenAI Responses runner contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
