#!/usr/bin/env python3
"""Compare identical PREPARE ranks with live guards and changing service evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_service import load_native_service_seed
from beliefkv.runtime.sglang_v0520_physical import ShadowBackupStep
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import distribution


def benchmark_case(baseline, samples, *, size_count, receipt_every, iterations):
    variants = {
        name: fixture(
            cls, workflows=1, depth=4, backed=False,
            host_full=1_000_000, leases=0, service_samples=samples,
        )
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    timings = {
        name: {"wall_ms": [], "thread_cpu_ms": []} for name in variants
    }
    outcomes = Counter()
    try:
        for runtime, *_ in variants.values():
            cache = runtime._native_cache
            cache.host_pool_group.entry_map["kv"].host_pool.size_per_token = 20480
            cache.host_pool_group.entry_map["mamba"].host_pool.size_per_token = 64389120
            key = runtime.context_sessions["ctx-wf-0-root"]
            inv = runtime.graph.invocations[key.invocation_id]
            inv.state = InvocationState.WAIT_TOOL
            inv.active_tool_calls["tool"] = NS()
            runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
                key, 5000., 10000., 15000., 1000000., 1020000., "a" * 64,
                inv.updated_ts_ms,
            )
        for iteration in range(iterations + 10):
            order = list(variants)
            if iteration % 2:
                order.reverse()
            ranks = {}
            guard = iteration % 4
            now = 1000. + (4.95 if guard == 1 else 0.)
            for name in order:
                runtime, *_ = variants[name]
                if receipt_every and iteration % receipt_every == 0:
                    sample = samples[iteration % len(samples)]
                    full_units = (
                        sample.size_bytes - 64389120 if sample.shape == "hybrid"
                        else sample.size_bytes if sample.shape == "full" else 0
                    ) // 20480
                    runtime.on_native_transfer_commit(NS(
                        status="completed", direction=sample.direction,
                        node_ids=(), child_commits=(),
                        actual_bytes=sample.size_bytes,
                        submit_to_ack_ms=sample.submit_to_ack_ms,
                        enqueue_to_submit_ms=sample.enqueue_to_submit_ms,
                        num_tokens_by_pool=(
                            ("kv", full_units),
                            ("mamba", int(sample.shape in ("mamba", "hybrid"))),
                        ),
                    ))
                node = runtime._native_cache.tree_core.node_by_id(3)
                units = 128 * (iteration % size_count + 1)
                node.component_data[0].value = range(units)
                node.component_data[0].host_value = range(units) if guard == 3 else None
                key = runtime.context_sessions["ctx-wf-0-root"]
                step = ShadowBackupStep(key, 3, node.creation_time, 3, node.creation_time)
                headroom = NS(
                    host_full_free_tokens=0 if guard == 2 else 1_000_000,
                    host_mamba_free_slots=622,
                )
                with patch(
                    "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                    return_value=now,
                ):
                    cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                    rank = runtime._prepare_step_rank(
                        step, headroom, full_pressure=True, mamba_pressure=False,
                    )
                    wall_ms = (time.perf_counter_ns() - wall) / 1e6
                    cpu_ms = (time.thread_time_ns() - cpu) / 1e6
                ranks[name] = rank
                if iteration >= 10:
                    timings[name]["wall_ms"].append(wall_ms)
                    timings[name]["thread_cpu_ms"].append(cpu_ms)
            if ranks["baseline"] != ranks["optimized"]:
                raise AssertionError(f"live PREPARE rank changed at {iteration}: {ranks}")
            outcomes[f"guard_{guard}:{'eligible' if ranks['optimized'] is not None else 'rejected'}"] += 1
        costs = {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in timings.items()
        }
        return {
            "distinct_size_count": size_count, "receipt_every_calls": receipt_every,
            "iterations": iterations, "ranks_equal": True,
            "live_guards": [
                "tool residual window", "Host capacity", "settled Host copy",
            ],
            "outcomes": dict(outcomes), "costs": costs,
            "thread_cpu_reduction": (
                1 - costs["optimized"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "optimized_estimates": {
                key: value for key, value in variants["optimized"][0].counts.items()
                if "service_estimate" in key
            },
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--seed-artifact", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=501)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        raise ValueError("positive iterations and a new output path required")
    seed_sha = hashlib.sha256(args.seed_artifact.read_bytes()).hexdigest()
    samples = load_native_service_seed(str(args.seed_artifact), seed_sha)
    baseline, baseline_sha = baseline_runtime(args.baseline_revision)
    started = time.time()
    cases = [
        benchmark_case(
            baseline, samples, size_count=count, receipt_every=receipt,
            iterations=args.iterations,
        )
        for count in (1, 8, 32)
        for receipt in (0, 8, 1)
    ]
    paths = [
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_prepare_service_cache.py",
        "tests/test_sglang_v0520_runtime.py",
    ]
    report = {
        "scope": (
            "CPU PREPARE-rank fixture using the frozen service seed; live guards "
            "and new receipts retained. No native DMA, workload repetition rate, "
            "critical-path saving or end-to-end speedup is inferred."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": baseline_sha,
        "candidate_source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in paths
        },
        "seed_artifact": str(args.seed_artifact.resolve()), "seed_sha256": seed_sha,
        "service_history_samples": len(samples),
        "started_ts_ms": started * 1000., "finished_ts_ms": time.time() * 1000.,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        key: case[key] for key in (
            "distinct_size_count", "receipt_every_calls", "thread_cpu_reduction",
            "optimized_estimates",
        )
    } for case in cases], indent=2))


if __name__ == "__main__":
    main()
