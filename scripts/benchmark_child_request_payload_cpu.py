#!/usr/bin/env python3
"""Compare complete SDK request construction with equal final JSON bodies."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI, DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


def _distribution(values):
    return {"count": len(values), "mean_ms": mean(values), "p50_ms": median(values),
            "max_ms": max(values)}


def _messages(rounds):
    messages = [
        SystemMessage(content="Inspect the repository and report the evidence."),
        HumanMessage(content="Fix the failing test and explain the result."),
    ]
    for index in range(rounds):
        messages.extend([
            AIMessage(content="", tool_calls=[{
                "id": f"call-{index}", "name": "execute",
                "args": {"command": f"sed -n '1,80p' file_{index}.py"},
            }]),
            ToolMessage(
                content=(f"# file {index}\n" + "value = data.get('value')\n" * 60),
                tool_call_id=f"call-{index}",
            ),
        ])
    messages.append(HumanMessage(content="Continue from the evidence."))
    return messages


def benchmark_case(rounds, iterations):
    metadata = BeliefKVRequestMetadata(
        "fixture", "root", "context", 0, full_prompt_replay_guaranteed=True,
    )
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    settings = dict(
        model="fixture", api_key="EMPTY", base_url="http://fixture.invalid/v1",
        max_retries=0, temperature=0, seed=21, max_tokens=8192,
    )
    models = {
        "baseline": ChatOpenAI(**settings),
        "optimized": BeliefKVChatOpenAI(beliefkv_adapter=adapter, **settings),
    }
    messages = _messages(rounds)
    kwargs = {
        "extra_body": {
            "beliefkv_metadata": metadata.to_wire(),
            "rid": "beliefkv:fixture", "session_id": "session-1",
        },
        "tools": [{
            "type": "function", "function": {
                "name": "execute", "parameters": {
                    "type": "object", "properties": {"command": {"type": "string"}},
                },
            },
        }],
        "stream": False,
    }
    bodies, clients, costs = {}, {}, {}
    last_body = None
    response = {
        "id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture",
        "choices": [{
            "index": 0, "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }],
    }

    def respond(request):
        nonlocal last_body
        last_body = request.content
        return httpx.Response(200, json=response)

    for name in models:
        clients[name] = OpenAI(
            api_key="EMPTY", base_url="http://fixture.invalid/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        costs[name] = []
    try:
        for iteration in range(iterations + 2):
            order = list(models)
            if iteration % 2:
                order.reverse()
            for name in order:
                started = time.perf_counter_ns()
                payload = models[name]._get_request_payload(messages, **kwargs)
                result = clients[name].chat.completions.create(**payload)
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                assert result.choices[0].message.content == "OK"
                if iteration >= 2:
                    costs[name].append(elapsed)
                bodies[name] = last_body
        decoded = {name: json.loads(body) for name, body in bodies.items()}
        if decoded["baseline"] != decoded["optimized"]:
            raise AssertionError("changed final SDK JSON request")
        canonical = json.dumps(decoded["baseline"], sort_keys=True).encode()
        return {
            "tool_rounds": rounds, "message_count": len(messages),
            "request_bytes": len(bodies["baseline"]),
            "final_json_equal": True,
            "canonical_body_sha256": hashlib.sha256(canonical).hexdigest(),
            "costs": {name: _distribution(values) for name, values in costs.items()},
            "mean_reduction": 1 - mean(costs["optimized"]) / mean(costs["baseline"]),
        }
    finally:
        for client in clients.values():
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        raise ValueError("positive iterations required")
    result = {
        "scope": (
            "LangChain payload conversion, real SDK request construction, JSON "
            "encoding and response parsing with MockTransport; no network or GPU. "
            "Same messages/tools/runtime metadata/seed/completion budget. "
            "CPU savings are not measured end-to-end throughput savings."
        ),
        "versions": {name: version(name) for name in ("openai", "langchain-openai", "httpx")},
        "baseline": "inherited ChatOpenAI payload conversion and SDK typed traversal",
        "optimized": "native-tagged wire messages merged via SDK extra_body",
        "source_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/deepagents_adapter.py").read_bytes()
        ).hexdigest(),
        "cases": [benchmark_case(rounds, args.iterations) for rounds in (4, 32, 128)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
