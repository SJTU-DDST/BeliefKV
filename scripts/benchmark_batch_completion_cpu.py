#!/usr/bin/env python3
"""Compare paired runtime/native completion callbacks using CPU request state."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import synthetic_runtime


def native_method(path, owner, name):
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {}
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def state(runtime, registered):
    return {
        "registered": sorted(registered),
        "visible": {rid: asdict(key) for rid, key in runtime.visible.items()},
        "context_sessions": {cid: asdict(key) for cid, key in runtime.context_sessions.items()},
        "context_tokens": dict(runtime._context_tokens),
        "semantic_revision": runtime.semantic_revision,
        "counts": dict(runtime.counts),
        "pending": runtime.physical_ledger.pending_count,
        "completed_actions": len(runtime.completed_physical_actions),
    }


def fixture(cls, callback, request_cls, *, size, finished, timing_only, mixed):
    runtime, original = synthetic_runtime(cls, CausalFrontierScheduler, size, 1)
    runtime._tool_timing_only = timing_only
    runtime.enable_final_stage_prefetch = runtime.enable_admission_prefetch = False
    requests = []
    for index, item in enumerate(original):
        fields = dict(vars(item))
        fields.pop("finished")
        fields.update(
            finished_reason="done" if finished and index == 0 else None,
            origin_input_ids=(1, 2, 3), output_ids=(4, 5),
        )
        request = request_cls(**fields)
        if mixed and index % 3 == 0:
            key = runtime.visible.pop(request.rid)
            runtime.context_sessions.pop(key.context_id, None)
            request.beliefkv_metadata = None
        requests.append(request)
    scheduler = NS(
        beliefkv_runtime=runtime, waiting_queue=[],
        _beliefkv_registered_request_ids=set(runtime.visible),
    )
    batch = NS(reqs=requests)
    initial = {
        "visible": dict(runtime.visible),
        "context_sessions": dict(runtime.context_sessions),
        "context_tokens": dict(runtime._context_tokens),
        "visible_since": dict(runtime._visible_since),
        "registered": set(scheduler._beliefkv_registered_request_ids),
        "semantic_revision": runtime.semantic_revision,
    }

    def reset():
        runtime.visible = dict(initial["visible"])
        runtime.context_sessions = dict(initial["context_sessions"])
        runtime._context_tokens = dict(initial["context_tokens"])
        runtime._visible_since = dict(initial["visible_since"])
        runtime.semantic_revision = initial["semantic_revision"]
        runtime.counts.clear()
        runtime._workflow_attained_output_tokens.clear()
        scheduler._beliefkv_registered_request_ids = set(initial["registered"])

    def run():
        callback(scheduler, batch)

    return runtime, scheduler, batch, reset, run


def measure(variants, request_cls, *, size, finished, timing_only, mixed, samples):
    fixtures = {
        name: fixture(cls, hook, request_cls, size=size, finished=finished,
                      timing_only=timing_only, mixed=mixed)
        for name, (cls, hook) in variants.items()
    }
    costs = {name: {"wall_us": [], "thread_cpu_us": []} for name in fixtures}
    calls = 1 if finished else 100
    checked_calls = {}
    try:
        for sample in range(samples + 20):
            order = list(fixtures)
            if sample % 2:
                order.reverse()
            states = {}
            for name in order:
                runtime, scheduler, batch, reset, run = fixtures[name]
                reset()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                for _ in range(calls):
                    run()
                wall_us = (time.perf_counter_ns() - wall) / 1000. / calls
                cpu_us = (time.thread_time_ns() - cpu) / 1000. / calls
                states[name] = state(runtime, scheduler._beliefkv_registered_request_ids)
                if sample >= 20:
                    costs[name]["wall_us"].append(wall_us)
                    costs[name]["thread_cpu_us"].append(cpu_us)
            if any(value != states["baseline"] for value in states.values()):
                raise AssertionError((size, finished, timing_only, mixed, states))
        # Count finished() calls only after timing; instrumentation must not
        # inflate the benefit of avoiding a native scalar predicate.
        for name, (_, _, batch, reset, run) in fixtures.items():
            reset()
            counts = {}
            for request in batch.reqs:
                def counted(*, request=request):
                    counts[request.rid] = counts.get(request.rid, 0) + 1
                    return request_cls.finished(request)
                request.finished = counted
            run()
            checked_calls[name] = counts
        if any(count != 1 for count in checked_calls["candidate"].values()):
            raise AssertionError("candidate skipped or repeated a request completion check")
        summaries = {
            name: {clock: {"p50": median(values), "mean": mean(values), "samples": values}
                   for clock, values in clocks.items()}
            for name, clocks in costs.items()
        }
        return {
            "requests": size, "finished_requests": int(finished),
            "tool_timing_only": timing_only, "mixed_untagged": mixed,
            "calls_per_sample": calls, "states_equal": True,
            "finished_checks_outside_timing": checked_calls, "costs": summaries,
        }
    finally:
        for runtime, *_ in fixtures.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="392d256")
    parser.add_argument("--baseline-engine", type=Path, required=True)
    parser.add_argument("--candidate-engine", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a fresh output are required")
    relative = Path("python/sglang/srt/managers")
    old_path = args.baseline_engine / relative / "scheduler.py"
    new_path = args.candidate_engine / relative / "scheduler.py"
    request_path = args.candidate_engine / relative / "schedule_batch.py"
    baseline, hashes = baseline_runtime(args.baseline)
    old_hook = native_method(old_path, "Scheduler", "_beliefkv_on_batch_completed")
    new_hook = native_method(new_path, "Scheduler", "_beliefkv_on_batch_completed")
    request_cls = type("CpuRequest", (NS,), {
        "finished": native_method(request_path, "Req", "finished"),
    })
    variants = {
        "baseline": (baseline, old_hook),
        "candidate": (NativeAdmissionRuntime, new_hook),
        "new_runtime_legacy_hook": (NativeAdmissionRuntime, old_hook),
        "legacy_runtime_new_hook": (baseline, new_hook),
    }
    source_paths = [
        ROOT / "beliefkv/runtime/sglang_v0520_runtime.py", Path(__file__),
        ROOT / "scripts/benchmark_native_prepare_path.py",
        ROOT / "scripts/benchmark_native_shared_path.py", old_path, new_path, request_path,
    ]
    sources = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in source_paths}
    cases = [
        measure(variants, request_cls, size=size, finished=finished,
                timing_only=timing_only, mixed=mixed, samples=args.samples)
        for size in (1, 8, 48) for finished in (False, True)
        for timing_only in (False, True) for mixed in (False, True)
    ]
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("a measured source changed")
    report = {
        "scope": (
            "Paired runtime/native scheduler completion callbacks on CPU. Native "
            "finished() and hook bodies are extracted without importing SGLang. "
            "No native result processor, GPU, DMA, launch, JCT or throughput measurement."
        ),
        "baseline_revision": args.baseline, "baseline_source_sha256": hashes,
        "source_sha256": sources, "samples": args.samples, "warmup_samples": 20,
        "cases": cases,
        "limitations": [
            "No-service unfinished callbacks use 100 calls per sample; completion cases reset before each single call.",
            "State resets, comparison, request construction and counted predicates are outside timings.",
            "The complete original batch is delivered in every arm; model final-stage prediction and diagnostic writer are disabled.",
            "Compatibility pairings are included; old runtimes without returned IDs retain their second native cleanup scan.",
            "These synthetic batch sizes and completion frequencies are not an online workload mixture.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {"requests": case["requests"], "finished": case["finished_requests"],
         "timing_only": case["tool_timing_only"], "mixed": case["mixed_untagged"],
         "baseline_us": case["costs"]["baseline"]["thread_cpu_us"]["p50"],
         "candidate_us": case["costs"]["candidate"]["thread_cpu_us"]["p50"]}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
