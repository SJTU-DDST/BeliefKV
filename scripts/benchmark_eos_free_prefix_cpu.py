#!/usr/bin/env python3
"""Measure first native enqueue and cold-reclaim order, without GPU transfers."""

from __future__ import annotations

import argparse
from datetime import datetime
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution
from tests.test_eos_free_prefix import pressured_prefix


def measure(classes, *, node_count, source, free_full, samples):
    durations = {name: [] for name in classes}
    first_enqueue = {name: [] for name in classes}
    behavior = {}
    for iteration in range(samples + 10):
        for name in ("baseline", "candidate")[::1 if iteration % 2 else -1]:
            with pressured_prefix(
                cls=classes[name], source=source, free_full=free_full,
                node_count=node_count,
            ) as case:
                started = time.thread_time_ns()
                case.dispatch()
                elapsed = (time.thread_time_ns() - started) / 1000.
                calls = [row for row in case.timeline if row["kind"] == "enqueue"]
                assert len(calls) == 1
                call = calls[0]
                signature = {
                    "call_order": [row["kind"] for row in case.timeline],
                    "first_enqueued_nodes": call["nodes"],
                    "first_enqueued_full_tokens": call["full_tokens"],
                    "pending_commands": case.runtime.physical_ledger.pending_count,
                    "cold_pass_pending_counts": [
                        row["pending"] for row in case.timeline if row["kind"] == "reclaim"
                    ],
                    "cold_pass_full_targets": [
                        row["full_tokens"] for row in case.timeline if row["kind"] == "reclaim"
                    ],
                }
                assert signature == behavior.setdefault(name, signature)
                assert not case.runtime.completed_physical_actions
                assert not case.runtime.physical_disabled
                assert case.runtime.counts["native_admitted"] == 0
                if iteration >= 10:
                    durations[name].append(elapsed)
                    first_enqueue[name].append((call["cpu_ns"] - started) / 1000.)
    positive = source == "observed_eos" and 5 <= free_full < 4 * node_count + 1
    if positive:
        assert behavior["candidate"]["call_order"] == ["enqueue", "reclaim"]
        assert behavior["baseline"]["call_order"] == ["reclaim", "enqueue"]
        prefix_count = (free_full - 1) // 4
        assert behavior["candidate"]["first_enqueued_nodes"] == tuple(range(1, prefix_count + 1))
        assert behavior["candidate"]["cold_pass_pending_counts"] == [prefix_count]
    else:
        assert behavior["baseline"] == behavior["candidate"]
    return {
        "source": source, "node_count": node_count, "initial_free_full_tokens": free_full,
        "behavior": behavior,
        "dispatch_cpu_us": {
            name: {**distribution(values), "max_us": max(values), "samples_us": values}
            for name, values in durations.items()
        },
        "first_native_enqueue_entry_cpu_us": {
            name: {**distribution(values), "max_us": max(values), "samples_us": values}
            for name, values in first_enqueue.items()
        },
        "non_eos_or_unpressured_behavior_equal": not positive,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="3c8504c")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a fresh output are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "tests/test_eos_free_prefix.py", "tests/test_wait_prefetch_batch.py",
        "scripts/benchmark_eos_free_prefix_cpu.py",
    )
    sources = {
        str(ROOT / path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
    }
    before, bindings = baseline_runtime(args.baseline)
    cases = [
        measure(
            {"baseline": before, "candidate": NativeAdmissionRuntime},
            node_count=depth, source=source, free_full=free_full, samples=args.samples,
        )
        for depth in (4, 52)
        for source, free_full in (
            ("observed_eos", 2 * depth + 1),
            ("observed_eos", 4 * depth + 1),
            ("observed_eos", 1),
            ("confirmed_join", 2 * depth + 1),
            ("tool_wait", 2 * depth + 1),
        )
    ]
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
               for path, value in sources.items())
    report = {
        "status": "cpu_enqueue_order_measurement_not_gpu_acceptance",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "baseline_revision": args.baseline, "baseline_sources": bindings,
        "source_sha256": sources, "gc_enabled": gc.isenabled(),
        "samples_per_variant": args.samples, "cases": cases,
        "scope": (
            "One dispatch with a CPU-shaped native cache and constant-cost mock "
            "reclaim. Positive cases submit different first payloads; the remaining "
            "tail H2D is not included. Enqueue entry is not first DMA submission. "
            "No timing of real cold scans, PCIe/GPU overlap, full recovery, "
            "additional batches, throughput or JCT is inferred."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "cases": [{
            "source": case["source"], "nodes": case["node_count"],
            "free_full": case["initial_free_full_tokens"],
            "dispatch_p50_us": {
                name: values["p50_us"] for name, values in case["dispatch_cpu_us"].items()
            },
            "behavior": case["behavior"],
        } for case in cases],
    }, indent=2))


if __name__ == "__main__":
    main()
