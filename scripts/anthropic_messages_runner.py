#!/usr/bin/env python3
"""Command-runner adapter for Anthropic-compatible Messages endpoints."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping


DEFAULT_BASE_URL = "http://127.0.0.1:8484/v1/messages"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_API_KEY_ENV = "DEEPSEEK_API_KEY"


def extract_output_text(response: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("content", []):
        if not isinstance(item, Mapping) or item.get("type") != "text":
            continue
        value = item.get("text")
        if isinstance(value, str):
            parts.append(value)
    if not parts:
        raise ValueError("Messages payload did not contain text content")
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
    system_prompt = (
        str(config.get("system_prompt") or "")
        if "system_prompt" in config
        else "Complete the task accurately. Return only the requested result."
    )
    user_content = build_user_content(task.get("input"), config)
    return {
        "model": str(config.get("model") or DEFAULT_MODEL),
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "max_tokens": int(config.get("max_output_tokens", 192)),
        "temperature": 0,
        "thinking": {"type": "disabled"},
    }


def build_user_content(task_input: Any, config: Mapping[str, Any]) -> str:
    """Render a config-owned prompt without exposing eval labels to the agent."""

    template = config.get("user_prompt_template")
    if template is None:
        return json.dumps(task_input, ensure_ascii=False, sort_keys=True)
    if not isinstance(template, str) or "{{REPORT_TEXT}}" not in template:
        raise ValueError("user_prompt_template must contain {{REPORT_TEXT}}")
    if not isinstance(task_input, Mapping):
        raise TypeError("templated runner input must be an object")
    report_text = task_input.get("report_text")
    if not isinstance(report_text, str) or not report_text.strip():
        raise ValueError("templated runner input requires non-empty report_text")
    return template.replace("{{REPORT_TEXT}}", report_text)


def call_messages(
    payload: Mapping[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(
            f"Messages request failed with HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Messages request failed: {exc.reason}") from exc
    if not isinstance(value, dict):
        raise TypeError("Messages response must be a JSON object")
    return value


def main() -> int:
    try:
        request = json.load(sys.stdin)
        config = request["config"]
        api_key_env = str(config.get("api_key_env") or DEFAULT_API_KEY_ENV)
        api_key = os.environ.get(api_key_env)
        if not api_key:
            print(f"{api_key_env} is not configured", file=sys.stderr)
            return 2

        payload = build_request(request)
        response = call_messages(
            payload,
            base_url=str(config.get("base_url") or DEFAULT_BASE_URL),
            api_key=api_key,
            timeout_seconds=float(config.get("request_timeout_seconds", 180)),
        )
        text = extract_output_text(response)
        result = {
            "output": parse_agent_output(text),
            "note": "Anthropic-compatible Messages output",
            "metadata": {
                "response_id": response.get("id"),
                "requested_model": payload["model"],
                "response_model": response.get("model"),
                "stop_reason": response.get("stop_reason"),
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
