#!/usr/bin/env python3
"""Compare scheduler-side semantic snapshot handling with identical CPU inputs."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.hotpath_timing import timed_runtime
from beliefkv.runtime.semantic_report_worker import SEMANTIC_TEXT, SemanticReportReply
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import (
    baseline_classes, distribution, runtime_source_path, synthetic_runtime,
)


def fixture(runtime_class, frontier_class, requests, start_ms):
    runtime, queue = synthetic_runtime(runtime_class, frontier_class, requests, 16)
    submitted, replies = [], []
    readiness = {"value": True}

    def submit(item):
        submitted.append(item)
        replies.append(SemanticReportReply(item, .25, 10., 20., 40., 0.))

    def poll():
        result = tuple(replies)
        replies.clear()
        return result

    runtime._semantic_worker = NS(
        submit=submit, poll=poll, ready=True, disabled=False, dropped=0,
        error="", close=lambda: None,
    )
    runtime._semantic_transfer_target_ready = lambda child, now: readiness["value"]
    keys = tuple(runtime.visible[req.rid] for req in queue)
    for key in keys:
        runtime._semantic_progress[key.request_id] = deque(
            ((start_ms - 250, 20), (start_ms - 120, 40)), maxlen=128,
        )
    return runtime, keys, submitted, readiness


def deliver(runtime, keys, now_ms, sequence):
    domain = local_monotonic_clock_domain()
    for key in keys:
        runtime._semantic_progress[key.request_id].append(
            (now_ms - 120, 40 + sequence * 16),
        )
        runtime._capture_semantic_text(RuntimeEvent(
            f"{key.request_id}-{sequence}", now_ms,
            RuntimeEventKind.STRUCTURED_ACTION, key.root_workflow_id,
            invocation_id=key.invocation_id, context_id=key.context_id,
            context_epoch=key.context_epoch,
            attributes={
                SEMANTIC_TEXT: True, "request_id": key.request_id,
                "content_chars": 128 + sequence * 64,
                "content_tail": f"Report section {sequence} complete.",
                "monotonic_clock_domain": domain,
            },
        ))


def measure_case(baseline, frontier, *, name, requests, iterations):
    start_ms = time.monotonic() * 1000
    variants = {
        "baseline": fixture(baseline, frontier, requests, start_ms),
        "optimized": fixture(
            NativeAdmissionRuntime, CausalFrontierScheduler, requests, start_ms,
        ),
    }
    samples = {variant: [] for variant in variants}
    pending_counts = {variant: [] for variant in variants}
    try:
        if name not in ("idle", "fresh", "target_retry"):
            for runtime, keys, _, _ in variants.values():
                deliver(runtime, keys, start_ms, 0)
                runtime._poll_semantic_reports(start_ms + 1)
        for iteration in range(iterations + 5):
            order = list(variants)
            if iteration % 2:
                order.reverse()
            now_ms = (
                start_ms + iteration * 400 if name == "fresh"
                else start_ms + iteration * 10 if name == "target_retry"
                else start_ms + 400
            )
            for variant in order:
                runtime, keys, _, readiness = variants[variant]
                readiness["value"] = name != "target_retry" or iteration % 40 >= 10
                started = time.perf_counter_ns()
                if name == "fresh" or name == "target_retry" and iteration % 40 == 0:
                    deliver(runtime, keys, now_ms, iteration + 1)
                runtime._poll_semantic_reports(now_ms + 1)
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                if iteration >= 5:
                    samples[variant].append(elapsed_ms)
                    pending_counts[variant].append(len(runtime._semantic_frames))
            before, after = variants["baseline"], variants["optimized"]
            if before[2] != after[2]:
                raise AssertionError(f"{name}: semantic model inputs changed at {iteration}")
            if before[0]._semantic_forecasts != after[0]._semantic_forecasts:
                raise AssertionError(f"{name}: accepted forecasts changed at {iteration}")
        costs = {variant: distribution(values) for variant, values in samples.items()}
        inputs = [asdict(item) for item in variants["optimized"][2]]
        return {
            "scenario": name, "visible_requests": requests, "iterations": iterations,
            "model_inputs_equal": True, "accepted_forecasts_equal": True,
            "submitted_inputs": len(inputs),
            "model_input_sha256": hashlib.sha256(json.dumps(
                inputs, sort_keys=True, allow_nan=False,
            ).encode()).hexdigest(),
            "pending_frame_max": {
                variant: max(values) for variant, values in pending_counts.items()
            },
            "costs": costs,
            "mean_reduction_fraction": (
                1 - costs["optimized"]["mean_ms"] / costs["baseline"]["mean_ms"]
            ),
        }
    finally:
        for runtime, _, _, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 40:
        raise ValueError("at least forty iterations required for target retries")
    runtime_path = runtime_source_path()
    baseline, frontier, digests = baseline_classes(args.baseline_revision)
    baseline._poll_semantic_reports = timed_runtime("semantic_updates")(
        baseline._poll_semantic_reports,
    )
    cases = [
        measure_case(
            baseline, frontier, name=name, requests=requests, iterations=args.iterations,
        )
        for name, requests in (
            ("idle", 48), ("submitted", 12), ("submitted", 48), ("submitted", 96),
            ("fresh", 48), ("target_retry", 48),
        )
    ]
    report = {
        "scope": (
            "same-input synthetic CPU semantic capture/poll; retained workflow graph "
            "and actual runtime validation; stub worker and target availability; "
            "excludes neural inference, process IPC, DMA, GPU and throughput"
        ),
        "baseline_revision": args.baseline_revision, "baseline_source_sha256": digests,
        "optimized_runtime_source": str(runtime_path),
        "optimized_runtime_source_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "equal_disabled_timing_wrappers": True,
        "worker_final_score": .25, "retained_rounds_per_workflow": 16, "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"scope": report["scope"], "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
