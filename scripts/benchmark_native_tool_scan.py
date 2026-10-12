#!/usr/bin/env python3
"""Measure tool-candidate scan cadence under a fixed CPU-only event timeline."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime
from scripts.benchmark_tool_survival_lookup_cpu import recorded_curves


def fixture(runtime_class, workflows: int, forecasts=()):
    runtime, requests = synthetic_runtime(
        runtime_class, CausalFrontierScheduler, workflows, 1,
    )
    runtime.enable_tool_prefetch = True
    runtime.prefetch_lead_ms = 2000.
    runtime.predictor_sha256 = "a" * 64
    for index, request in enumerate(requests):
        key = runtime.context_sessions[request.beliefkv_metadata["context_id"]]
        invocation = runtime.graph.invocations[key.invocation_id]
        invocation.state = InvocationState.WAIT_TOOL
        row = forecasts[index % len(forecasts)] if forecasts else {}
        runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
            key, 30_000., 60_000., 120_000., 1_000_000., 1_600_000.,
            runtime.predictor_sha256, invocation.updated_ts_ms,
            release_cdf=tuple(tuple(point) for point in row.get("release_cdf", ())),
            residual_duration_mass=tuple(
                tuple(point) for point in row.get("residual_duration_mass", ())
            ),
            residual_support_detail=row.get("residual_support_detail"),
        )
    return runtime


def benchmark(*, revision: str, workflows: int, iterations: int, tick_ms: float,
              forecast_log: Path | None = None, freeze_gc: bool = False):
    baseline, digests = baseline_runtime(revision)
    forecasts, evidence = (), None
    if forecast_log is not None:
        rows, evidence = recorded_curves(
            forecast_log, max_forecasts=8192, max_curves=4096,
        )
        probe = fixture(NativeAdmissionRuntime, 1)
        key = next(iter(probe.tool_wait_hints.values())).key
        try:
            selected = []
            for row in rows:
                hint = NativeToolWaitHint(
                    key, row["p10_ms"], row["p50_ms"], row["p90_ms"],
                    1_000_000., 1_600_000., "a" * 64,
                    release_cdf=tuple(tuple(point) for point in row["release_cdf"]),
                    residual_duration_mass=tuple(
                        tuple(point) for point in row.get("residual_duration_mass", ())
                    ),
                    residual_support_detail=row.get("residual_support_detail"),
                )
                remaining = [
                    hint.remaining_quantile(.5, now_ms=1_000_000. + age)
                    for age in range(0, int(iterations * tick_ms) + 51, 50)
                ]
                if all(value is None or value > 2000. for value in remaining):
                    selected.append(row)
            if not selected:
                raise ValueError("no recorded curve stays outside the fixture's 2s window")
            forecasts = tuple(selected)
        finally:
            probe.close()
    variants = {
        name: fixture(cls, workflows, forecasts)
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    samples = {name: [] for name in variants}
    cpu_samples = {name: [] for name in variants}
    clock = [1000.]
    gc_samples = {name: [] for name in variants}
    gc_start = {}
    active_variant = [None]

    def observe_gc(phase, info):
        name = active_variant[0]
        if name is None:
            return
        if phase == "start":
            gc_start[name] = time.thread_time_ns()
        elif name in gc_start:
            gc_samples[name].append({
                "generation": info["generation"],
                "thread_cpu_ms": (time.thread_time_ns() - gc_start.pop(name)) / 1_000_000.,
            })

    gc.callbacks.append(observe_gc)
    if freeze_gc:
        gc.collect()
        gc.freeze()
    try:
        with patch("time.monotonic", new=lambda: clock[0]):
            for iteration in range(iterations):
                clock[0] = 1000. + iteration * tick_ms / 1000.
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                for name in names:
                    runtime = variants[name]
                    active_variant[0] = name
                    start = time.perf_counter_ns()
                    cpu_start = time.thread_time_ns()
                    runtime.dispatch_tool_prefetch()
                    cpu_samples[name].append(
                        (time.thread_time_ns() - cpu_start) / 1_000_000.
                    )
                    samples[name].append((time.perf_counter_ns() - start) / 1_000_000.)
                    active_variant[0] = None
                    if runtime._tool_ticket is not None or runtime.physical_ledger.pending_count:
                        raise AssertionError("long-wait fixture must not issue a transfer")
        costs = {name: distribution(values) for name, values in samples.items()}
        return {
            "scope": "CPU candidate scans outside transfer windows; not GPU throughput",
            "baseline_revision": revision,
            "baseline_source_sha256": digests,
            "tool_wait_contexts": workflows,
            "prefetch_lead_ms": 2000.,
            "recorded_prefix": evidence,
            "eligible_recorded_curve_pool": len(forecasts),
            "recorded_curves_repeated_across_fixture_contexts": min(len(forecasts), workflows),
            "recorded_curve_selection": (
                "Only CDFs whose conditional P50 is unsupported or stays above 2s on this timeline; "
                "not sampled workload frequencies. Both arms share the current curve implementation."
                if forecasts else None
            ),
            "iterations": iterations,
            "scheduler_tick_ms": tick_ms,
            "timeline_ms": (iterations - 1) * tick_ms,
            "physical_actions": 0,
            "costs": costs,
            "total_cpu_ms": {name: sum(values) for name, values in samples.items()},
            "legacy_total_cpu_ms_field_is_call_wall": True,
            "total_thread_cpu_ms": {
                name: sum(values) for name, values in cpu_samples.items()
            },
            "thread_cpu_reduction_fraction": 1. - (
                sum(cpu_samples["optimized"]) / sum(cpu_samples["baseline"])
            ),
            "clock_fixture": "plain monotonic lambda; no Mock call recording",
            "gc_frozen_after_fixture_initialization": freeze_gc,
            "gc_collections_during_measured_calls": gc_samples,
            "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "optimized_runtime_source_sha256": hashlib.sha256(
                Path(sys.modules[NativeAdmissionRuntime.__module__].__file__).read_bytes()
            ).hexdigest(),
            "candidate_checks": {
                name: runtime.counts["tool_prefetch_not_in_time_window"]
                for name, runtime in variants.items()
            },
            "optimized_scans": variants["optimized"].counts["tool_prefetch_scans"],
            "optimized_deferred": variants["optimized"].counts["tool_prefetch_scan_deferred"],
            "optimized_survival_queries_deferred": variants["optimized"].counts[
                "tool_prefetch_survival_deferred"
            ],
            "mean_reduction_fraction": 1. - (
                costs["optimized"]["mean_ms"] / costs["baseline"]["mean_ms"]
            ),
        }
    finally:
        gc.callbacks.remove(observe_gc)
        if freeze_gc:
            gc.unfreeze()
        for runtime in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="aa93dde")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--tick-ms", type=float, default=2.)
    parser.add_argument("--forecast-log", type=Path)
    parser.add_argument(
        "--freeze-gc", action="store_true",
        help="Collect and freeze initialized objects before timing, as in the live server.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.workflows <= 512 or args.iterations < 1 or not 0 < args.tick_ms < 100:
        raise ValueError("positive workload/iterations and sub-100ms ticks required")
    report = benchmark(
        revision=args.baseline_revision, workflows=args.workflows,
        iterations=args.iterations, tick_ms=args.tick_ms,
        forecast_log=args.forecast_log,
        freeze_gc=args.freeze_gc,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "contexts": args.workflows,
        "baseline_cpu_ms": report["total_cpu_ms"]["baseline"],
        "optimized_cpu_ms": report["total_cpu_ms"]["optimized"],
        "mean_reduction_fraction": report["mean_reduction_fraction"],
        "thread_cpu_ms": report["total_thread_cpu_ms"],
        "thread_cpu_reduction_fraction": report["thread_cpu_reduction_fraction"],
        "optimized_scans": report["optimized_scans"],
    }))


if __name__ == "__main__":
    main()
