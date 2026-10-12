#!/usr/bin/env python3
"""Measure complete waiting PREPARE/refresh paths on identical CPU fixtures."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_prepare_path import fixture
from scripts.benchmark_native_shared_path import distribution

SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"


def baseline_class(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    original = next(
        item for item in ast.parse(source).body
        if isinstance(item, ast.ClassDef) and item.name == "NativeAdmissionRuntime"
    )
    original.name = "BaselineWaitRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [
        item for item in original.body if isinstance(item, ast.FunctionDef)
        and item.name in ("dispatch_join_prepare", "scheduler_step", "_scan_unhinted_wait")
    ]
    namespace = dict(vars(runtime_module))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        original,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace["BaselineWaitRuntime"], hashlib.sha256(source.encode()).hexdigest()


def measure_case(baseline, *, workflows, mode, iterations):
    variants = {}
    for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime)):
        runtime, queue, reads, actions, observations = fixture(
            cls, workflows=workflows, depth=16, backed=mode == "backed",
            host_full=10_000_000, leases=0,
        )
        submissions = []
        runtime._tool_timing_only = True
        runtime._submit_tool_wait = submissions.append
        for index, request in enumerate(queue):
            key = runtime.visible[request.rid]
            if index % 2 == 0:
                runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_TOOL
        if mode == "no_join":
            for invocation in runtime.graph.invocations.values():
                if invocation.state is InvocationState.WAIT_JOIN:
                    invocation.state = InvocationState.READY
        runtime.graph._graph_version += 1
        variants[name] = (runtime, queue, reads, actions, observations, submissions)
    phases = ("maintenance", "join_prepare")
    timings = {
        name: {phase: {"thread_cpu": [], "wall": []} for phase in phases}
        for name in variants
    }
    cold_reads = {name: 0 for name in variants}
    try:
        clock = [time.monotonic() + 2.]
        with patch.object(runtime_module.time, "monotonic", lambda: clock[0]):
            for iteration in range(iterations + 5):
                clock[0] += .06
                order = list(variants)
                if iteration % 2:
                    order.reverse()
                outputs = {}
                for name in order:
                    runtime, queue, reads, actions, observations, submissions = variants[name]
                    if mode == "churn":
                        key = runtime.visible[queue[0].rid]
                        runtime.graph.apply(RuntimeEvent(
                            event_id=f"change-{iteration}", workflow_id=key.root_workflow_id,
                            invocation_id=key.invocation_id, ts_ms=clock[0] * 1000.,
                            kind=(
                                RuntimeEventKind.TOOL_START if iteration % 2 == 0
                                else RuntimeEventKind.TOOL_END
                            ),
                        ))
                    actions.clear()
                    observations.clear()
                    submissions.clear()
                    before = reads["session_snapshot"]
                    for phase, call in (
                        ("maintenance", lambda: runtime.scheduler_step(queue, defer_observations=True)),
                        ("join_prepare", lambda: runtime.dispatch_join_prepare(queue)),
                    ):
                        cpu_start, wall_start = time.thread_time_ns(), time.perf_counter_ns()
                        call()
                        wall_ms = (time.perf_counter_ns() - wall_start) / 1e6
                        cpu_ms = (time.thread_time_ns() - cpu_start) / 1e6
                        if iteration >= 5:
                            timings[name][phase]["wall"].append(wall_ms)
                            timings[name][phase]["thread_cpu"].append(cpu_ms)
                    if iteration >= 5:
                        cold_reads[name] += reads["session_snapshot"] - before
                    outputs[name] = (
                        tuple(actions), tuple(submissions),
                        tuple(runtime._native_cache.beliefkv_join_pressure_candidates),
                    )
                if outputs["baseline"] != outputs["optimized"]:
                    raise AssertionError((workflows, mode, iteration, outputs))
    finally:
        for runtime, *_ in variants.values():
            runtime.close()
    costs = {
        name: {phase: {clock: distribution(samples) for clock, samples in clocks.items()}
               for phase, clocks in per_phase.items()}
        for name, per_phase in timings.items()
    }
    return {
        "workflows": workflows, "mode": mode, "iterations": iterations,
        "checkpoint_depth": 16, "decision_clock_step_ms": 60,
        "prepare_actions_tool_refresh_rotation_and_publication_equal": True,
        "native_session_snapshot_calls": cold_reads, "costs": costs,
        "thread_cpu_reduction": {
            phase: 1. - costs["optimized"][phase]["thread_cpu"]["mean_ms"]
            / costs["baseline"][phase]["thread_cpu"]["mean_ms"] for phase in phases
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline, digest = baseline_class(args.baseline_revision)
    cases = [
        measure_case(baseline, workflows=count, mode=mode, iterations=args.iterations)
        for count, mode in (
            (24, "unbacked"), (156, "unbacked"), (512, "unbacked"),
            (156, "backed"), (512, "backed"), (156, "no_join"),
            (156, "churn"), (512, "churn"),
        )
    ]
    report = {
        "schema_version": 1, "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digest,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": (
            "CPU dispatch and maintenance on identical graph/cache fixtures. Real checkpoint "
            "planning and live headroom reads; model submission and physical enqueue are stubbed. "
            "No native allocator, DMA, GPU throughput or JCT evidence."
        ),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "cases": len(cases), "outputs_equal": True,
        "reductions": [
            {"workflows": case["workflows"], "mode": case["mode"],
             **case["thread_cpu_reduction"]} for case in cases
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
