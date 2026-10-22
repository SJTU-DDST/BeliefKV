#!/usr/bin/env python3
"""Measure complete CPU planning/selection for Full and the simple JOIN control."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import gc
import json
import os
from pathlib import Path
from statistics import median
import sys
import subprocess
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_shared_path import synthetic_runtime


def fixture(mode, profile, size, *, runtime_class=NativeAdmissionRuntime):
    environment = arm_environment({"PREFILL_SCHEDULING_POLICY": profile}, mode)
    environment.update(
        BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH="0",
        BELIEFKV_ENABLE_TOOL_PREFETCH="0", BELIEFKV_ONLINE_TOOL_TIMING="0",
        BELIEFKV_BORROWABLE_PARENT_HEADROOM="0",
    )
    with patch.dict(os.environ, environment):
        return synthetic_runtime(runtime_class, CausalFrontierScheduler, size, 4)


def pinned_planner(revision):
    path = "beliefkv/runtime/sglang_v0520_runtime.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    original = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    original.name = "PinnedPlanningRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [
        node for node in original.body
        if isinstance(node, ast.FunctionDef) and node.name == "plan_native_prefill"
    ]
    if len(original.body) != 1:
        raise ValueError("baseline has no prefill planner")
    namespace = dict(vars(runtime_module))
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[original], type_ignores=[],
    )), f"{revision}:{path}", "exec"), namespace)
    return namespace[original.name]


def measure_aging(baseline, *, size, pattern, samples):
    fixtures = {
        name: fixture("full", "join_priority", size, runtime_class=runtime_class)
        for name, runtime_class in (
            ("baseline", baseline), ("candidate", NativeAdmissionRuntime),
        )
    }
    costs = {name: {"wall_us": [], "cpu_us": []} for name in fixtures}
    calls = 10
    now = time.monotonic()
    running = NS(reqs=[])
    adder = NS(max_running_requests=8, rem_input_tokens=8192, page_size=16)
    try:
        for runtime, queue in fixtures.values():
            runtime._visible_since = {
                request.rid: now - (
                    12. + (index * 7 % size) if (
                        pattern == "aged" or pattern == "mixed" and index % 2 == 0
                    ) else index % 5
                )
                for index, request in enumerate(queue)
                if pattern != "mixed" or index % 5
            }
        with patch("time.monotonic", lambda: now):
            for sample in range(samples + 20):
                observations = {}
                order = tuple(fixtures)
                for name in order if sample % 2 else reversed(order):
                    runtime, queue = fixtures[name]
                    runtime.counts.clear()
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    for _ in range(calls):
                        runtime.scheduler_step(queue, defer_observations=True)
                        inspected = runtime.inspect_native_prefill_queue(queue)
                        plan = runtime.plan_native_prefill(
                            queue, running_batch=running, adder=adder,
                            _queue_inspection=inspected,
                        )
                        selection = select_native_prefill_candidates(
                            queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
                        )
                    elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                    elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                    if sample >= 20:
                        costs[name]["wall_us"].append(elapsed_wall)
                        costs[name]["cpu_us"].append(elapsed_cpu)
                    observations[name] = (
                        plan, [req.rid for req in selection.candidates], selection.rejected,
                        runtime.graph.snapshot(), dict(runtime.counts),
                        dict(runtime._join_priority_pending), runtime.physical_ledger.pending_count,
                    )
                if observations["baseline"] != observations["candidate"]:
                    raise AssertionError("aging order, complete plan or runtime state changed")
                if len(selection.candidates) != size or selection.rejected or runtime.physical_ledger.pending_count:
                    raise AssertionError("aging comparison must serve the complete CPU queue without DMA")
        return {
            "queue_size": size, "age_pattern": pattern, "calls_per_sample": calls,
            "complete_plan_selection_graph_counters_pending_equal": True,
            "costs": {
                name: {
                    clock: {"p50_us": median(values), "samples_us": values}
                    for clock, values in clocks.items()
                } for name, clocks in costs.items()
            },
        }
    finally:
        for runtime, _ in fixtures.values():
            runtime.close()


def measure(size, samples):
    fixtures = {
        name: fixture(mode, profile, size) for name, mode, profile in (
            ("full_workflow", "full", "workflow"),
            ("full_join_priority", "full", "join_priority"),
            ("join_only", "join_priority_only", "join_priority"),
            ("full_workflow_completion", "full", "workflow_completion"),
        )
    }
    values = {name: {"wall_us": [], "cpu_us": []} for name in fixtures}
    calls = 10
    try:
        for sample in range(samples + 20):
            observations = {}
            order = tuple(fixtures)
            for name in order if sample % 2 else reversed(order):
                runtime, queue = fixtures[name]
                now = time.monotonic()
                runtime._visible_since = {rid: now for rid in runtime.visible}
                running = NS(reqs=[])
                adder = NS(max_running_requests=8, rem_input_tokens=8192, page_size=16)
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                for _ in range(calls):
                    runtime.scheduler_step(queue, defer_observations=True)
                    inspection = runtime.inspect_native_prefill_queue(queue)
                    plan = runtime.plan_native_prefill(
                        queue, running_batch=running, adder=adder,
                        _queue_inspection=inspection,
                    )
                    selection = select_native_prefill_candidates(
                        queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
                    )
                elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                observations[name] = (
                    [request.rid for request in selection.candidates], selection.rejected,
                    runtime.graph.snapshot(), dict(runtime._join_priority_pending),
                    runtime.physical_ledger.pending_count,
                )
                if sample >= 20:
                    values[name]["wall_us"].append(elapsed_wall)
                    values[name]["cpu_us"].append(elapsed_cpu)
            if observations["full_join_priority"] != observations["join_only"]:
                raise AssertionError("simple Full/control selection or causal state differs")
            if any(observation[-1] for observation in observations.values()):
                raise AssertionError("CPU planning issued a physical action")
        return {
            "queue_size": size, "calls_per_sample": calls,
            "simple_full_and_join_only_selection_graph_pending_equal": True,
            "no_native_cache_or_dma": True,
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


def measure_last_child(baseline, *, size, scenario, samples):
    fixtures = {
        name: fixture("full", "join_priority", size, runtime_class=runtime_class)
        for name, runtime_class in (
            ("baseline", baseline), ("disabled", NativeAdmissionRuntime),
            ("enabled", NativeAdmissionRuntime),
        )
    }
    costs = {name: {"wall_us": [], "cpu_us": []} for name in fixtures}
    calls = 10
    running = NS(reqs=[])
    adder = NS(max_running_requests=8, rem_input_tokens=8192, page_size=16)
    try:
        for name, (runtime, queue) in fixtures.items():
            runtime.enable_join_last_child_priority = name == "enabled"
            runtime._join_priority_normal_admissions = 3 if scenario == "quota" else 4
            runtime._visible_since = {req.rid: 9. for req in queue}
            for req in queue:
                key = runtime.visible[req.rid]
                child = runtime.graph.invocations[key.invocation_id]
                parent = runtime.graph.invocations[child.parent_invocation_id]
                join = next(
                    join for join in runtime.graph.joins.values()
                    if not join.satisfied and child.invocation_id in join.member_invocation_ids
                )
                parent.join_id = join.join_id
                runtime._join_by_invocation[child.invocation_id] = {join.join_id}
                if scenario == "none" or scenario == "tail" and req is not queue[-1]:
                    join.waiter_invocation_ids.clear()
            initial_graph = runtime.graph.snapshot()
        with patch("time.monotonic", lambda: 20.):
            for sample in range(samples + 20):
                selections = {}
                order = tuple(fixtures)
                for name in order if sample % 2 else reversed(order):
                    runtime, queue = fixtures[name]
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    for _ in range(calls):
                        runtime.scheduler_step(queue, defer_observations=True)
                        inspection = runtime.inspect_native_prefill_queue(queue)
                        plan = runtime.plan_native_prefill(
                            queue, running_batch=running, adder=adder,
                            _queue_inspection=inspection,
                        )
                        selected = select_native_prefill_candidates(
                            queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
                        )
                    elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                    elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                    if sample >= 20:
                        costs[name]["wall_us"].append(elapsed_wall)
                        costs[name]["cpu_us"].append(elapsed_cpu)
                    selections[name] = [req.rid for req in selected.candidates]
                    expected = (
                        [queue[-1].rid, *(req.rid for req in queue[:-1])]
                        if name == "enabled" and scenario == "tail"
                        else [req.rid for req in queue]
                    )
                    if (
                        selections[name] != expected or selected.rejected
                        or runtime.graph.snapshot() != initial_graph
                        or runtime.physical_ledger.pending_count
                    ):
                        raise AssertionError("sole-child order, causal graph or physical state changed")
                if selections["baseline"] != selections["disabled"]:
                    raise AssertionError("disabled sole-child control changed the old queue order")
        return {
            "queue_size": size, "scenario": scenario, "calls_per_sample": calls,
            "scope": "Complete CPU maintenance, inspection, planning and selection; no native cache, DMA or JCT prediction.",
            "disabled_selection_matches_pinned_baseline": True,
            "enabled_expected_selection_verified": True,
            "costs": {
                name: {
                    clock: {"p50_us": median(rows), "samples_us": rows}
                    for clock, rows in clocks.items()
                } for name, clocks in costs.items()
            },
        }
    finally:
        for runtime, _ in fixtures.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--baseline", help="Compare this revision's actual planner on identical aging queues")
    parser.add_argument("--last-child", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists() or not gc.isenabled():
        parser.error("positive samples, normal GC and a new output are required")
    if args.last_child and not args.baseline:
        parser.error("--last-child requires a pinned baseline planner")
    sources = {
        path: sha256_file(ROOT / path) for path in (
            "beliefkv/runtime/sglang_v0520_runtime.py",
            "beliefkv/runtime/sglang_v0520_admission.py",
            "beliefkv/experiments/internal_comparison.py",
            "beliefkv/policy/workflow_completion.py",
            "beliefkv/policy/workflow_frontier.py",
            "scripts/benchmark_join_priority_kv_cpu.py",
        )
    }
    baseline = pinned_planner(args.baseline) if args.baseline else None
    result = {
        "scope": "synthetic CPU planning/selection only; no GPU, DMA, JCT or quality claim",
        "normal_gc": True, "retains_outliers": True, "samples": args.samples,
        "source_sha256": sources,
        "baseline_revision": args.baseline,
        "baseline_scope": (
            "Pinned baseline plan_native_prefill body and decorator; other helpers and "
            "identity classes are shared. Fixed same-clock aging, normal GC, all samples retained."
        ) if baseline else None,
        "cases": [
            measure_last_child(baseline, size=size, scenario=scenario, samples=args.samples)
            for size in (8, 48, 128) for scenario in ("none", "quota", "tail", "all")
        ] if args.last_child else [
            measure_aging(baseline, size=size, pattern=pattern, samples=args.samples)
            for size in (8, 48, 128) for pattern in ("recent", "aged", "mixed")
        ] if baseline else [measure(size, args.samples) for size in (8, 48, 128)],
    }
    if any(sha256_file(ROOT / path) != digest for path, digest in sources.items()):
        raise RuntimeError("CPU benchmark sources changed during measurement")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cases": len(result["cases"])}, indent=2))


if __name__ == "__main__":
    main()
