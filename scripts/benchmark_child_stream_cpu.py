#!/usr/bin/env python3
"""Compare SDK SSE conversion and LangChain aggregation with equal results."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace

import httpx
from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI, DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


def _distribution(values):
    return {
        "count": len(values), "mean_ms": mean(values), "p50_ms": median(values),
        "max_ms": max(values),
    }


def _sse(text_frames, tool_frames):
    def chunk(delta, finish=None):
        return {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0,
            "model": "fixture", "choices": [{
                "index": 0, "delta": delta, "finish_reason": finish,
            }],
        }

    frames = [chunk({"role": "assistant", "content": ""})]
    frames.extend(chunk({"content": f"{index}."}) for index in range(text_frames))
    if tool_frames:
        arguments = json.dumps({"command": "python -m pytest", "note": "check " * tool_frames})
        for index in range(0, len(arguments), 6):
            call = {"index": 0, "function": {"arguments": arguments[index:index + 6]}}
            if index == 0:
                call.update(id="call-1", type="function")
                call["function"]["name"] = "execute"
            frames.append(chunk({"tool_calls": [call]}))
    frames.append(chunk({}, "tool_calls" if tool_frames else "stop"))
    frames.append({
        "id": "fixture", "object": "chat.completion.chunk", "created": 0,
        "model": "fixture", "choices": [], "usage": {
            "prompt_tokens": 100, "completion_tokens": text_frames,
            "total_tokens": 100 + text_frames,
        },
    })
    payload = (
        "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
        + "data: [DONE]\n\n"
    ).encode()
    return payload, len(frames)


async def benchmark_case(text_frames, tool_frames, async_stream, iterations):
    sse, frame_count = _sse(text_frames, tool_frames)
    bodies, outputs = {}, {}
    active_name = None

    def respond(request):
        bodies[active_name] = json.loads(request.content)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(sse),
        )

    metadata = BeliefKVRequestMetadata(
        "fixture", "root", "context", 0, full_prompt_replay_guaranteed=True,
    )
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    wall_costs = {"typed": [], "decoded": []}
    cpu_costs = {"typed": [], "decoded": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_async:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            settings = dict(
                model="fixture", api_key="EMPTY", base_url="http://fixture.invalid/v1",
                http_client=http, http_async_client=http_async, max_retries=0,
                seed=21, temperature=0, max_tokens=8192,
            )
            models = {
                name: BeliefKVChatOpenAI(beliefkv_adapter=adapter, **settings)
                for name in wall_costs
            }
            models["typed"].client = models["typed"].root_client.chat.completions
            models["typed"].async_client = models["typed"].root_async_client.chat.completions
            messages = [HumanMessage(content="Inspect and report.")]
            kwargs = {"extra_body": {"beliefkv_metadata": metadata.to_wire()}}
            for iteration in range(iterations + 2):
                order = list(models)
                if iteration % 2:
                    order.reverse()
                for name in order:
                    active_name = name
                    wall_started, cpu_started = time.perf_counter_ns(), time.thread_time_ns()
                    model = models[name]
                    if async_stream:
                        chunks = [chunk async for chunk in ChatOpenAI._astream(
                            model, messages, **kwargs,
                        )]
                    else:
                        chunks = list(ChatOpenAI._stream(model, messages, **kwargs))
                    result = generate_from_stream(iter(chunks))
                    wall_elapsed = (time.perf_counter_ns() - wall_started) / 1_000_000
                    cpu_elapsed = (time.thread_time_ns() - cpu_started) / 1_000_000
                    outputs[name] = result.model_dump()
                    if iteration >= 2:
                        wall_costs[name].append(wall_elapsed)
                        cpu_costs[name].append(cpu_elapsed)
                if outputs["typed"] != outputs["decoded"]:
                    raise AssertionError("changed final LangChain result")
                if bodies["typed"] != bodies["decoded"]:
                    raise AssertionError("changed final SDK JSON request")
    return {
        "text_frames": text_frames, "tool_note_repeats": tool_frames,
        "tool_argument_fragments": frame_count - text_frames - 3,
        "sse_frames": frame_count, "sse_bytes": len(sse), "async_stream": async_stream,
        "final_result_equal": True, "final_request_json_equal": True,
        "canonical_result_sha256": hashlib.sha256(
            json.dumps(outputs["typed"], sort_keys=True).encode()
        ).hexdigest(),
        "wall_costs": {name: _distribution(values) for name, values in wall_costs.items()},
        "thread_cpu_costs": {name: _distribution(values) for name, values in cpu_costs.items()},
        "mean_cpu_reduction": 1 - mean(cpu_costs["decoded"]) / mean(cpu_costs["typed"]),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        raise ValueError("positive iterations required")
    result = {
        "scope": (
            "Actual SDK SSE decoder, LangChain chunk conversion and final-message "
            "aggregation through MockTransport; no network, GPU, callback work or "
            "prediction changes. Synthetic CPU reductions are not throughput gains."
        ),
        "versions": {name: version(name) for name in (
            "openai", "langchain-openai", "langchain-core", "httpx",
        )},
        "baseline": "typed SDK chunks immediately dumped to dict by LangChain",
        "optimized": "SDK decoded data passed directly to inherited LangChain conversion",
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "beliefkv/runtime/deepagents_adapter.py", "beliefkv/runtime/openai_stream.py",
            )
        },
        "cases": [
            await benchmark_case(text, tool, async_stream, args.iterations)
            for async_stream in (False, True)
            for text, tool in ((64, 0), (512, 0), (256, 128))
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
