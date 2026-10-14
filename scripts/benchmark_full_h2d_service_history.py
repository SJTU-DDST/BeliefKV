#!/usr/bin/env python3
"""Measure cached estimate cost while preserving previously supported outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime


def receipt(runtime, *, shape, delay):
    runtime.on_native_transfer_commit(NS(
        direction="h2d", status="completed", child_commits=(),
        actual_bytes=100, submit_to_ack_ms=delay,
        num_tokens_by_pool=(("kv", int(shape != "mamba")),
                            ("mamba", int(shape != "full"))),
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=301)
    parser.add_argument("--calls-per-block", type=int, default=100)
    args = parser.parse_args()
    if args.output.exists() or args.blocks < 1 or args.calls_per_block < 1:
        raise ValueError("positive repetitions and a new output required")
    baseline, sources = baseline_runtime(args.baseline_revision)
    cases = []
    for case in ("empty", "legacy", "full", "hybrid"):
        runtimes = {"baseline": baseline(), "candidate": NativeAdmissionRuntime()}
        costs = {name: [] for name in runtimes}
        try:
            for runtime in runtimes.values():
                if case == "legacy":
                    runtime._h2d_samples.extend((100, delay) for delay in (10., 20., 30.))
                elif case != "empty":
                    for delay in (10., 20., 30.):
                        receipt(runtime, shape=case, delay=delay)
            query_shape = "hybrid" if case == "hybrid" else "full"
            outputs = {
                name: runtime._estimate_h2d_service(100, shape=query_shape)
                for name, runtime in runtimes.items()
            }
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError("existing supported/unsupported estimate changed")
            for block in range(args.blocks + 10):
                names = list(runtimes)
                if block % 2:
                    names.reverse()
                for name in names:
                    method = runtimes[name]._estimate_h2d_service
                    started = time.thread_time_ns()
                    for _ in range(args.calls_per_block):
                        estimate = method(100, shape=query_shape)
                    elapsed = (time.thread_time_ns() - started) / args.calls_per_block / 1000.
                    if estimate != outputs[name]:
                        raise AssertionError("cached estimate changed")
                    if block >= 10:
                        costs[name].append(elapsed)
            medians = {name: median(values) for name, values in costs.items()}
            cases.append({
                "case": case, "query_shape": query_shape, "outputs_identical": True,
                "mean_thread_cpu_us": {name: mean(values) for name, values in costs.items()},
                "p50_thread_cpu_us": medians,
                "p50_added_thread_cpu_us": medians["candidate"] - medians["baseline"],
            })
        finally:
            for runtime in runtimes.values():
                runtime.close()
    report = {
        "status": "cpu_cached_estimate_fixture_not_gpu_or_end_to_end_gain",
        "baseline_revision": args.baseline_revision, "baseline_sources": sources,
        "candidate_runtime_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes(),
        ).hexdigest(),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "blocks": args.blocks, "calls_per_block": args.calls_per_block,
        "cases": cases,
        "limitations": [
            "CPU fixtures without event writer, native memory allocator or GPU.",
            "Alternating order and real monotonic clock; unpinned and under concurrent workload.",
            "Measures cached existing estimates; uncached supplemental computation is covered by runtime replay separately.",
            "No scheduler call rate, exposed stall or workflow JCT benefit is inferred.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(cases), flush=True)


if __name__ == "__main__":
    main()
