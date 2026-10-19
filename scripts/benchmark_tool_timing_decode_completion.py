#!/usr/bin/env python3
"""Compare decode completion bookkeeping while preserving finished-request state."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from tests.test_sglang_v0520_runtime import req


def measure(runtime_class, batch_size, iterations):
    runtime = runtime_class()
    runtime._tool_timing_only = True
    requests = [req(str(index)) for index in range(batch_size)]
    inputs = [1] * 65536
    finished = [False]
    for request in requests:
        request.session_id, request.session_generation = request.rid, 1
        request.origin_input_ids, request.output_ids = inputs, [2] * 64
        request.finished = lambda: finished[0]
        runtime.register_visible_request(request)
    batch = NS(reqs=requests)
    try:
        for _ in range(100):
            runtime.on_batch_completed(batch)
        cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
        for _ in range(iterations):
            runtime.on_batch_completed(batch)
        wall = (time.perf_counter_ns() - wall) / iterations / 1000.
        cpu = (time.thread_time_ns() - cpu) / iterations / 1000.
        partial_snapshots = len(runtime._context_tokens)
        finished[0] = True
        runtime.on_batch_completed(batch)
        assert not runtime.visible
        assert len(runtime._context_tokens) == batch_size
        assert all(
            counts == (0, 65536, 64, False)
            for counts in runtime._context_tokens.values()
        )
        return {
            "wall_us_per_callback": wall,
            "thread_cpu_us_per_callback": cpu,
            "partial_context_snapshots": partial_snapshots,
            "completed_contexts": dict(runtime._context_tokens),
            "visible_after_completion": len(runtime.visible),
            "physical_disabled": runtime.physical_disabled,
        }
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or min(args.iterations, args.repetitions) < 1
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "999"
    ):
        parser.error("fresh output, positive work and CUDA_VISIBLE_DEVICES=999 required")
    before, digests = baseline_runtime(args.baseline_revision)
    variants = {"baseline": before, "candidate": NativeAdmissionRuntime}
    cases = []
    for size in (1, 24, 48):
        samples = {name: [] for name in variants}
        for repeat in range(args.repetitions):
            names = list(variants)
            if repeat % 2:
                names.reverse()
            for name in names:
                samples[name].append(measure(variants[name], size, args.iterations))
        completed_equal = all(
            before["completed_contexts"] == after["completed_contexts"]
            and before["visible_after_completion"] == after["visible_after_completion"]
            and before["physical_disabled"] == after["physical_disabled"]
            for before, after in zip(samples["baseline"], samples["candidate"])
        )
        if not completed_equal:
            raise ValueError("completed request state differs")
        cases.append({
            "batch_size": size, "samples": samples,
            "completed_request_state_equal": completed_equal,
            "p50": {
                name: {
                    field: median(row[field] for row in rows)
                    for field in ("wall_us_per_callback", "thread_cpu_us_per_callback")
                }
                for name, rows in samples.items()
            },
        })
    report = {
        "at": datetime.now().astimezone().isoformat(),
        "scope": "CPU callback only, with no pending service observation; not GPU or JCT improvement",
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests,
        "candidate_runtime_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes()
        ).hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations": args.iterations, "repetitions": args.repetitions,
        "cases": cases,
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {"batch_size": row["batch_size"], "p50": row["p50"],
         "completed_request_state_equal": row["completed_request_state_equal"]}
        for row in cases
    ], indent=2))


if __name__ == "__main__":
    main()
