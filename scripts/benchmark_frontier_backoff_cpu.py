#!/usr/bin/env python3
"""Paired CPU cost of queued probes in backoff and a newly eligible frontier."""

from __future__ import annotations

import argparse
import ast
import gc
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_physical import ContextSessionAnchors, has_host_backed_missing_copy
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from scripts.benchmark_join_priority_kv_cpu import fixture
from scripts.benchmark_native_reentry_cpu import fixture as radix_fixture, variant as native_variant


def pinned_dispatcher(revision):
    filename = "beliefkv/runtime/sglang_v0520_runtime.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{filename}"], cwd=ROOT, text=True,
    )
    definition = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    definition.name = "PinnedFrontierRuntime"
    definition.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    definition.body = [
        node for node in definition.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_frontier_restore"
    ]
    if len(definition.body) != 1:
        raise ValueError("baseline frontier dispatcher is absent")
    namespace = dict(vars(runtime_module))
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[definition], type_ignores=[],
    )), f"{revision}:{filename}", "exec"), namespace)
    return namespace[definition.name], hashlib.sha256(source.encode()).hexdigest()


def measure(baseline, size, scenario, samples):
    fixtures, counters, probes, initial_graph = {}, {}, {}, {}
    for name, runtime_class in (
        ("baseline", baseline), ("candidate", NativeAdmissionRuntime),
    ):
        runtime, queue = fixture("full", "join_priority", size, runtime_class=runtime_class)
        runtime.enable_frontier_restore = True
        runtime.enable_execution_handoff = runtime.enable_tool_prefetch = False
        count = counters[name] = {"plans": 0, "anchor_snapshots": 0}
        original = runtime.plan_native_prefill

        def plan(*args, _original=original, _count=count, **kwargs):
            _count["plans"] += 1
            return _original(*args, **kwargs)

        def snapshot(*_args, _count=count, **_kwargs):
            _count["anchor_snapshots"] += 1
            return None

        runtime.plan_native_prefill = plan
        runtime.attach_native_cache(NS(
            prefetch_gpu_session_nodes=lambda **_kwargs: None,
            session_refs=NS(snapshot_latest_session_leaf_anchors=snapshot),
        ))
        runtime._visible_since = {request.rid: 20. for request in queue}
        if scenario == "no_slot":
            runtime._current_residency_budget = lambda: PrefetchResidencyBudget(0, 0)
        probes[name] = {key: 20200. for key in runtime.visible.values()}
        if scenario == "first_due":
            probes[name].pop(runtime.visible[queue[0].rid])
        fixtures[name] = runtime, queue
        initial_graph[name] = runtime.graph.snapshot()
    values = {name: {"wall_us": [], "cpu_us": []} for name in fixtures}
    calls = 10
    running = NS(reqs=[])
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=20.051):
            for sample in range(samples + 20):
                names = tuple(fixtures)
                signatures = {}
                for name in names if sample % 2 else reversed(names):
                    runtime, queue = fixtures[name]
                    count = counters[name]
                    count["plans"] = count["anchor_snapshots"] = 0
                    wall_total = cpu_total = 0
                    for _ in range(calls):
                        runtime._frontier_probe_after_ms = dict(probes[name])
                        runtime._execution_handoff_next_ms = 0.
                        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                        runtime.dispatch_frontier_restore(queue, running_batch=running)
                        cpu_total += time.thread_time_ns() - cpu
                        wall_total += time.perf_counter_ns() - wall
                    if sample >= 20:
                        values[name]["wall_us"].append(wall_total / calls / 1000.)
                        values[name]["cpu_us"].append(cpu_total / calls / 1000.)
                    expected_plans = (
                        0 if scenario == "no_slot" or scenario == "all_backoff" and name == "candidate"
                        else calls
                    )
                    expected_reads = calls if scenario == "first_due" else 0
                    if count != {"plans": expected_plans, "anchor_snapshots": expected_reads}:
                        raise AssertionError(("probe behavior changed", name, scenario, count))
                    signatures[name] = (
                        runtime.graph.snapshot(), dict(runtime.visible),
                        dict(runtime.context_sessions),
                        runtime.physical_ledger.pending_count,
                        runtime._execution_handoff, runtime.physical_disabled,
                    )
                    if (
                        signatures[name][0] != initial_graph[name]
                        or runtime.physical_ledger.pending_count
                        or runtime._execution_handoff is not None or runtime.physical_disabled
                    ):
                        raise AssertionError("CPU control changed graph or issued physical work")
                if signatures["baseline"] != signatures["candidate"]:
                    raise AssertionError("native-visible logical state differs")
        return {
            "queue_size": size, "scenario": scenario, "calls_per_sample": calls,
            "graph_visible_sessions_pending_ticket_equal": True,
            "counters_per_sample": counters,
            "costs": {
                name: {
                    clock: {"p50_us": median(rows), "samples_us": rows}
                    for clock, rows in clocks.items()
                } for name, clocks in values.items()
            },
        }
    finally:
        for runtime, _ in fixtures.values():
            runtime.close()


