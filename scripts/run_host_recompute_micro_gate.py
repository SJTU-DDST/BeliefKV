#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beliefkv.core.events import (
    ContextMode,
    EventConfidence,
    ExecutionMode,
    RelationType,
    RuntimeEvent,
    RuntimeEventKind,
)
from beliefkv.experiments.server_contract import (
    fetch_server_info,
    validate_server_identity,
)
from beliefkv.runtime.event_channel import UnixDatagramRuntimeEventSink


WORKFLOW_ID = "host-recompute-micro-gate:workflow"
INVOCATION_ID = f"{WORKFLOW_ID}:invocation"
CONTEXT_ID = f"{WORKFLOW_ID}:context"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic CPU_ONLY drop/recompute GPU gate."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-audit", type=Path, required=True)
    parser.add_argument("--event-socket", type=Path, required=True)
    parser.add_argument("--gate-id", default="p5-host-recompute-v1")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-model-path", type=Path, required=True)
    parser.add_argument("--prompt-words", type=int, default=32768)
    parser.add_argument("--first-output-tokens", type=int, default=64)
    parser.add_argument("--continuation-output-tokens", type=int, default=128)
    parser.add_argument("--gate-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=1800.0)
    return parser.parse_args()


def _metadata() -> dict[str, object]:
    return {
        "root_workflow_id": WORKFLOW_ID,
        "invocation_id": INVOCATION_ID,
        "context_id": CONTEXT_ID,
        "context_epoch": 0,
        "agent_definition_id": "host-recompute-micro-gate",
        "agent_instance_id": "host-recompute-micro-gate:parent",
        "parent_invocation_id": None,
        "parent_context_id": None,
        "relation_type": "root",
        "context_mode": "fresh",
        "execution_mode": "foreground",
        "return_target_id": None,
        "join_id": None,
        "full_prompt_replay_guaranteed": True,
    }


def _event(kind: RuntimeEventKind, *, suffix: str) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"host-recompute-{suffix}-{uuid.uuid4().hex[:8]}",
        ts_ms=time.monotonic() * 1000.0,
        kind=kind,
        workflow_id=WORKFLOW_ID,
        invocation_id=INVOCATION_ID,
        context_id=CONTEXT_ID,
        context_epoch=0,
        relation_type=(
            RelationType.ROOT
            if kind == RuntimeEventKind.INVOCATION_CREATE
            else None
        ),
        context_mode=(
            ContextMode.FRESH
            if kind == RuntimeEventKind.INVOCATION_CREATE
            else None
        ),
        execution_mode=(
            ExecutionMode.FOREGROUND
            if kind == RuntimeEventKind.INVOCATION_CREATE
            else None
        ),
        confidence=EventConfidence.OBSERVED_EXACT,
        attributes={"source": "host_recompute_micro_gate"},
    )


def _post(endpoint: str, payload: dict[str, object], timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict) or value.get("error"):
        raise RuntimeError(f"invalid model response: {value!r}")
    choice = (value.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "elapsed_seconds": time.monotonic() - started,
        "finish_reason": choice.get("finish_reason"),
        "content": str(message.get("content") or ""),
        "usage": value.get("usage") or {},
    }


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _wait_for_stage(path: Path, stage: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [
            item
            for item in _records(path)
            if item.get("event") == "host_recompute_micro_gate_state"
            and item.get("stage") in {stage, "failed"}
        ]
        if matches:
            latest = matches[-1]
            if latest.get("stage") == "failed":
                raise RuntimeError(f"host recompute gate failed: {latest}")
            return latest
        time.sleep(0.2)
    raise TimeoutError(f"host recompute gate did not reach {stage}")


def main() -> int:
    args = _args()
    output = args.output_dir.expanduser().resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    server_info = fetch_server_info(args.base_url, timeout_s=10.0)
    identity = validate_server_identity(
        server_info,
        expected_model=args.model,
        expected_model_path=args.expected_model_path,
        expected_weight_dtype="bfloat16",
        expected_kv_dtype="bfloat16",
    )
    endpoint = f"{args.base_url.rstrip('/')}/chat/completions"
    long_prompt = "host-recompute-prefix " + "context " * args.prompt_words
    system = (
        "Emit deterministic plain text until the token limit. Do not call tools "
        "and do not stop early."
    )
    started_at = datetime.now(timezone.utc)
    with UnixDatagramRuntimeEventSink(
        args.event_socket,
        ack_timeout_s=10.0,
        retries=3,
    ) as sink:
        sink.emit_batch(
            (
                _event(RuntimeEventKind.WORKFLOW_START, suffix="workflow-start"),
                _event(RuntimeEventKind.INVOCATION_CREATE, suffix="create"),
            )
        )
        first = _post(
            endpoint,
            {
                "model": args.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": long_prompt},
                ],
                "max_tokens": args.first_output_tokens,
                "temperature": 0,
                "ignore_eos": True,
                "stream": False,
                "beliefkv_metadata": _metadata(),
            },
            args.request_timeout_seconds,
        )
        sink.emit_batch((_event(RuntimeEventKind.TOOL_START, suffix="tool-start"),))
        drop = _wait_for_stage(
            args.runtime_audit,
            "host_dropped_recompute_required",
            args.gate_timeout_seconds,
        )
        sink.emit_batch((_event(RuntimeEventKind.TOOL_END, suffix="tool-end"),))
        continuation = _post(
            endpoint,
            {
                "model": args.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": long_prompt},
                    {"role": "assistant", "content": first["content"]},
                    {"role": "user", "content": "Tool result: continue."},
                ],
                "max_tokens": args.continuation_output_tokens,
                "temperature": 0,
                "ignore_eos": True,
                "stream": False,
                "beliefkv_metadata": _metadata(),
            },
            args.request_timeout_seconds,
        )
        completed = _wait_for_stage(
            args.runtime_audit,
            "completed",
            args.gate_timeout_seconds,
        )
        sink.emit_batch(
            (
                _event(RuntimeEventKind.RETURN, suffix="return"),
                _event(RuntimeEventKind.WORKFLOW_END, suffix="workflow-end"),
            )
        )
    finished_at = datetime.now(timezone.utc)
    manifest = {
        "schema_version": 1,
        "gate_id": args.gate_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "server_identity": identity,
        "first": {
            key: value
            for key, value in first.items()
            if key != "content"
        },
        "drop_state": drop,
        "continuation": continuation,
        "completed_state": completed,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
