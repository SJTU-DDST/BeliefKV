#!/usr/bin/env python3
"""Compare inherited and optimized tool-fragment conversion on real SSE paths."""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from statistics import mean
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
from scripts.benchmark_child_stream_cpu import _distribution, _sse


class InheritedFragmentConversion(BeliefKVChatOpenAI):
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        return ChatOpenAI._convert_chunk_to_generation_chunk(
            self, chunk, default_chunk_class, base_generation_info,
        )


async def benchmark_case(text_frames, note_repeats, fragment_chars, async_stream, iterations):
    sse, frame_count = _sse(
        text_frames, note_repeats, argument_fragment_chars=fragment_chars,
    )
    bodies, outputs = {}, {}
    active_name = None

    def respond(request):
        bodies[active_name] = json.loads(request.content)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(sse),
        )

    metadata = BeliefKVRequestMetadata("fixture", "root", "context", 0)
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    wall_costs = {"inherited": [], "optimized": []}
    cpu_costs = {"inherited": [], "optimized": []}
    gc_costs = {"inherited": [], "optimized": []}
    measured_name = None
    gc_started = None
    gc_cpu_ns = {"inherited": 0, "optimized": 0}

    def observe_gc(phase, info):
        nonlocal gc_started
        if measured_name is None:
            return
        if phase == "start":
            gc_started = time.thread_time_ns()
        elif gc_started is not None:
            gc_cpu_ns[measured_name] += time.thread_time_ns() - gc_started
            gc_started = None

    gc.callbacks.append(observe_gc)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_async:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            settings = dict(
                beliefkv_adapter=adapter, model="fixture", api_key="EMPTY",
                base_url="http://fixture.invalid/v1", http_client=http,
                http_async_client=http_async, max_retries=0,
            )
            models = {
                "inherited": InheritedFragmentConversion(**settings),
                "optimized": BeliefKVChatOpenAI(**settings),
            }
            messages = [HumanMessage(content="Inspect and report.")]
            kwargs = {"extra_body": {"beliefkv_metadata": metadata.to_wire()}}
            for iteration in range(iterations + 2):
                order = list(models)
                if iteration % 2:
                    order.reverse()
                for name in order:
                    active_name = name
                    measured_name = name
                    gc_before = gc_cpu_ns[name]
                    wall_started, cpu_started = time.perf_counter_ns(), time.thread_time_ns()
                    if async_stream:
                        chunks = [chunk async for chunk in ChatOpenAI._astream(
                            models[name], messages, **kwargs,
                        )]
                    else:
                        chunks = list(ChatOpenAI._stream(models[name], messages, **kwargs))
                    result = generate_from_stream(iter(chunks))
                    wall_elapsed = (time.perf_counter_ns() - wall_started) / 1_000_000
                    cpu_elapsed = (time.thread_time_ns() - cpu_started) / 1_000_000
                    measured_name = None
                    outputs[name] = result.model_dump()
                    if iteration >= 2:
                        wall_costs[name].append(wall_elapsed)
                        cpu_costs[name].append(cpu_elapsed)
                        gc_costs[name].append((gc_cpu_ns[name] - gc_before) / 1_000_000)
                assert outputs["inherited"] == outputs["optimized"], "changed final result"
                assert bodies["inherited"] == bodies["optimized"], "changed request body"
    gc.callbacks.remove(observe_gc)
    return {
        "async_stream": async_stream, "text_frames": text_frames,
        "tool_note_repeats": note_repeats, "fragment_chars": fragment_chars,
        "sse_frames": frame_count, "sse_bytes": len(sse),
        "tool_argument_fragments": frame_count - text_frames - 3,
        "final_result_equal": True, "final_request_json_equal": True,
        "canonical_result_sha256": hashlib.sha256(
            json.dumps(outputs["inherited"], sort_keys=True).encode()
        ).hexdigest(),
        "wall_costs": {name: _distribution(values) for name, values in wall_costs.items()},
        "thread_cpu_costs": {name: _distribution(values) for name, values in cpu_costs.items()},
        "included_gc_thread_cpu_costs": {
            name: _distribution(values) for name, values in gc_costs.items()
        },
        "mean_cpu_reduction": 1 - mean(cpu_costs["optimized"]) / mean(cpu_costs["inherited"]),
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
            "Actual SDK-decoded SSE, LangChain chunk conversion and final-message "
            "aggregation. Both sides use the same SDK and wire-payload optimizations. "
            "No network, GPU or agent callbacks. Garbage collection remains enabled "
            "and its pauses are included; synthetic CPU savings are not throughput gains."
        ),
        "baseline": "inherited LangChain partial JSON parsing for each fragment",
        "optimized": "same invalid-fragment result without futile suffix-trimming parse",
        "versions": {name: version(name) for name in (
            "openai", "langchain-openai", "langchain-core", "httpx",
        )},
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "beliefkv/runtime/deepagents_adapter.py",
                "scripts/benchmark_tool_fragment_cpu.py",
                "scripts/benchmark_child_stream_cpu.py",
            )
        },
        "cases": [
            await benchmark_case(text, note, chars, async_stream, args.iterations)
            for async_stream in (False, True)
            for text, note, chars in (
                (512, 0, 8), (64, 128, 8), (64, 512, 64), (64, 4096, 1024),
            )
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
