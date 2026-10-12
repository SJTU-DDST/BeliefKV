#!/usr/bin/env python3
"""Compare transfer estimates on recorded controller histories, without a GPU."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, deque
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import beliefkv.runtime.native_transfer_service as service


SOURCE = "beliefkv/runtime/native_transfer_service.py"


def baseline_estimator(revision: str):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    function = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "estimate_native_service"
    )
    namespace = dict(vars(service))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), function,
    ], type_ignores=[])), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace["estimate_native_service"], hashlib.sha256(source.encode()).hexdigest()


def recorded_histories(path: Path):
    history = deque(maxlen=256)
    snapshots = []
    digest, prefix_bytes, eligible = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            digest.update(line)
            prefix_bytes += len(line)
            row = json.loads(line)
            size, delay = row.get("actual_bytes"), row.get("submit_to_ack_ms")
            direction = row.get("direction")
            if (
                row.get("status") != "completed" or direction not in ("h2d", "d2h")
                or type(size) is not int or size <= 0
                or type(delay) not in (int, float)
                or not math.isfinite(delay) or not 0 < delay <= 60_000
            ):
                continue
            units = row["num_tokens_by_pool"]
            history.append(service.NativeServiceSample(
                size, delay, direction,
                service.pool_shape(units.get("kv", 0), units.get("mamba", 0)),
                row.get("enqueue_to_submit_ms"),
            ))
            eligible += 1
            if eligible in (256, 8192, 16384, 32768):
                snapshots.append((eligible, tuple(history)))
            if eligible == 32768:
                break
    if history and (not snapshots or snapshots[-1][0] != eligible):
        snapshots.append((eligible, tuple(history)))
    return snapshots, {
        "path": str(path.resolve()), "read_prefix_bytes": prefix_bytes,
        "read_prefix_sha256": digest.hexdigest(), "eligible_receipts": eligible,
        "history_limit": history.maxlen,
        "semantics": "Completed recorded receipts; each snapshot uses the last 256 eligible rows.",
    }


def distribution(values):
    return {"mean_us": mean(values), "p50_us": median(values)}


def benchmark(revision: str, transfer_log: Path, iterations: int):
    baseline, baseline_digest = baseline_estimator(revision)
    histories, evidence = recorded_histories(transfer_log)
    histories.append(("legacy_tuples", tuple(
        (size, 25. + index) for index, size in enumerate(
            (500_000, 1_000_000, 2_000_000, 4_000_000, 16_000_000, 64_000_000) * 10
        )
    )))
    cases = []
    for snapshot, samples in histories:
        for direction in ("h2d", "d2h"):
            for shape in ("full", "mamba", "hybrid", "unknown"):
                for size in (1_000_000, 32_000_000, 128_000_000):
                    variants = {
                        "baseline": baseline, "optimized": service.estimate_native_service,
                    }
                    timings = {name: {"thread_cpu": [], "wall": []} for name in variants}
                    expected = baseline(samples, size, direction=direction, shape=shape)
                    for iteration in range(iterations + 10):
                        order = list(variants)
                        if iteration % 2:
                            order.reverse()
                        for name in order:
                            cpu_start, wall_start = time.thread_time_ns(), time.perf_counter_ns()
                            observed = variants[name](
                                samples, size, direction=direction, shape=shape,
                            )
                            wall_us = (time.perf_counter_ns() - wall_start) / 1000.
                            cpu_us = (time.thread_time_ns() - cpu_start) / 1000.
                            if observed != expected:
                                raise AssertionError((snapshot, direction, shape, size))
                            if iteration >= 10:
                                timings[name]["thread_cpu"].append(cpu_us)
                                timings[name]["wall"].append(wall_us)
                    costs = {
                        name: {clock: distribution(values) for clock, values in clocks.items()}
                        for name, clocks in timings.items()
                    }
                    cases.append({
                        "snapshot": snapshot, "samples": len(samples),
                        "sample_shapes": dict(Counter(
                            row.shape if isinstance(row, service.NativeServiceSample) else "legacy"
                            for row in samples
                        )),
                        "direction": direction, "shape": shape, "size_bytes": size,
                        "estimate": asdict(expected) if expected is not None else None,
                        "estimates_equal": True, "costs": costs,
                        "thread_cpu_mean_reduction": 1. - (
                            costs["optimized"]["thread_cpu"]["mean_us"]
                            / costs["baseline"]["thread_cpu"]["mean_us"]
                        ),
                    })
    return {
        "schema_version": 1, "baseline_revision": revision,
        "baseline_source_sha256": baseline_digest,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "transfer_evidence": evidence, "iterations_per_case": iterations,
        "scope": (
            "CPU estimates on fixed recorded histories and legacy tuples; identical outputs "
            "including unknown support. No live scheduler, allocation, DMA or JCT evidence."
        ),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--transfer-log", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.baseline_revision, args.transfer_log, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "cases": len(report["cases"]), "estimates_equal": True,
        "transfer_evidence": report["transfer_evidence"],
    }, indent=2))


if __name__ == "__main__":
    main()