def measure_metadata(implementation, depth, samples):
    case = {"tokens": 131072, "depth": depth, "leaves": 1, "page_size": 1}
    cache, request = radix_fixture(implementation, case)
    node = cache.tree_core.node_by_id(depth)
    while node.parent is not None:
        for component in node.component_data.values():
            component.value = component.host_value
        node = node.parent
    node.component_data = {
        component: NS(value=None, host_value=None) for component in (0, 2)
    }
    anchors = ContextSessionAnchors(
        None, cache.session_refs.snapshot_latest_session_leaf_anchors(None, None), 0.,
    )
    timings = {
        name: {"wall_us": [], "cpu_us": []} for name in ("metadata", "native_prefix")
    }
    calls = 10
    for sample in range(samples + 20):
        names = tuple(timings)
        for name in names if sample % 2 else reversed(names):
            start, thread = time.perf_counter_ns(), time.thread_time_ns()
            for _ in range(calls):
                result = (
                    has_host_backed_missing_copy(cache, anchors) if name == "metadata"
                    else implementation.inspect(cache, request)
                )
            cpu = (time.thread_time_ns() - thread) / calls / 1000.
            wall = (time.perf_counter_ns() - start) / calls / 1000.
            if name == "metadata":
                if result is not False:
                    raise AssertionError("fully resident metadata was not proven empty")
            elif result is None or result["missing_full_tokens"] or result["missing_mamba_slots"]:
                raise AssertionError("native inspection disagrees on residency")
            if sample >= 20:
                timings[name]["cpu_us"].append(cpu)
                timings[name]["wall_us"].append(wall)
    return {
        **case, "calls_per_sample": calls,
        "scope": "Per-verification CPU cost; actual native RadixKey and reentry method, scalar index metadata, no native allocation or GPU. Not net periodic-control savings.",
        "costs": {
            name: {
                clock: {"p50_us": median(rows), "samples_us": rows}
                for clock, rows in clocks.items()
            } for name, clocks in timings.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a fresh output are required")
    baseline, digest = pinned_dispatcher(args.baseline)
    implementation = native_variant(args.engine)
    sources = {
        name: sha256_file(ROOT / name) for name in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_admission.py",
            "beliefkv/runtime/sglang_v0520_physical.py",
            "beliefkv/experiments/internal_comparison.py",
            "scripts/benchmark_frontier_backoff_cpu.py",
            "scripts/benchmark_join_priority_kv_cpu.py",
            "scripts/benchmark_native_shared_path.py",
            "scripts/benchmark_native_reentry_cpu.py",
        )
    }
    report = {
        "scope": "Paired CPU frontier control only; bounded scalar native stand-ins, no allocator, DMA, GPU or JCT claim.",
        "baseline_revision": args.baseline, "baseline_runtime_sha256": digest,
        "baseline_scope": "Pinned dispatch_frontier_restore body and decorator; all other helpers and identity classes shared.",
        "semantics": [
            "A stable queue is already within the existing 200ms per-key probe backoff.",
            "first_due exposes one uncached request at the actual admission frontier; no long-input prefix matching occurs without published anchors.",
            "The same clock, queue identities and backoff evidence are supplied on every iteration; reset and graph verification are outside timing.",
            "Future probe deadlines deliberately differ after a negative read: the candidate retries temporary negatives rather than keeping infinity.",
            "All logical graph, visible/session state, pending commands and waiting tickets are compared; diagnostic counters may differ.",
        ],
        "normal_gc": True, "retains_outliers": True, "samples": args.samples,
        "source_sha256": sources,
        "native_engine": str(args.engine.resolve()),
        "native_source_sha256": implementation.source_sha256,
        "cases": [
            measure(baseline, size, scenario, args.samples)
            for size in (8, 48, 128) for scenario in ("no_slot", "all_backoff", "first_due")
        ],
        "resident_metadata_cases": [
            measure_metadata(implementation, depth, args.samples) for depth in (16, 64, 1024)
        ],
        "gpu_performance_accepted": False,
    }
    if any(sha256_file(ROOT / name) != expected for name, expected in sources.items()):
        raise RuntimeError("CPU benchmark source changed during measurement")
    if any(
        sha256_file(args.engine / name) != expected
        for name, expected in implementation.source_sha256.items()
    ):
        raise RuntimeError("native source changed during metadata measurement")
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
