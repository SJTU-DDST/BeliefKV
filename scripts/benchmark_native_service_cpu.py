#!/usr/bin/env python3
"""Measure service-completion telemetry CPU cost with exact output comparison."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import beliefkv.runtime.v0520_native_telemetry as telemetry

SOURCE = "beliefkv/runtime/v0520_native_telemetry.py"


def baseline_method(revision):
    source = subprocess.check_output(["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True)
    tree = ast.parse(source)
    cls = next(item for item in tree.body if isinstance(item, ast.ClassDef)
               and item.name == "NativeReactiveTelemetry")
    method = next(item for item in cls.body if isinstance(item, ast.FunctionDef)
                  and item.name == "on_completed")
    namespace = dict(vars(telemetry))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace["on_completed"], hashlib.sha256(source.encode()).hexdigest()


def fixture(batch_size, *, removed):
    emitted, audit = [], telemetry.NativeReactiveTelemetry.__new__(telemetry.NativeReactiveTelemetry)
    audit._poll_restore_wait = lambda: None
    audit._observe_targeted_pair = lambda *args: None
    audit._emit = lambda stream, record: emitted.append(record)
    audit._cache = None
    audit._previous_completed_mono = None
    audit._reported_output_tokens = {}
    audit._completed = set()
    audit._active = set()
    samples = [
        {"request_id": f"r-{index}", "output_tokens_before": index, "token_delta": 0}
        for index in range(batch_size)
    ]
    audit._launched = {1: {
        "sample_id": "fixture", "launch_mono": 100., "phase": "decode",
        "batch_size": batch_size, "request_samples": samples,
        "mamba_forward_candidates": [],
    }}
    requests = [
        NS(rid=f"r-{index}", output_ids=range(index + 1), finished=lambda: False)
        for index in reversed(range(batch_size))
        if index % 4 or not removed
    ]
    return audit, NS(forward_iter=1, reqs=requests), emitted


def benchmark(revision, iterations):
    baseline, digest = baseline_method(revision)
    current = telemetry.NativeReactiveTelemetry.on_completed
    results = []
    original_mono, original_wall = telemetry.time.monotonic, telemetry.time.time
    telemetry.time.monotonic, telemetry.time.time = lambda: 101., lambda: 200.
    try:
        for size in (16, 48):
            for removed in (False, True):
                timings = {"baseline": [], "optimized": []}
                for iteration in range(iterations + 20):
                    outputs = {}
                    variants = [("baseline", baseline), ("optimized", current)]
                    if iteration % 2:
                        variants.reverse()
                    for name, method in variants:
                        audit, batch, emitted = fixture(size, removed=removed)
                        start = time.perf_counter_ns()
                        method(audit, batch)
                        elapsed = (time.perf_counter_ns() - start) / 1000.
                        if iteration >= 20:
                            timings[name].append(elapsed)
                        outputs[name] = (emitted, audit._reported_output_tokens)
                    if outputs["baseline"] != outputs["optimized"]:
                        raise AssertionError("completion telemetry or token attribution changed")
                p50 = {name: median(values) for name, values in timings.items()}
                results.append({
                    "batch_size": size, "removed_requests": removed,
                    "p50_cpu_us": p50, "median_reduction_fraction": 1 - p50["optimized"] / p50["baseline"],
                })
    finally:
        telemetry.time.monotonic, telemetry.time.time = original_mono, original_wall
    return {
        "scope": "CPU service-completion fixture with no writer, DMA or GPU; not end-to-end speedup",
        "baseline_revision": revision, "baseline_source_sha256": digest,
        "iterations": iterations, "outputs_equal": True, "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="1893bc5")
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.baseline_revision, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
