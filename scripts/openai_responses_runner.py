#!/usr/bin/env python3
"""Command-runner adapter that executes one task with the OpenAI Responses API."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping


RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-sol"


def extract_output_text(response: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, Mapping) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    if not parts:
        raise ValueError("Responses payload did not contain output_text")
    return "\n".join(parts).strip()


def parse_agent_output(text: str) -> Any:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return text.strip()


def build_request(request: Mapping[str, Any]) -> dict[str, Any]:
    config = request["config"]
    task = request["task"]
    model = str(config.get("model") or DEFAULT_MODEL)
    effort = str(config.get("reasoning_effort") or "low")
    instructions = str(
        config.get("system_prompt")
        or "Complete the task accurately. Return only the requested result."
    )
    return {
        "model": model,
        "reasoning": {"effort": effort},
        "instructions": instructions,
        "input": json.dumps(task.get("input"), ensure_ascii=False, sort_keys=True),
        "max_output_tokens": int(config.get("max_output_tokens", 300)),
        "store": False,
    }


def call_responses(payload: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"OpenAI Responses request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI Responses request failed: {exc.reason}") from exc
    if not isinstance(value, dict):
        raise TypeError("OpenAI Responses payload must be a JSON object")
    if value.get("status") not in {None, "completed"}:
        raise RuntimeError(f"OpenAI response did not complete: {value.get('status')}")
    return value


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not configured", file=sys.stderr)
        return 2
    try:
        request = json.load(sys.stdin)
        payload = build_request(request)
        response = call_responses(payload, api_key)
        text = extract_output_text(response)
        result = {
            "output": parse_agent_output(text),
            "note": "OpenAI Responses API output",
            "metadata": {
                "response_id": response.get("id"),
                "requested_model": payload["model"],
                "response_model": response.get("model"),
                "reasoning_effort": payload["reasoning"]["effort"],
                "usage": response.get("usage"),
            },
        }
        json.dump(result, sys.stdout, ensure_ascii=False)
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
