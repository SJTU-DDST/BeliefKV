#!/usr/bin/env python3
"""Measure optional JOIN progress prediction against the existing EOS-only path."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_transfer_service import NativeServiceSample
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_prefetch_control_cpu import distribution
from scripts.benchmark_wait_prefetch_dispatch_cpu import selection_fixture
from scripts.launch_qualified_after_hold import last_runtime_state

MODES = ("selective_report_or_eos", "observed_eos")


def setup(mode, *, depth, parent_tokens, remaining_ms, service_ms):
    runtime, reads, actions, reset, now = selection_fixture(
        NativeAdmissionRuntime, depth=depth, remaining_ms=remaining_ms,
        report_service=True,
    )
    runtime.join_prefetch_trigger = mode
    stage = next(iter(runtime._final_stages.values()))
    runtime._context_tokens[stage.key.context_id] = (
        stage.key.context_epoch, parent_tokens, 0, False,
    )
    entries = runtime._native_cache.cache_controller.mem_pool_host.entry_map
    entries["kv"].host_pool.size_per_token = 20480
    entries["mamba"].host_pool.size_per_token = 64389120
    runtime._residency_pool_bytes = 20480, 64389120
    runtime._native_service_samples.clear()
    runtime._native_service_samples.extend(
        NativeServiceSample(
            depth * 128 * 20480 + 64389120, service_ms, shape="hybrid",
        ) for _ in range(3)
    )
    runtime._h2d_samples.clear()
    runtime._h2d_full_service_samples.clear()
    runtime._h2d_service_estimates.clear()
    return runtime, reads, actions, reset, now


def summarize(values):
    return {mode: {
        **distribution(samples), "min_us": min(samples), "max_us": max(samples),
        "samples_us": samples,
    } for mode, samples in values.items()}


def selection_case(*, name, depth, parent_tokens, remaining_ms, service_ms, samples):
    variants = {
        mode: setup(
            mode, depth=depth, parent_tokens=parent_tokens,
            remaining_ms=remaining_ms, service_ms=service_ms,
        ) for mode in MODES
    }
    costs = {mode: [] for mode in MODES}
    reads_total = {mode: Counter() for mode in MODES}
    counts, selected = {}, {}
    try:
        for iteration in range(samples + 10):
            for mode in MODES[::1 if iteration % 2 else -1]:
                runtime, reads, actions, reset, now = variants[mode]
                reset()
                runtime._critical_parent_demand = None
                with patch("time.monotonic", lambda: now):
                    start = time.thread_time_ns()
                    result = runtime._roll_final_stage()
                    elapsed = (time.thread_time_ns() - start) / 1000.
                assert not actions and not runtime.physical_ledger.pending_count
                assert runtime.physical_disabled is False
                selected[mode] = result is not None
                counts[mode] = dict(runtime.counts)
                if iteration >= 10:
                    costs[mode].append(elapsed)
                    reads_total[mode].update(reads)
        assert selected["observed_eos"] is False
        assert selected["selective_report_or_eos"] is (name == "positive_deficit_control")
        return {
            "case": name, "depth": depth, "parent_tokens": parent_tokens,
            "remaining_ms": remaining_ms, "service_ms": service_ms,
            "thread_cpu": summarize(costs), "last_call_counters": counts,
            "native_reads_per_call": {
                mode: {key: value / samples for key, value in reads.items()}
                for mode, reads in reads_total.items()
            },
            "selected_before_eos": selected, "physical_actions": 0,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def batch_case(*, due, samples):
    variants = {
        mode: setup(
            mode, depth=32, parent_tokens=100001, remaining_ms=500.,
            service_ms=10.,
        ) for mode in MODES
    }
    costs = {mode: [] for mode in MODES}
    try:
        for iteration in range(samples + 10):
            for mode in MODES[::1 if iteration % 2 else -1]:
                runtime, _, actions, _, now = variants[mode]
                stage = next(iter(runtime._final_stages.values()))
                requests = [NS(
                    rid=stage.request_id if index == 0 else f"busy-{index}",
                    output_ids=list(range(128)), finished=lambda: False,
                ) for index in range(8)]
                batch = NS(reqs=requests)
                runtime._tool_timing_only = True
                runtime._native_running_batch = batch
                runtime._report_sample_next_ms = 0. if due else now * 1000. + 200.
                runtime._semantic_progress[stage.request_id] = deque((
                    (now * 1000. - 200., 80), (now * 1000. - 100., 100),
                ), maxlen=64)
                with patch("time.monotonic", lambda: now):
                    start = time.thread_time_ns()
                    completed = runtime.on_batch_completed(batch)
                    elapsed = (time.thread_time_ns() - start) / 1000.
                assert not completed and not actions and not runtime.physical_ledger.pending_count
                if mode == "observed_eos":
                    assert len(runtime._semantic_progress[stage.request_id]) == 2
                if iteration >= 10:
                    costs[mode].append(elapsed)
        return {
            "case": "batch_completed_progress_due" if due else "batch_completed_progress_throttled",
            "batch_size": 8, "thread_cpu": summarize(costs),
            "physical_actions": 0,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def trace_counts(path):
    state = last_runtime_state(path)
    if state is None:
        raise ValueError("a valid runtime state is required")
    counts = state["counts"]
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "path": str(path.resolve()), "sha256": digest, "bytes": path.stat().st_size,
        "runtime_state_ts_ms": state["ts_ms"], "final": state.get("final"),
        "join_prefetch_trigger": state["join_prefetch_trigger"],
        "counts": {
            name: counts.get(name, 0) for name in (
                "selective_join_candidates_checked", "selective_join_native_plan_checks",
                "report_service_planning_deferred", "selective_join_small_or_unknown_parent",
                "selective_join_small_missing_full", "selective_join_eos_window_sufficient",
                "selective_join_no_host_restore", "selective_join_h2d_candidate",
                "report_service_h2d_candidate", "report_length_online_calibrated",
                "observed_eos_h2d_candidate", "join_eos_arrival_observed",
            )
        },
        "shared_path_profiling": state["profile_shared_path"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-observations", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=101)
    args = parser.parse_args()
    if args.output.exists() or args.samples < 1:
        parser.error("use a fresh output and positive sample count")
    cases = [
        selection_case(
            name=name, depth=depth, parent_tokens=parent_tokens,
            remaining_ms=remaining_ms, service_ms=service_ms, samples=args.samples,
        ) for name, depth, parent_tokens, remaining_ms, service_ms in (
            ("small_parent", 32, 4097, 60., 10.),
            ("distant_report", 32, 100001, 5000., 10.),
            ("small_missing_full", 32, 100001, 60., 10.),
            ("eos_window_sufficient", 224, 100001, 60., 10.),
            ("positive_deficit_control", 224, 100001, 60., 400.),
        )
    ] + [batch_case(due=due, samples=args.samples) for due in (False, True)]
    sources = (
        Path(__file__), ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
        ROOT / "beliefkv/runtime/native_transfer_policy.py",
        ROOT / "beliefkv/runtime/native_transfer_service.py",
        ROOT / "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        ROOT / "scripts/benchmark_native_prepare_path.py",
        ROOT / "scripts/benchmark_prefetch_control_cpu.py",
        ROOT / "scripts/launch_qualified_after_hold.py",
    )
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gc_enabled": gc.isenabled(), "samples_per_case_and_mode": args.samples,
        "cases": cases, "runtime_evidence": trace_counts(args.runtime_observations),
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "semantics": [
            "Alternate mode order; retain all samples and normal GC. No GPU or DMA.",
            "Selection uses bounded native-tree substitutes; batch timing covers the actual application callback with unfinished request substitutes.",
            "The positive deficit control deliberately demonstrates useful prediction that EOS-only declines. Equal coverage is not claimed.",
            "Recorded online counters are not online CPU durations. Do not multiply these fixture timings into total control time, removable JCT or throughput.",
            "EOS correctness and retained tool/PREPARE/admission behavior are checked separately by regression tests.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([{
        "case": case["case"],
        "p50_thread_cpu_us": {
            mode: case["thread_cpu"][mode]["p50_us"] for mode in MODES
        },
    } for case in cases], indent=2))


if __name__ == "__main__":
    main()
