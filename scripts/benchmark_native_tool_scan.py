#!/usr/bin/env python3
"""Measure tool-candidate scan cadence under a fixed CPU-only event timeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def fixture(runtime_class, workflows: int):
    runtime, requests = synthetic_runtime(
        runtime_class, CausalFrontierScheduler, workflows, 1,
    )
    runtime.enable_tool_prefetch = True
    runtime.predictor_sha256 = "a" * 64
    for request in requests:
        key = runtime.context_sessions[request.beliefkv_metadata["context_id"]]
        invocation = runtime.graph.invocations[key.invocation_id]
        invocation.state = InvocationState.WAIT_TOOL
        runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
            key, 30_000., 60_000., 120_000., 1_000_000., 1_600_000.,
            runtime.predictor_sha256, invocation.updated_ts_ms,
        )
    return runtime


def benchmark(*, revision: str, workflows: int, iterations: int, tick_ms: float):
    baseline, digests = baseline_runtime(revision)
    variants = {
        name: fixture(cls, workflows)
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    samples = {name: [] for name in variants}
    clock = [1000.]
    try:
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            for iteration in range(iterations):
                clock[0] = 1000. + iteration * tick_ms / 1000.
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                for name in names:
                    runtime = variants[name]
                    start = time.perf_counter_ns()
                    runtime.dispatch_tool_prefetch()
                    samples[name].append((time.perf_counter_ns() - start) / 1_000_000.)
                    if runtime._tool_ticket is not None or runtime.physical_ledger.pending_count:
                        raise AssertionError("long-wait fixture must not issue a transfer")
        costs = {name: distribution(values) for name, values in samples.items()}
        return {
            "scope": "CPU candidate scans outside transfer windows; not GPU throughput",
            "baseline_revision": revision,
            "baseline_source_sha256": digests,
            "tool_wait_contexts": workflows,
            "iterations": iterations,
            "scheduler_tick_ms": tick_ms,
            "timeline_ms": (iterations - 1) * tick_ms,
            "physical_actions": 0,
            "costs": costs,
            "total_cpu_ms": {name: sum(values) for name, values in samples.items()},
            "candidate_checks": {
                name: runtime.counts["tool_prefetch_not_in_time_window"]
                for name, runtime in variants.items()
            },
            "optimized_scans": variants["optimized"].counts["tool_prefetch_scans"],
            "optimized_deferred": variants["optimized"].counts["tool_prefetch_scan_deferred"],
            "mean_reduction_fraction": 1. - (
                costs["optimized"]["mean_ms"] / costs["baseline"]["mean_ms"]
            ),
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="aa93dde")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--tick-ms", type=float, default=2.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.workflows <= 512 or args.iterations < 1 or not 0 < args.tick_ms < 100:
        raise ValueError("positive workload/iterations and sub-100ms ticks required")
    report = benchmark(
        revision=args.baseline_revision, workflows=args.workflows,
        iterations=args.iterations, tick_ms=args.tick_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "contexts": args.workflows,
        "baseline_cpu_ms": report["total_cpu_ms"]["baseline"],
        "optimized_cpu_ms": report["total_cpu_ms"]["optimized"],
        "mean_reduction_fraction": report["mean_reduction_fraction"],
        "optimized_scans": report["optimized_scans"],
    }))


if __name__ == "__main__":
    main()
