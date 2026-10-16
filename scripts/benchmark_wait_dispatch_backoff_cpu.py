#!/usr/bin/env python3
"""Compare complete maintenance/dispatch cycles under existing retry deadlines."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_prepare_path import fixture
from scripts.benchmark_native_shared_path import distribution


SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"


def baseline_class(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    cls = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    cls.name = "BaselineDispatchRuntime"
    cls.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    cls.body = [
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_after_forward"
    ]
    if len(cls.body) != 1:
        raise ValueError("baseline dispatcher missing")
    namespace = dict(vars(runtime_module))
    tree = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        cls,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace[cls.name], hashlib.sha256(source.encode()).hexdigest()


def measure(baseline, *, workflows, spacing_ms, profiling, iterations):
    variants = {}
    for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
        value = fixture(cls, workflows=workflows, depth=16, backed=True,
                        host_full=10_000_000, leases=0)
        runtime = value[0]
        runtime.enable_tool_prefetch = True
        runtime.enable_prepare_host = True
        runtime._hotpath_timing = HotpathTiming(enabled=profiling)
        variants[name] = value
    costs = {name: {"wall": [], "thread_cpu": []} for name in variants}
    dispatcher = {name: {"wall": [], "thread_cpu": []} for name in variants}
    native_reads = {name: Counter() for name in variants}
    clock = [time.monotonic() + 1.]
    final_states = {}
    try:
        with patch.object(runtime_module.time, "monotonic", lambda: clock[0]):
            for iteration in range(iterations + 20):
                clock[0] += spacing_ms / 1000.
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                outputs = {}
                for name in names:
                    runtime, queue, reads, actions, observations = variants[name]
                    actions.clear()
                    observations.clear()
                    before_reads = reads.copy()
                    started_cpu, started = time.thread_time_ns(), time.perf_counter_ns()
                    runtime.scheduler_step(queue, defer_observations=True)
                    dispatch_cpu, dispatch_started = time.thread_time_ns(), time.perf_counter_ns()
                    runtime.dispatch_after_forward(queue)
                    ended_cpu, ended = time.thread_time_ns(), time.perf_counter_ns()
                    if iteration >= 20:
                        costs[name]["thread_cpu"].append((ended_cpu - started_cpu) / 1e6)
                        costs[name]["wall"].append((ended - started) / 1e6)
                        dispatcher[name]["thread_cpu"].append((ended_cpu - dispatch_cpu) / 1e6)
                        dispatcher[name]["wall"].append((ended - dispatch_started) / 1e6)
                        native_reads[name].update(reads - before_reads)
                    outputs[name] = {
                        "actions": list(actions),
                        "counts": dict(runtime.counts),
                        "deadlines": (
                            runtime._tool_prefetch_next_ms,
                            runtime._join_prepare_next_ms,
                            runtime._tool_prepare_next_ms,
                        ),
                        "published": runtime._native_cache.beliefkv_join_pressure_candidates,
                        "reads": dict(reads),
                        "observations": [
                            {field: value for field, value in row.items()
                             if field not in ("ts_ms", "monotonic_ms")}
                            for row in observations
                        ],
                    }
                if outputs["baseline"] != outputs["candidate"]:
                    raise AssertionError((workflows, spacing_ms, profiling, iteration, outputs))
                final_states = outputs
        timing_counts = {}
        for name, (runtime, *_) in variants.items():
            phases = runtime._hotpath_timing.snapshot()["phases"]
            timing_counts[name] = {
                phase: phases.get(phase, {}).get("count", 0)
                for phase in (
                    "scheduler_maintenance", "post_launch_physical",
                    "tool_prefetch", "join_prepare", "tool_prepare",
                )
            }
        for phase in ("scheduler_maintenance", "post_launch_physical"):
            if timing_counts["baseline"][phase] != timing_counts["candidate"][phase]:
                raise AssertionError(("parent timing coverage changed", phase, timing_counts))
        summaries = {
            name: {field: distribution(values) for field, values in clocks.items()}
            for name, clocks in costs.items()
        }
        dispatch_summaries = {
            name: {field: distribution(values) for field, values in clocks.items()}
            for name, clocks in dispatcher.items()
        }
        return {
            "workflows": workflows, "decision_clock_step_ms": spacing_ms,
            "profiling": profiling, "iterations": iterations,
            "outputs_actions_counts_deadlines_reads_and_publication_equal": True,
            "costs": summaries, "dispatch_costs": dispatch_summaries,
            "timing_counts_including_warmup": timing_counts,
            "native_reads": {name: dict(values) for name, values in native_reads.items()},
            "physical_planning_calls_including_warmup": final_states["candidate"]["counts"].get(
                "physical_planning_after_forward", 0,
            ),
            "thread_cpu_reduction_fraction": 1 - (
                summaries["candidate"]["thread_cpu"]["mean_ms"]
                / summaries["baseline"]["thread_cpu"]["mean_ms"]
            ),
            "dispatch_thread_cpu_reduction_fraction": 1 - (
                dispatch_summaries["candidate"]["thread_cpu"]["mean_ms"]
                / dispatch_summaries["baseline"]["thread_cpu"]["mean_ms"]
            ),
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline, baseline_digest = baseline_class(args.baseline_revision)
    cases = [
        measure(baseline, workflows=count, spacing_ms=spacing,
                profiling=profiling, iterations=args.iterations)
        for count in (24, 156)
        for spacing in (1, 3, 60)
        for profiling in (False, True)
    ]
    report = {
        "scope": (
            "Identical complete CPU maintenance/dispatch fixture cycles. Existing retry "
            "deadlines, observations, actions, counters and live native reads are compared "
            "each iteration. Parent timing covers both arms; skipped no-op child phases "
            "have fewer actual calls. No model worker, writer I/O, DMA, GPU or JCT claim."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_runtime_sha256": baseline_digest,
        "candidate_runtime_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": cases,
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps([
        {field: case[field] for field in (
            "workflows", "decision_clock_step_ms", "profiling",
            "thread_cpu_reduction_fraction", "dispatch_thread_cpu_reduction_fraction",
        )}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
