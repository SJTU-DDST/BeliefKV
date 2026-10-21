#!/usr/bin/env python3
"""Measure complete CPU planning/selection for Full and the simple JOIN control."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import json
import os
from pathlib import Path
from statistics import median
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import synthetic_runtime


def fixture(mode, profile, size):
    environment = arm_environment({"PREFILL_SCHEDULING_POLICY": profile}, mode)
    environment.update(
        BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH="0",
        BELIEFKV_ENABLE_TOOL_PREFETCH="0", BELIEFKV_ONLINE_TOOL_TIMING="0",
        BELIEFKV_BORROWABLE_PARENT_HEADROOM="0",
    )
    with patch.dict(os.environ, environment):
        return synthetic_runtime(NativeAdmissionRuntime, CausalFrontierScheduler, size, 4)


def measure(size, samples):
    fixtures = {
        name: fixture(mode, profile, size) for name, mode, profile in (
            ("full_workflow", "full", "workflow"),
            ("full_join_priority", "full", "join_priority"),
            ("join_only", "join_priority_only", "join_priority"),
        )
    }
    values = {name: {"wall_us": [], "cpu_us": []} for name in fixtures}
    calls = 10
    try:
        for sample in range(samples + 20):
            observations = {}
            order = tuple(fixtures)
            for name in order if sample % 2 else reversed(order):
                runtime, queue = fixtures[name]
                now = time.monotonic()
                runtime._visible_since = {rid: now for rid in runtime.visible}
                running = NS(reqs=[])
                adder = NS(max_running_requests=8, rem_input_tokens=8192, page_size=16)
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                for _ in range(calls):
                    runtime.scheduler_step(queue, defer_observations=True)
                    inspection = runtime.inspect_native_prefill_queue(queue)
                    plan = runtime.plan_native_prefill(
                        queue, running_batch=running, adder=adder,
                        _queue_inspection=inspection,
                    )
                    selection = select_native_prefill_candidates(
                        queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
                    )
                elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                observations[name] = (
                    [request.rid for request in selection.candidates], selection.rejected,
                    runtime.graph.snapshot(), dict(runtime._join_priority_pending),
                    runtime.physical_ledger.pending_count,
                )
                if sample >= 20:
                    values[name]["wall_us"].append(elapsed_wall)
                    values[name]["cpu_us"].append(elapsed_cpu)
            if observations["full_join_priority"] != observations["join_only"]:
                raise AssertionError("simple Full/control selection or causal state differs")
            if any(observation[-1] for observation in observations.values()):
                raise AssertionError("CPU planning issued a physical action")
        return {
            "queue_size": size, "calls_per_sample": calls,
            "simple_full_and_join_only_selection_graph_pending_equal": True,
            "no_native_cache_or_dma": True,
            "costs": {
                name: {
                    clock: {"p50_us": median(rows), "samples_us": rows}
                    for clock, rows in clocks.items()
                } for name, clocks in values.items()
            },
        }
    finally:
        for runtime, _ in fixtures.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a new output are required")
    result = {
        "scope": "synthetic CPU planning/selection only; no GPU, DMA, JCT or quality claim",
        "normal_gc": True, "retains_outliers": True, "samples": args.samples,
        "source_sha256": {
            path: sha256_file(ROOT / path) for path in (
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "beliefkv/runtime/sglang_v0520_admission.py",
                "beliefkv/experiments/internal_comparison.py",
                "scripts/benchmark_join_priority_kv_cpu.py",
            )
        },
        "cases": [measure(size, args.samples) for size in (8, 48, 128)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(result["cases"])}, indent=2))


if __name__ == "__main__":
    main()
