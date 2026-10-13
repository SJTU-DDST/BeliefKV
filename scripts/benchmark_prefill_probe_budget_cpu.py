#!/usr/bin/env python3
"""Measure slot-bounded residency probing with real native reentry on CPU."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from scripts.benchmark_native_reentry_cpu import fixture as reentry_fixture, variant
from scripts.benchmark_native_shared_path import (
    baseline_classes, distribution, synthetic_runtime,
)


def measure(classes, implementation, case, clock, iterations):
    variants = {}
    costs = {name: {"wall_ms": [], "thread_cpu_ms": []} for name in classes}
    reads = {name: 0 for name in classes}
    try:
        for name, (runtime_class, frontier_class) in classes.items():
            runtime, queue = synthetic_runtime(
                runtime_class, frontier_class, case["queue"], 1,
            )
            cache, input_request = reentry_fixture(implementation, {
                "tokens": 32768, "depth": case["depth"], "leaves": 1, "page_size": 1,
            })
            anchors = cache.session_refs.snapshot_latest_session_leaf_anchors()
            node = cache.tree_core.node_by_id(anchors[0][1][0][0])
            while node.parent is not None:
                node.component_data[0].value = None
                node.component_data[2].value = None
                node = node.parent
            for request in queue:
                request.origin_input_ids = input_request.origin_input_ids

            def inspect(request, *, label=name, native=cache):
                reads[label] += 1
                return implementation.inspect(native, request)

            cache.inspect_beliefkv_reentry = inspect
            runtime.attach_native_cache(cache)
            runtime.enable_resident_first = True
            runtime.enable_final_stage_priority = False
            runtime._residency_budget = PrefetchResidencyBudget(
                case["slots"], 1024 ** 3, source="native_next_prefill",
            )
            runtime._prefill_cycle_active = True
            variants[name] = (runtime, queue)
        for iteration in range(iterations + 5):
            clock[0] = 1000. + iteration * .051
            plans = {}
            order = list(variants)
            if iteration % 2:
                order.reverse()
            for name in order:
                runtime, queue = variants[name]
                runtime._prefill_residency_scan = None
                runtime._reentry_observations.clear()
                runtime._visible_since = {request.rid: clock[0] for request in queue}
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                plans[name] = tuple(runtime.plan_native_prefill(
                    queue, running_batch=None, adder=None,
                ) for _ in range(2))
                observed = {
                    "wall_ms": (time.perf_counter_ns() - wall) / 1e6,
                    "thread_cpu_ms": (time.thread_time_ns() - cpu) / 1e6,
                }
                if iteration >= 5:
                    for metric, value in observed.items():
                        costs[name][metric].append(value)
            if plans["baseline"] != plans["optimized"]:
                raise AssertionError("identical Host-only checkpoints changed causal ordering")
        measurements = {
            name: {metric: distribution(values) for metric, values in clocks.items()}
            for name, clocks in costs.items()
        }
        before = measurements["baseline"]["thread_cpu_ms"]["mean_ms"]
        after = measurements["optimized"]["thread_cpu_ms"]["mean_ms"]
        return {
            **case, "host_only_checkpoint_order_equal": True,
            "native_inspection_counts_including_warmup": reads,
            "measurements": measurements, "raw_samples": costs,
            "mean_thread_cpu_reduction_fraction": 1 - after / before,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        parser.error("require positive iterations and a new output path")
    baseline, frontier, hashes = baseline_classes(args.baseline_commit)
    implementation = variant(args.engine_root)
    classes = {
        "baseline": (baseline, frontier),
        "optimized": (runtime_module.NativeAdmissionRuntime, CausalFrontierScheduler),
    }
    clock, results = [1000.], []
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("BELIEFKV_")
    }
    with patch.dict(os.environ, environment, clear=True), \
         patch.object(runtime_module.time, "monotonic", new=lambda: clock[0]):
        for queue in (32, 156):
            for depth in (8, 64):
                for slots in (1, 4, 8):
                    clock[0] = 1000.
                    results.append(measure(classes, implementation, {
                        "queue": queue, "depth": depth, "slots": slots,
                    }, clock, args.iterations))
    report = {
        "schema_version": 1,
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_commit": args.baseline_commit,
        "baseline_source_sha256": hashes,
        "qualified_runtime_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes(),
        ).hexdigest(),
        "native_source_sha256": implementation.source_sha256,
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations": args.iterations, "warmup": 5, "planning_calls_per_iteration": 2,
        "cases": results,
        "scope": (
            "CPU-only complete causal planning on synthetic Host-only checkpoints with "
            "the actual native RadixKey and inspect_beliefkv_reentry methods. "
            "Slot budgets are fixture inputs; native pool geometry is not measured. "
            "Ordering equality applies only to these Host-only fixtures. Fewer head "
            "probes can alter mixed-residency ordering; eight rotating tail probes, "
            "aging, promotion and selected-request validation remain. No DMA, "
            "allocator release, GPU throughput or JCT improvement is established."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for result in results:
        before = result["measurements"]["baseline"]["thread_cpu_ms"]["mean_ms"]
        after = result["measurements"]["optimized"]["thread_cpu_ms"]["mean_ms"]
        print(
            f"queue={result['queue']} depth={result['depth']} slots={result['slots']}: "
            f"{before:.3f}->{after:.3f}ms thread CPU, "
            f"{100 * result['mean_thread_cpu_reduction_fraction']:.1f}% lower"
        )


if __name__ == "__main__":
    main()
