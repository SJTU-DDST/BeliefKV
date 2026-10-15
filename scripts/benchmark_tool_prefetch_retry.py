#!/usr/bin/env python3
"""Compare complete deferred tool-prefetch calls and ledger queries on a CPU."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution


def fixture(runtime_class, *, pending: int, mixed: bool, profiling: bool):
    runtime = runtime_class()
    runtime.enable_tool_prefetch = True
    runtime._hotpath_timing.enabled = profiling
    for index in range(pending):
        runtime.physical_ledger.register(PhysicalActionExpectation(
            command_id=f"command-{index}",
            action="PREFETCH_GPU" if mixed and index % 2 == 0 else "PREPARE_HOST",
            context_id=f"ctx-{index}", context_epoch=0,
            children=(PhysicalChildExpectation(
                index, (index,), (("kv", 10),), 10,
            ),),
            pool_bytes_per_token=(("kv", 10),),
        ))
    runtime._tool_prefetch_next_ms = time.monotonic() * 1000. + 3_600_000.
    return runtime


def benchmark(runtime_class, *, iterations: int, pending: int, mixed: bool, profiling: bool):
    variants = {
        name: fixture(cls, pending=pending, mixed=mixed, profiling=profiling)
        for name, cls in (
            ("baseline", runtime_class), ("candidate", NativeAdmissionRuntime),
        )
    }
    samples = {name: [] for name in variants}
    order = random.Random(23)
    warmup, calls_per_sample = 32, 50
    try:
        for iteration in range(iterations + warmup):
            names = list(variants)
            if order.getrandbits(1):
                names.reverse()
            for name in names:
                runtime = variants[name]
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                for _ in range(calls_per_sample):
                    runtime.dispatch_tool_prefetch()
                cost = {
                    "thread_cpu_ms": (time.thread_time_ns() - cpu) / 1_000_000. / calls_per_sample,
                    "wall_ms": (time.perf_counter_ns() - wall) / 1_000_000. / calls_per_sample,
                }
                if iteration >= warmup:
                    samples[name].append(cost)
        states = {
            name: {
                "graph": runtime.graph.snapshot(),
                "pending": {
                    command: asdict(runtime.physical_ledger.pending_expectation(command))
                    for command in (f"command-{index}" for index in range(pending))
                },
                "completed_actions": list(runtime.completed_physical_actions),
                "ticket": runtime._tool_ticket,
                "action_counts": {
                    action: runtime.physical_ledger.pending_action_count(action)
                    for action in ("PREPARE_HOST", "PREFETCH_GPU")
                },
            }
            for name, runtime in variants.items()
        }
        if states["baseline"] != states["candidate"]:
            raise AssertionError("graph, physical expectations, ticket or completion credit changed")
        if any(
            runtime.physical_ledger.pending_count != pending
            or runtime.counts["tool_prefetch_issued"] != 0
            for runtime in variants.values()
        ):
            raise AssertionError("a deferred scan changed or issued a physical action")
        costs = {
            name: {
                metric: distribution([item[metric] for item in values])
                for metric in ("thread_cpu_ms", "wall_ms")
            } for name, values in samples.items()
        }
        return {
            "pending_transactions": pending, "mixed_transfer_directions": mixed,
            "profiling_enabled": profiling, "iterations": iterations,
            "warmup_iterations": warmup, "calls_per_sample": calls_per_sample,
            "costs": costs,
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "graph_expectations_tickets_and_completion_credit_equal": True,
            "deferred_scan_counters": {
                name: runtime.counts["tool_prefetch_scan_deferred"]
                for name, runtime in variants.items()
            },
            "physical_actions_issued": 0,
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        raise ValueError("positive iterations and a new report path are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/hotpath_timing.py",
        "scripts/benchmark_tool_prefetch_retry.py",
        "scripts/benchmark_native_prepare_path.py",
    )
    sources = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
    }
    baseline, baseline_sources = baseline_runtime(args.baseline_revision)
    cases = [
        benchmark(
            baseline, iterations=args.iterations, pending=pending,
            mixed=mixed, profiling=profiling,
        )
        for profiling in (False, True)
        for pending, mixed in ((0, False), (1, False), (16, False), (16, True), (64, True))
    ]
    if any(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
        for path, digest in sources.items()
    ):
        raise ValueError("measured source changed during comparison")
    report = {
        "status": "tool_retry_cpu_comparison_not_gpu_or_jct_acceptance",
        "observed_at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision,
        "baseline_sources": baseline_sources, "candidate_sources": sources,
        "cases": cases,
        "limitations": [
            "Complete deferred dispatch calls with actual registered physical expectations, without a GPU.",
            "Pending population sizes are scenarios, not a replay of V59's population distribution.",
            "State and credit must match; overlapping defer reasons intentionally change counter precedence.",
            "CPU-thread costs are not historical wall sums or promised JCT savings.",
            "Tests separately cover partial/full ACKs, expiry, invalid receipts and immediate new-hint rearming.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "pending_transactions", "mixed_transfer_directions", "profiling_enabled",
            "thread_cpu_mean_reduction_fraction",
        )} for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
