#!/usr/bin/env python3
"""Compare cached common-window CPU cost and diagnose subset cost fallback."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_transfer_service import NativeServiceSample
from beliefkv.runtime.sglang_v0520_runtime import (
    NativeAdmissionRuntime, _WaitPrefetchServiceShape,
)
from scripts.benchmark_native_prepare_path import baseline_runtime
from tests.test_wait_prefetch_batch import batch_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior CPU evidence")
    baseline, sources = baseline_runtime(args.baseline_revision)
    holder, _, opportunity, cache, _ = batch_fixture("tool_wait")
    step = replace(opportunity.steps[0], include_mamba=False)
    smaller = replace(
        opportunity, step=step, steps=(step,),
        required_full_tokens=4, required_mamba_slots=0,
    )
    shape = _WaitPrefetchServiceShape.from_opportunity(opportunity)
    runtimes = {"baseline": baseline(), "candidate": NativeAdmissionRuntime()}
    cases = []
    try:
        for runtime in runtimes.values():
            runtime.attach_native_cache(cache)
            runtime._native_service_samples.extend(
                NativeServiceSample(180, delay, shape="hybrid", enqueue_to_submit_ms=5.)
                for delay in (50., 60., 70.)
            )
        for name, payload, supplied_shape in (
            ("supported_common_window", opportunity, None),
            ("unsupported_common_window", smaller, None),
            ("reduced_payload_cost_fallback", smaller, shape),
        ):
            outputs = {
                variant: runtime._h2d_start_window(payload)
                for variant, runtime in runtimes.items()
            }
            if supplied_shape is None and outputs["baseline"] != outputs["candidate"]:
                raise AssertionError("common timing behavior changed")
            costs = {variant: [] for variant in runtimes}
            for block in range(211):
                order = list(runtimes)
                if block % 2:
                    order.reverse()
                for variant in order:
                    runtime = runtimes[variant]
                    started = time.thread_time_ns()
                    for _ in range(100):
                        if variant == "candidate" and supplied_shape is not None:
                            output = runtime._h2d_start_window(payload, service_shape=supplied_shape)
                        else:
                            output = runtime._h2d_start_window(payload)
                    elapsed = (time.thread_time_ns() - started) / 100_000.
                    if supplied_shape is None and output != outputs[variant]:
                        raise AssertionError("cached estimate changed")
                    if supplied_shape is not None and variant == "candidate":
                        if output is None or output.service_ceiling_bytes != 180:
                            raise AssertionError("subset fallback not exercised")
                    if block >= 10:
                        costs[variant].append(elapsed)
            medians = {variant: median(values) for variant, values in costs.items()}
            cases.append({
                "case": name, "common_outputs_identical": supplied_shape is None,
                "p50_thread_cpu_us": medians,
                "mean_thread_cpu_us": {variant: mean(values) for variant, values in costs.items()},
                "p50_added_thread_cpu_us": medians["candidate"] - medians["baseline"],
            })
        report = {
            "status": "cpu_window_fixture_not_scheduler_or_gpu_gain",
            "baseline_revision": args.baseline_revision, "baseline_sources": sources,
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (
                    "beliefkv/runtime/sglang_v0520_runtime.py",
                    "beliefkv/runtime/native_transfer_policy.py",
                    "scripts/benchmark_wait_prefetch_subset_service.py",
                    "tests/test_wait_prefetch_service_subset.py",
                )
            },
            "blocks": 201, "calls_per_block": 100, "cases": cases,
            "limitations": [
                "CPU-only fixtures with no event writer, cache allocation, action or GPU.",
                "Alternating order under concurrent experiment load; thread placement is uncontrolled.",
                "The fallback comparison intentionally adds supported timing where baseline has none.",
                "Per-window CPU does not measure dispatch shape construction, live call frequency or JCT savings.",
            ],
        }
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps(cases), flush=True)
    finally:
        for runtime in (*runtimes.values(), holder):
            runtime.close()


if __name__ == "__main__":
    main()
