#!/usr/bin/env python3
"""Measure complete native prefetch bursts while retaining live path checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.benchmark_native_prefetch_load_cpu import fixture, run_once, variant


def signature(value):
    outcomes, callbacks, locks, operations = value
    return (
        tuple(tuple(item) for item in outcomes),
        callbacks,
        locks,
        tuple(
            (
                node_id,
                indices.tolist(),
                tuple((pool, extra.tolist()) for pool, extra in transfers),
            )
            for node_id, indices, transfers in operations
        ),
    )


def counted_fixture(implementation, case):
    state = fixture(implementation, case)
    lookup = state.cache.tree_core.node_by_id
    reads = [0]

    def node_by_id(node_id):
        reads[0] += 1
        return lookup(node_id)

    state.cache.tree_core.node_by_id = node_by_id
    return state, reads


def measure(implementations, case, *, samples, iterations):
    states = {
        name: counted_fixture(implementation, case)
        for name, implementation in implementations.items()
    }
    readings = {
        name: {"thread_cpu_us": [], "wall_us": []} for name in states
    }
    expected_reads = {}
    for sample in range(samples + 2):
        batch = {
            name: {"thread_cpu_us": [], "wall_us": []} for name in states
        }
        for iteration in range(iterations):
            order = list(states)
            if (sample + iteration) % 2:
                order.reverse()
            snapshots = {}
            for name in order:
                state, reads = states[name]
                reads[0] = 0
                costs, result, builds = run_once(state, case)
                snapshots[name] = signature(result), reads[0], builds
                if builds != case["burst"]:
                    raise AssertionError("unexpected transfer-spec build count")
                expected_reads[name] = reads[0]
                for clock, cost in costs.items():
                    batch[name][clock].append(cost)
            if snapshots["baseline"] != snapshots["candidate"]:
                raise AssertionError((case, snapshots))
        if sample >= 2:
            for name in readings:
                for clock in readings[name]:
                    readings[name][clock].append(mean(batch[name][clock]))
    measurements = {
        name: {
            clock: {"mean": mean(values), "p50": median(values), "samples": values}
            for clock, values in clocks.items()
        }
        for name, clocks in readings.items()
    }
    before = measurements["baseline"]["thread_cpu_us"]["p50"]
    after = measurements["candidate"]["thread_cpu_us"]["p50"]
    return {
        **case,
        "measurements": measurements,
        "thread_cpu_p50_reduction_percent": 100.0 * (1.0 - after / before),
        "node_lookup_calls": expected_reads,
        "spec_builds_per_variant": case["burst"],
        "outcomes_transfers_callbacks_locks_and_lookup_counts_equal": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-engine-root", type=Path, required=True)
    parser.add_argument("--candidate-engine-root", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.iterations < 1 or args.output.exists():
        parser.error("positive samples/iterations and a fresh output are required")
    implementations = {
        name: variant(path.resolve())
        for name, path in (
            ("baseline", args.baseline_engine_root),
            ("candidate", args.candidate_engine_root),
        )
    }
    cases = [
        {"depth": depth, "burst": burst, "tokens": tokens, "mamba": mamba}
        for depth, burst in (
            (8, 1), (16, 8), (32, 16), (64, 16), (64, 32), (256, 32), (256, 64)
        )
        for tokens in (128, 4096)
        for mamba in (False, True)
    ]
    results = []
    for case in cases:
        result = measure(
            implementations, case, samples=args.samples, iterations=args.iterations,
        )
        results.append(result)
        print(
            f"{case}: CPU {result['thread_cpu_p50_reduction_percent']:.2f}% lower",
            flush=True,
        )
    report = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(
            (ROOT / "scripts/benchmark_native_prefetch_load_cpu.py").read_bytes()
        ).hexdigest(),
        "source_sha256": {
            name: implementation.source_sha256
            for name, implementation in implementations.items()
        },
        "samples": args.samples,
        "iterations_per_sample": args.iterations,
        "cases": results,
        "scope": [
            "Actual extracted native prefetch, load-back and transfer-builder methods.",
            "Plain counted node lookup functions; no unittest.mock lookup costs.",
            "Outcomes, indices, callbacks, locks, lookups and spec counts match each iteration.",
            "Native allocation, component preparation, controller enqueue and commit use CPU stand-ins.",
            "The traversal is revalidated separately for every command, including after callbacks.",
            "No DMA, GPU time, online event frequency, JCT or throughput improvement is measured.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
