#!/usr/bin/env python3
"""Measure ordering-only callbacks without any native/GPU execution."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import json
import os
from pathlib import Path
from statistics import median
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime


def fixture(runtime_class, *, mode, size, finished):
    if mode in ("full_control", "eos_control"):
        environment = {
            "BELIEFKV_INTERNAL_COMPARISON_MODE": "full",
            "BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH": "0",
            "BELIEFKV_ENABLE_TOOL_PREFETCH": "0",
            "BELIEFKV_ONLINE_TOOL_TIMING": "0",
            "BELIEFKV_TOOL_TIMING_ARTIFACT": "",
            "BELIEFKV_TOOL_TIMING_SHA256": "",
            "BELIEFKV_JOIN_PREFETCH_TRIGGER": "observed_eos",
        }
    else:
        environment = arm_environment({}, mode)
        environment["BELIEFKV_ONLINE_TOOL_TIMING"] = "0"
        environment["BELIEFKV_BORROWABLE_PARENT_HEADROOM"] = "0"
    with patch.dict(os.environ, environment):
        runtime = runtime_class()
    if mode == "full_control":
        runtime._tool_timing_only = True
    requests = []
    for index in range(size):
        rid, context = f"r{index}", f"c{index}"
        request = NS(
            rid=rid, session_id=f"s{index}", session_generation=1,
            cache_request_handle=NS(attempt_id=0),
            beliefkv_metadata={
                "root_workflow_id": "wf", "invocation_id": rid,
                "context_id": context, "context_epoch": 0,
            },
            origin_input_ids=(1, 2), output_ids=[3],
            finished=lambda index=index: finished and index == 0,
        )
        runtime.register_visible_request(request)
        requests.append(request)
    initial = dict(runtime.visible), dict(runtime._visible_since), runtime.semantic_revision

    def reset():
        runtime.visible = dict(initial[0])
        runtime._visible_since = dict(initial[1])
        runtime.semantic_revision = initial[2]
        runtime._context_tokens.clear()
        runtime.counts.clear()

    def signature():
        return {
            "visible": {rid: asdict(key) for rid, key in runtime.visible.items()},
            "sessions": {cid: asdict(key) for cid, key in runtime.context_sessions.items()},
            "semantic_revision": runtime.semantic_revision,
            "pending": runtime.physical_ledger.pending_count,
            "completed_context": runtime._context_tokens.get("c0") if finished else None,
        }

    return runtime, NS(reqs=requests), reset, signature


def measure(classes, *, mode, size, finished, samples):
    fixtures = {
        name: fixture(cls, mode=mode, size=size, finished=finished)
        for name, cls in classes.items()
    }
    times = {name: [] for name in classes}
    cpu_times = {name: [] for name in classes}
    snapshots = {}
    calls = 1 if finished else 100
    try:
        for sample in range(samples + 20):
            order = tuple(classes) if sample % 2 else tuple(reversed(classes))
            observations = {}
            for name in order:
                runtime, batch, reset, signature = fixtures[name]
                reset()
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                for _ in range(calls):
                    completed = runtime.on_batch_completed(batch)
                elapsed = (time.perf_counter_ns() - wall) / (1000. * calls)
                cpu_elapsed = (time.thread_time_ns() - cpu) / (1000. * calls)
                observations[name] = (completed, signature())
                if sample >= 20:
                    times[name].append(elapsed)
                    cpu_times[name].append(cpu_elapsed)
                    snapshots[name] = {
                        "context_updates": len(runtime._context_tokens),
                        "completion_ids": completed,
                    }
            if observations["baseline"] != observations["candidate"]:
                raise AssertionError("completion, identity, session or pending state changed")
        return {
            "mode": mode, "batch_size": size, "one_finished": finished,
            "calls_per_sample": calls, "required_completion_state_equal": True,
            "unused_unfinished_context_updates_may_differ": mode != "full_control",
            "snapshots": snapshots,
            "costs": {
                name: {
                    "wall_p50_us": median(times[name]), "wall_samples_us": times[name],
                    "thread_cpu_p50_us": median(cpu_times[name]),
                } for name in classes
            },
        }
    finally:
        for runtime, *_ in fixtures.values():
            runtime.close()


def tool_events(classes):
    counts = {}
    for name, cls in classes.items():
        runtime, _, _, _ = fixture(cls, mode="join_priority_only", size=1, finished=False)
        try:
            runtime.on_events((
                RuntimeEvent("start", 0., Kind.WORKFLOW_START, "wf"),
                RuntimeEvent("create", 1., Kind.INVOCATION_CREATE, "wf",
                             invocation_id="r0", context_id="c0"),
                RuntimeEvent("tool", 2., Kind.TOOL_START, "wf", invocation_id="r0"),
                RuntimeEvent("return", 3., Kind.TOOL_END, "wf", invocation_id="r0"),
            ))
            counts[name] = {
                "predictor_unavailable": runtime.counts["tool_wait_predictor_unavailable"],
                "invocation_state": runtime.graph.invocations["r0"].state.value,
                "pending_physical_actions": runtime.physical_ledger.pending_count,
            }
        finally:
            runtime.close()
    if counts["candidate"]["predictor_unavailable"] != 0:
        raise AssertionError("unused tool timing was still invoked")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="5ddb43a")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a fresh output are required")
    baseline, digests = baseline_runtime(args.baseline)
    classes = {"baseline": baseline, "candidate": NativeAdmissionRuntime}
    result = {
        "schema_version": 1, "scope": "CPU callbacks only; no whole-run or GPU speedup claim.",
        "baseline": args.baseline, "baseline_source_sha256": digests,
        "candidate_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "beliefkv/experiments/internal_comparison.py",
                "scripts/benchmark_internal_controls_cpu.py",
            )
        },
        "samples": args.samples, "normal_gc": True, "retains_all_outliers": True,
        "tool_events": tool_events(classes),
        "cases": [
            measure(classes, mode=mode, size=size, finished=finished, samples=args.samples)
            for mode in ("join_priority_only", "scheduler_only", "full_control", "eos_control")
            for size in (1, 8, 12, 48) for finished in (False, True)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "cases": len(result["cases"]),
        "completion_state_controls_equal": True, "gpu_speedup_claim": False,
    }, indent=2))


if __name__ == "__main__":
    main()
