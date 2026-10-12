#!/usr/bin/env python3
"""Compare scheduler maintenance plus tool scans with live CPU lease fixtures."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import distribution


def measure(baseline, *, workflows, leases, tick_ms, iterations):
    clock = [1000.]
    variants = {}
    with patch("time.monotonic", new=lambda: clock[0]):
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime)):
            runtime, _, reads, _, observations = fixture(
                cls, workflows=workflows, depth=4, backed=True,
                host_full=1_000_000, leases=leases,
            )
            runtime.enable_tool_prefetch = True
            runtime.prefetch_lead_ms = 2000.
            runtime.predictor_sha256 = "a" * 64
            for key in runtime.context_sessions.values():
                invocation = runtime.graph.invocations[key.invocation_id]
                invocation.state = InvocationState.WAIT_TOOL
                runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
                    key, 30_000., 60_000., 120_000., 1_000_000., 1_600_000.,
                    runtime.predictor_sha256, invocation.updated_ts_ms,
                )
            reads.clear()
            observations.clear()
            variants[name] = runtime, reads, observations
    samples = {
        name: {phase: {"wall": [], "thread_cpu": []} for phase in ("scan", "maintenance_and_scan")}
        for name in variants
    }
    try:
        with patch("time.monotonic", new=lambda: clock[0]):
            for iteration in range(iterations + 5):
                clock[0] = 1000. + iteration * tick_ms / 1000.
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                for name in names:
                    runtime, _, _ = variants[name]
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    runtime.scheduler_step(defer_observations=True)
                    scan_wall, scan_cpu = time.perf_counter_ns(), time.thread_time_ns()
                    runtime.dispatch_tool_prefetch()
                    end_cpu, end_wall = time.thread_time_ns(), time.perf_counter_ns()
                    if iteration >= 5:
                        for phase, start_wall, start_cpu in (
                            ("scan", scan_wall, scan_cpu),
                            ("maintenance_and_scan", wall, cpu),
                        ):
                            samples[name][phase]["wall"].append((end_wall - start_wall) / 1e6)
                            samples[name][phase]["thread_cpu"].append((end_cpu - start_cpu) / 1e6)
                    if runtime._tool_ticket is not None or runtime.physical_ledger.pending_count:
                        raise AssertionError("outside-window fixture issued a transfer")
                outputs = {
                    name: {
                        "leases": [asdict(item) for item in runtime._prefetch_service_leases.values()],
                        "counts": dict(runtime.counts),
                        "records": observations,
                    } for name, (runtime, _, observations) in variants.items()
                }
                if outputs["baseline"] != outputs["optimized"]:
                    raise AssertionError(f"changed maintenance or scan outputs at {iteration}")
                if any(len(row["leases"]) != leases for row in outputs.values()):
                    raise AssertionError("fixture unexpectedly lost lease identity")
        costs = {
            name: {phase: {kind: distribution(values) for kind, values in clocks.items()}
                   for phase, clocks in phases.items()}
            for name, phases in samples.items()
        }
        canonical = json.dumps(outputs["optimized"], sort_keys=True).encode()
        return {
            "workflows": workflows, "leases": leases, "tick_ms": tick_ms,
            "iterations": iterations, "outputs_equal": True,
            "canonical_output_sha256": hashlib.sha256(canonical).hexdigest(),
            "scans": variants["optimized"][0].counts["tool_prefetch_scans"],
            "deferred": variants["optimized"][0].counts["tool_prefetch_scan_deferred"],
            "costs": costs,
            "native_reads": {name: dict(reads) for name, (_, reads, _) in variants.items()},
            "thread_cpu_reduction": {
                phase: 1. - costs["optimized"][phase]["thread_cpu"]["mean_ms"]
                / costs["baseline"][phase]["thread_cpu"]["mean_ms"]
                for phase in ("scan", "maintenance_and_scan")
            },
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        raise ValueError("iterations must be positive")
    baseline, digests = baseline_runtime(args.baseline_revision)
    cases = [
        measure(baseline, workflows=workflows, leases=leases, tick_ms=tick, iterations=args.iterations)
        for workflows, leases in ((8, 0), (8, 4), (156, 0), (156, 4), (156, 16), (156, 48))
        for tick in (2., 20.)
    ]
    report = {
        "scope": "CPU maintenance plus outside-window tool scans; not live frequency or GPU throughput",
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests,
        "optimized_runtime_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes()
        ).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "physical_actions": 0, "cases": cases,
        "semantics": [
            "Both arms execute scheduler maintenance on every tick and retain live native lease checks.",
            "Only a redundant tool-scan refresh is removed when no forecast enters the start window.",
            "Native cache fixtures expose real graph/session/node conditions, with no GPU or DMA.",
            "Alternating arm order and thread CPU separate scheduling noise from measured call wall.",
            "Small regressions and raw distributions remain; no fixture frequency is extrapolated to JCT.",
        ],
    }
    if args.output.exists():
        raise ValueError("preserve prior benchmark")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in ("workflows", "leases", "tick_ms", "thread_cpu_reduction")}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
