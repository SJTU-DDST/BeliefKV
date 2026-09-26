#!/usr/bin/env python3
"""Read-only SSE timing probe: never persist prompt, text, or hidden vectors."""

from __future__ import annotations

import argparse
import json
import time
from urllib.request import Request, urlopen
from uuid import uuid4


def _hidden_shape(value: object) -> list[int]:
    shape = []
    while isinstance(value, list):
        shape.append(len(value))
        if not value:
            break
        value = value[0]
    return shape


def _observe_event(summary: dict, event: dict, elapsed_ms: float) -> None:
    request_id = event.get("id")
    if request_id is not None:
        if summary["response_id"] not in (None, request_id):
            raise ValueError("SSE response changed request identity")
        summary["response_id"] = request_id
    for choice in event.get("choices") or []:
        delta = choice.get("delta") or {}
        if delta.get("reasoning_content") or delta.get("content"):
            summary["text_chunks"] += 1
            summary["first_text_ms"] = summary["first_text_ms"] or elapsed_ms
        if delta.get("hidden_states") is not None:
            summary["hidden_chunks"] += 1
            summary["first_hidden_ms"] = summary["first_hidden_ms"] or elapsed_ms
            summary["hidden_event_times_ms"].append(elapsed_ms)
            summary["hidden_shapes"].append(_hidden_shape(delta["hidden_states"]))
        if choice.get("finish_reason"):
            summary["first_finish_ms"] = summary["first_finish_ms"] or elapsed_ms
            summary["finish_reason"] = choice["finish_reason"]


def _finish_summary(summary: dict) -> dict:
    summary["hidden_before_finish"] = (
        summary["first_hidden_ms"] is not None
        and summary["first_finish_ms"] is not None
        and summary["first_hidden_ms"] < summary["first_finish_ms"]
    )
    if summary["first_finish_ms"] is not None:
        early = [
            t for t in summary.pop("hidden_event_times_ms")
            if t < summary["first_finish_ms"]
        ]
        summary["early_hidden_chunks"] = len(early)
        summary["last_hidden_lead_ms"] = (
            summary["first_finish_ms"] - early[-1] if early else None
        )
        summary["first_hidden_lead_ms"] = (
            summary["first_finish_ms"] - early[0] if early else None
        )
    else:
        summary.pop("hidden_event_times_ms")
        summary["early_hidden_chunks"] = 0
        summary["last_hidden_lead_ms"] = None
        summary["first_hidden_lead_ms"] = None
    summary["hidden_shapes"] = sorted({
        tuple(shape) for shape in summary["hidden_shapes"]
    })
    return summary


def run(base_url: str, *, hidden: bool, max_tokens: int) -> dict:
    rid = uuid4().hex
    request_body = {
        "model": "Qwen3.5-35B-A3B",
        "messages": [{
            "role": "user",
            "content": (
                "In plain prose, compare three ways to test a parser. "
                "Use at least 250 words. Do not call tools."
            ),
        }],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "rid": rid,
        "return_hidden_states": "last" if hidden else False,
    }
    summary = {
        "requested_hidden": hidden,
        "request_id": rid,
        "response_id": None,
        "text_chunks": 0,
        "first_text_ms": None,
        "hidden_chunks": 0,
        "first_hidden_ms": None,
        "hidden_event_times_ms": [],
        "hidden_shapes": [],
        "first_finish_ms": None,
        "finish_reason": None,
        "done_ms": None,
        "sse_payload_bytes": 0,
        "hidden_event_bytes": 0,
    }
    request = Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(request_body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urlopen(request, timeout=180) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            elapsed_ms = (time.monotonic() - started) * 1000
            if payload == b"[DONE]":
                summary["done_ms"] = elapsed_ms
                break
            summary["sse_payload_bytes"] += len(payload)
            before = summary["hidden_chunks"]
            _observe_event(summary, json.loads(payload), elapsed_ms)
            if summary["hidden_chunks"] > before:
                summary["hidden_event_bytes"] += len(payload)
    summary["total_ms"] = (time.monotonic() - started) * 1000
    return _finish_summary(summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()
    if args.max_tokens < 1:
        parser.error("--max-tokens must be positive")
    print(json.dumps(
        [run(args.base_url, hidden=value, max_tokens=args.max_tokens)
         for value in (False, True)],
        indent=2,
    ))


if __name__ == "__main__":
    main()
