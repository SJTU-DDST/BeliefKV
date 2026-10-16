#!/usr/bin/env python3
"""Measure the added empty confirmed-JOIN dispatch check on CPU."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime


def measure(baseline, *, enabled: bool, profiling: bool, blocks: int) -> dict:
    runtimes = {
        "baseline": baseline(), "candidate": NativeAdmissionRuntime(),
    }
    samples = {name: [] for name in runtimes}
    calls = 128
    try:
        for runtime in runtimes.values():
            runtime.enable_final_stage_prefetch = enabled
            runtime._hotpath_timing = HotpathTiming(enabled=profiling)
            runtime._h2d_samples.extend(((1024, 2.),) * 3)
        for index in range(blocks + 10):
            order = tuple(runtimes) if index % 2 else tuple(reversed(runtimes))
            for name in order:
                runtime = runtimes[name]
                start = time.thread_time_ns()
                for _ in range(calls):
                    runtime.dispatch_join_prefetch()
                elapsed = (time.thread_time_ns() - start) / calls / 1000.
                if index >= 10:
                    samples[name].append(elapsed)
        if runtimes["baseline"].counts != runtimes["candidate"].counts:
            raise AssertionError("no-action dispatch counters changed")
        if any(
            runtime._join_ticket is not None or runtime.physical_ledger.pending_count
            for runtime in runtimes.values()
        ):
            raise AssertionError("no-action dispatch produced a ticket or transfer")
        return {
            "final_stage_enabled": enabled, "profiling": profiling,
            "blocks": blocks, "calls_per_block": calls,
            "thread_cpu_us_per_call": {
                name: {"mean": mean(values), "median": median(values)}
                for name, values in samples.items()
            },
            "added_mean_thread_cpu_us": mean(samples["candidate"]) - mean(samples["baseline"]),
            "same_counters_and_no_actions": True,
        }
    finally:
        for runtime in runtimes.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--blocks", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.blocks < 10 or args.output.exists():
        raise ValueError("require at least ten blocks and a fresh output")
    baseline, sources = baseline_runtime(args.baseline_revision)
    report = {
        "scope": (
            "Alternating CPU dispatch calls with no live final-stage or confirmed "
            "JOIN candidate. Both arms retain the real timing decorator. Measures "
            "only idle-path overhead; not active planning, DMA, GPU or JCT benefit."
        ),
        "baseline_revision": args.baseline_revision, "baseline_sources": sources,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (*sources, "scripts/benchmark_join_reentry_dispatch.py")
        },
        "cases": [
            measure(baseline, enabled=enabled, profiling=profiling, blocks=args.blocks)
            for enabled in (False, True) for profiling in (False, True)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report["cases"], indent=2))


if __name__ == "__main__":
    main()
