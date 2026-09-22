#!/usr/bin/env python3
"""Check native Qwen3.5 chat and tool round trip, without enabling BeliefKV."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, payload: dict, timeout: float) -> dict:
    request = Request(
        f"{url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(
            f"native chat returned HTTP {exc.code}: {exc.read(512)!r}"
        ) from exc


def _chat(url: str, model: str, messages: list[dict], timeout: float, **extra: object) -> dict:
    response = _request(
        url,
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 512,
            **extra,
        },
        timeout,
    )
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("native chat response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("native chat response has no assistant message")
    return message


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--model", default="Qwen3.5-35B-A3B")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    start = time.monotonic()
    plain = _chat(
        args.url,
        args.model,
        [{"role": "user", "content": "Reply with one short sentence in English."}],
        args.timeout,
    )
    if not plain.get("content"):
        raise RuntimeError("native chat produced no answer")

    messages = [{"role": "user", "content": "Use echo to repeat the word ready."}]
    tool_request = _chat(
        args.url,
        args.model,
        messages,
        args.timeout,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Repeat the provided text.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "echo"}},
    )
    calls = tool_request.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise RuntimeError(f"expected one parsed native tool call, got {calls!r}")
    call = calls[0]
    if call.get("function", {}).get("name") != "echo" or not call.get("id"):
        raise RuntimeError("native tool call is missing echo name or call ID")
    arguments = json.loads(call["function"]["arguments"])
    if not isinstance(arguments, dict) or not isinstance(arguments.get("text"), str):
        raise RuntimeError("native tool-call arguments were not decoded")
    messages.extend(
        [
            tool_request,
            {"role": "tool", "tool_call_id": call["id"], "content": "ready"},
        ]
    )
    resumed = _chat(args.url, args.model, messages, args.timeout)
    if not resumed.get("content"):
        raise RuntimeError("native chat did not resume after tool result")
    print(
        json.dumps(
            {
                "native_smoke": "passed",
                "tool_name": call["function"]["name"],
                "tool_arguments": arguments,
                "elapsed_s": round(time.monotonic() - start, 3),
                "beliefkv_enabled": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        raise SystemExit(f"native server unavailable: {exc}") from exc
