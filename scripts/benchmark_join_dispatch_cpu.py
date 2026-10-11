#!/usr/bin/env python3
"""Compare JOIN dispatch planning on identical read-only CPU radix fixtures."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from scripts.benchmark_checkpoint_ancestry_cpu import fixture
from tests.test_wait_prefetch_batch import provisional_join_batch_fixture

SOURCE = Path("beliefkv/runtime/sglang_v0520_runtime.py")
METHODS = ("_roll_final_stage", "dispatch_join_prefetch")


def load_methods(source, label):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    methods = [node for node in cls.body
               if isinstance(node, ast.FunctionDef) and node.name in METHODS]
    for method in methods:
        method.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *methods,
    ], type_ignores=[])
    namespace = dict(vars(runtime_module))
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return {name: namespace[name] for name in METHODS}


def runtime_fixture(methods, depth, leaves):
    runtime, key, _, _, _ = provisional_join_batch_fixture()
    cache, anchors = fixture({
        "mode": "h2d", "depth": depth, "leaves": leaves, "output_tail_nodes": 0,
    })
    cache.token_to_kv_pool_allocator.available_size = lambda: 131072
    cache.prefetch_gpu_session_nodes = lambda **kwargs: ()
    runtime.attach_native_cache(cache)
    anchors = replace(anchors, key=key)
    runtime.snapshot_session_anchors = lambda *args, **kwargs: anchors
    for name, method in methods.items():
        setattr(runtime, name, MethodType(method, runtime))
    observation = runtime._wait_prefetch_opportunity(key)
    if observation is None or not observation.steps or observation.fits_current_free_lists is not True:
        raise AssertionError("CPU fixture has no fitting restore prefix")
    entries = cache.cache_controller.mem_pool_host.entry_map
    size = (observation.required_full_tokens * entries["kv"].host_pool.size_per_token
            + observation.required_mamba_slots * entries["mamba"].host_pool.size_per_token)
    runtime._h2d_samples.clear()
    runtime._h2d_samples.extend(((size, 10.),) * 3)
    inspections, submitted = [], []
    original_inspect = runtime._wait_prefetch_opportunity

    def inspect(key, **kwargs):
        inspections.append(kwargs)
        return original_inspect(key, **kwargs)

    def revalidate(key, steps, *, source):
        fresh = runtime._wait_prefetch_opportunity(
            key, max_steps=len(steps), fit_current_capacity=True,
        )
        if (fresh is None or fresh.steps != steps
                or not runtime._live_wait_prefetch(key, source)
                or not runtime._wait_prefetch_timing_valid(key, fresh, source)):
            raise AssertionError("dispatch changed the final validated prefix")
        submitted.extend(steps)
        return tuple(f"fixture-{index}" for index in range(len(steps)))

    runtime._wait_prefetch_opportunity = inspect
    runtime._issue_wait_prefetch_steps = revalidate
    return runtime, inspections, submitted


def costs(values):
    ordered = sorted(values)
    return {
        "mean_ms": statistics.mean(values), "p50_ms": statistics.median(values),
        "p90_ms": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
        "max_ms": max(values),
    }


def measure(methods, *, depth, leaves, iterations):
    variants = {name: runtime_fixture(loaded, depth, leaves)
                for name, loaded in methods.items()}
    samples = {name: {"wall": [], "thread_cpu": []} for name in methods}
    inspected = {}
    try:
        for iteration in range(iterations + 5):
            outputs = {}
            order = list(variants)
            if iteration % 2:
                order.reverse()
            for name in order:
                runtime, inspections, submitted = variants[name]
                runtime._join_ticket = None
                runtime._join_prefetch_issued.clear()
                runtime._final_stages["join"].issued_nodes = 0
                runtime._semantic_finished["child"] = (time.monotonic() * 1000., 3)
                inspections.clear()
                submitted.clear()
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                runtime.dispatch_join_prefetch()
                elapsed = {
                    "thread_cpu": (time.thread_time_ns() - cpu) / 1e6,
                    "wall": (time.perf_counter_ns() - wall) / 1e6,
                }
                outputs[name] = (tuple(submitted), runtime._join_ticket.phase,
                                 runtime._join_ticket.command_ids,
                                 runtime.physical_ledger.pending_count)
                inspected[name] = len(inspections)
                if inspections[-1].get("fit_current_capacity") is not True:
                    raise AssertionError("final live plan was not refreshed")
                if iteration >= 5:
                    for clock, value in elapsed.items():
                        samples[name][clock].append(value)
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError("validated steps or ticket state changed")
        return {
            "depth": depth, "leaves": leaves, "selected_nodes": len(outputs["optimized"][0]),
            "plans_and_ticket_state_equal": True, "inspections_per_dispatch": inspected,
            "costs": {name: {clock: costs(values) for clock, values in clocks.items()}
                      for name, clocks in samples.items()},
        }
    finally:
        for runtime, _, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline_revision}:{SOURCE.as_posix()}"], cwd=ROOT, text=True,
    )
    current = (ROOT / SOURCE).read_text()
    methods = {
        "baseline": load_methods(baseline, args.baseline_revision),
        "optimized": load_methods(current, str(ROOT / SOURCE)),
    }
    with patch.object(runtime_module.time, "monotonic", return_value=1000.):
        cases = [
            measure(methods, depth=depth, leaves=leaves, iterations=args.iterations)
            for depth in (8, 64, 256, 1024) for leaves in (1, 4)
        ]
    report = {
        "scope": (
            "CPU JOIN dispatch with actual read-only closure/plan inspection. "
            "Fixed session anchors, refreshed native prefix/timing at mock enqueue; "
            "no native allocator, transaction reservation, writer, DMA or CUDA. "
            "Decision time frozen; wall/thread clocks remain real. Timing decorators "
            "excluded. This is not GPU throughput or JCT evidence."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(current.encode()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations": args.iterations, "warmup_iterations": 5, "deployed": False,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(report["cases"]),
                      "all_plans_equal": True}))


if __name__ == "__main__":
    main()
