#!/usr/bin/env python3
"""Measure scheduler sampling dispatch with identical logical event traces."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def baseline_runtime(revision: str):
    path = "beliefkv/runtime/sglang_v0520_runtime.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    original = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    method = next(
        node for node in original.body
        if isinstance(node, ast.FunctionDef) and node.name == "scheduler_step"
    )
    original.name = "BaselineSamplingRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [method]
    namespace = dict(vars(runtime_module))
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[])),
            f"{revision}:{path}", "exec",
        ),
        namespace,
    )
    return namespace[original.name], hashlib.sha256(source.encode()).hexdigest()


def policy_row(row: dict) -> dict:
    return {
        key: value for key, value in row.items()
        if key not in ("sample_wall_ms", "shared_path_timing")
    }


def benchmark(
    revision: str, *, samples: int, cycles: int, defer_observations: bool = False,
) -> dict:
    baseline, digest = baseline_runtime(revision)
    cases = []
    clock = [1000.]
    configurations = (
        ("no_observation_worker", False, False, False),
        ("semantic_worker_only", False, False, True),
        ("opportunities", True, False, False),
        ("opportunities_and_semantic", True, False, True),
        ("terminal_and_opportunities", True, True, False),
        ("all_observations", True, True, True),
    )
    with (
        patch.object(runtime_module.time, "monotonic", new=lambda: clock[0]),
        patch.object(runtime_module.time, "time", new=lambda: clock[0] + 1_700_000_000),
    ):
        for queue_size in (48, 108):
            for rounds in (1, 8):
                for name, writer_enabled, terminal_enabled, semantic_enabled in configurations:
                    clock[0] = 1000.
                    variants = {}
                    costs = {
                        variant: {"wall_ms": [], "thread_cpu_ms": []}
                        for variant in ("baseline", "optimized")
                    }
                    if defer_observations:
                        for measurements in costs.values():
                            measurements["prelaunch_thread_cpu_ms"] = []
                            measurements["postlaunch_thread_cpu_ms"] = []
                    try:
                        for variant, cls in (
                            ("baseline", baseline), ("optimized", NativeAdmissionRuntime),
                        ):
                            runtime, queue = synthetic_runtime(
                                cls, CausalFrontierScheduler, queue_size, rounds,
                                profiling=True,
                            )
                            rows, polls = [], []
                            if writer_enabled:
                                runtime._opportunity_writer = NS(
                                    record=rows.append, close=lambda: None,
                                )
                            runtime._terminal_cache_diagnostics = terminal_enabled
                            if semantic_enabled:
                                runtime._semantic_worker = NS(
                                    poll=lambda polls=polls: polls.append(clock[0]) or (),
                                    ready=True, disabled=False, dropped=0,
                                    error="", close=lambda: None,
                                )
                            variants[variant] = runtime, queue, rows, polls
                        tick = 0
                        for sample in range(samples + 5):
                            start_tick = tick
                            order = list(variants)
                            if sample % 2:
                                order.reverse()
                            for variant in order:
                                runtime, queue, _, _ = variants[variant]
                                start_wall = time.perf_counter_ns()
                                start_cpu = time.thread_time_ns()
                                prelaunch_cpu_ns = postlaunch_cpu_ns = 0
                                for offset in range(cycles):
                                    clock[0] = 1000. + (start_tick + offset) * .001
                                    if not defer_observations:
                                        runtime.scheduler_step(queue)
                                        continue
                                    before = time.thread_time_ns()
                                    if variant == "optimized":
                                        runtime.scheduler_step(queue, defer_observations=True)
                                    else:
                                        runtime.scheduler_step(queue)
                                    launched = time.thread_time_ns()
                                    if variant == "optimized":
                                        runtime.sample_runtime_observations(queue)
                                    after = time.thread_time_ns()
                                    prelaunch_cpu_ns += launched - before
                                    postlaunch_cpu_ns += after - launched
                                measured = {
                                    "thread_cpu_ms": (
                                        time.thread_time_ns() - start_cpu
                                    ) / 1_000_000 / cycles,
                                    "wall_ms": (
                                        time.perf_counter_ns() - start_wall
                                    ) / 1_000_000 / cycles,
                                }
                                if defer_observations:
                                    measured.update(
                                        prelaunch_thread_cpu_ms=prelaunch_cpu_ns / 1_000_000 / cycles,
                                        postlaunch_thread_cpu_ms=postlaunch_cpu_ns / 1_000_000 / cycles,
                                    )
                                if sample >= 5:
                                    for metric, value in measured.items():
                                        costs[variant][metric].append(value)
                            tick += cycles
                            left, right = (variants[variant] for variant in variants)
                            if left[0].counts != right[0].counts:
                                raise AssertionError("runtime policy counters changed")
                            if left[0].physical_ledger.pending_count != right[0].physical_ledger.pending_count:
                                raise AssertionError("physical state changed")
                            if left[3] != right[3]:
                                raise AssertionError("semantic polling cadence changed")
                        left, right = (variants[variant] for variant in variants)
                        if [policy_row(row) for row in left[2]] != [
                            policy_row(row) for row in right[2]
                        ]:
                            raise AssertionError("observed policy rows or cadence changed")
                        summaries = {
                            variant: {
                                metric: distribution(values)
                                for metric, values in measured.items()
                            }
                            for variant, measured in costs.items()
                        }
                        cases.append({
                            "configuration": name, "queue_size": queue_size,
                            "retained_rounds": rounds,
                            "policy_rows_counters_and_semantic_cadence_equal": True,
                            "observed_rows": len(left[2]),
                            "semantic_polls": len(left[3]),
                            "costs_per_cycle": summaries,
                            "mean_thread_cpu_reduction_fraction": 1 - (
                                summaries["optimized"]["thread_cpu_ms"]["mean_ms"]
                                / summaries["baseline"]["thread_cpu_ms"]["mean_ms"]
                            ),
                        })
                    finally:
                        for runtime, _, _, _ in variants.values():
                            runtime.close()
    source_path = Path(runtime_module.__file__).resolve()
    if source_path != ROOT / "beliefkv/runtime/sglang_v0520_runtime.py":
        raise RuntimeError(f"benchmark imported a different worktree: {source_path}")
    return {
        "scope": (
            "CPU-only full scheduler maintenance with profiling enabled, real "
            "causal graphs and a 1ms logical cycle. The baseline retains its "
            "timing decorator. Observation sinks and idle semantic workers are "
            "stubs; no native socket, allocator, decode, DMA or GPU throughput "
            "is measured. Only sample_wall_ms and shared_path_timing are "
            "excluded from policy-row equality."
        ),
        "baseline_revision": revision, "baseline_runtime_sha256": digest,
        "updated_runtime_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "deferred_observations": defer_observations,
        "phase_partition_semantics": (
            "CPU fixture dispatch boundary only; no GPU is launched. The optimized "
            "observation call runs after that boundary using the same logical time "
            "and queue. Total cost and observation records are retained. This does "
            "not measure actual GPU overlap, hidden CPU time or throughput."
            if defer_observations else None
        ),
        "samples_per_case": samples, "cycles_per_sample": cycles,
        "warmup_samples": 5, "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="e317b68")
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--cycles", type=int, default=128)
    parser.add_argument("--defer-observations", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples <= 0 or args.cycles <= 0:
        parser.error("samples and cycles must be positive")
    report = benchmark(
        args.baseline_revision, samples=args.samples, cycles=args.cycles,
        defer_observations=args.defer_observations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
