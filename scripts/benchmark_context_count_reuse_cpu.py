#!/usr/bin/env python3
"""Measure consecutive context counts on reconstructed long agent inputs, CPU only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import SystemMessage, convert_to_messages, convert_to_openai_messages

from beliefkv.experiments.long_repository_tasks import parse_repository_context
from beliefkv.runtime import context_token_counter as counter_module
from scripts.benchmark_chat_prompt_encoding import trajectory_messages
from scripts.benchmark_long_chat_token_cache import distribution, source_binding


class RenderCounter:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.render_calls = 0

    def __getattr__(self, name):
        return getattr(self.tokenizer, name)

    def apply_chat_template(self, *args, **kwargs):
        self.render_calls += 1
        return self.tokenizer.apply_chat_template(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-workflows", type=int, default=12)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists() or min(args.iterations, args.max_workflows) < 1:
        parser.error("use positive sample counts and a fresh output path")
    manifest = json.loads(args.manifest.read_text())
    workloads = {row["instance_id"]: row for row in manifest["workloads"]}
    sources = {str(args.manifest.resolve()): source_binding(args.manifest)}
    cases = []
    for path in sorted(args.arm.glob("client_*/workflows/*/trajectory.json"))[:args.max_workflows]:
        context = parse_repository_context(workloads[path.parent.name]["frozen_repository_context"])
        messages = [
            SystemMessage(content=context.text),
            *convert_to_messages(trajectory_messages(json.loads(path.read_text()))),
        ]
        ends = [i + 1 for i, message in enumerate(messages) if message.type in ("human", "tool")]
        for ordinal in sorted(set((0, len(ends) // 2, len(ends) - 1))):
            cases.append((f"{path.parent.name}:{ends[ordinal]}", messages[:ends[ordinal]]))
        for source in (path, context.path):
            sources[str(source.resolve())] = source_binding(source)
    if not cases:
        parser.error("no source-backed histories found")
    counters = {
        name: counter_module.ChatTemplateTokenCounter(
            manifest["tokenizer"], cache_requests=enabled,
        )
        for name, enabled in (("baseline", False), ("candidate", True))
    }
    native_tokenizer = counters["baseline"].tokenizer
    rows = {}
    for identity, messages in cases:
        wire = convert_to_openai_messages(messages)
        for message in wire:
            for call in message.get("tool_calls", ()):
                call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        rendered = native_tokenizer.apply_chat_template(
            wire, tools=None, tokenize=False, add_generation_prompt=True, enable_thinking=True,
        )
        rows[identity] = {
            "case": identity, "prompt_tokens": len(native_tokenizer.encode(
                rendered, add_special_tokens=False,
            )),
            **{
                f"{name}_{phase}_{clock}_ms": []
                for name in counters for phase in ("first", "second", "pair")
                for clock in ("wall", "thread_cpu")
            },
        }
    for counter in counters.values():
        counter.tokenizer = RenderCounter(counter.tokenizer)
    for iteration in range(args.iterations):
        for counter in counters.values():
            counter._cache.clear()
            counter._chars = 0
            counter._last_request = None
        for identity, messages in cases:
            names = list(counters)
            if iteration % 2:
                names.reverse()
            for name in names:
                phase_times = []
                for phase in ("first", "second"):
                    wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                    count = counters[name](messages)
                    wall = (time.perf_counter_ns() - wall_start) / 1e6
                    cpu = (time.thread_time_ns() - cpu_start) / 1e6
                    if count != rows[identity]["prompt_tokens"]:
                        raise AssertionError(f"native count mismatch: {name}:{identity}:{phase}")
                    rows[identity][f"{name}_{phase}_wall_ms"].append(wall)
                    rows[identity][f"{name}_{phase}_thread_cpu_ms"].append(cpu)
                    phase_times.append((wall, cpu))
                for index, clock in enumerate(("wall", "thread_cpu")):
                    rows[identity][f"{name}_pair_{clock}_ms"].append(
                        sum(value[index] for value in phase_times),
                    )
    result_rows = [{
        key: statistics.mean(value) if isinstance(value, list) else value
        for key, value in row.items()
    } for row in rows.values()]
    render_calls = {name: counter.tokenizer.render_calls for name, counter in counters.items()}
    expected_renders = len(cases) * args.iterations
    if render_calls != {"baseline": expected_renders * 2, "candidate": expected_renders}:
        raise AssertionError(f"unexpected count reuse: {render_calls}")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "CPU only, real Qwen tokenizer and exact native token-count checks. "
            "Two consecutive counts model compression checking followed by completion budgeting. "
            "Reconstructed source-backed root histories omit original role prompts/tool schemas; "
            "not exact HTTP replay, observed cache-hit frequency, GPU, JCT or speedup evidence."
        ),
        "sources": list(sources.values()),
        "counter_source": source_binding(Path(counter_module.__file__)),
        "benchmark_source": source_binding(Path(__file__)),
        "cache_requests_by_variant": {"baseline": False, "candidate": True},
        "iterations": args.iterations, "all_counts_equal_native": True,
        "template_render_calls": render_calls,
        "summary": {
            name: distribution(row[name] for row in result_rows)
            for name in result_rows[0] if name.endswith("_ms")
        },
        "rows": result_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "all_counts_equal_native": True, "template_render_calls": render_calls,
        "summary": report["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
