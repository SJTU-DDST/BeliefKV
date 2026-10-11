#!/usr/bin/env python3
"""Compare the pinned old and current native-admission CPU paths, without a GPU."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from statistics import mean
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import (
    ContextRecord, InvocationRecord, InvocationState, JoinRecord,
    WorkflowRecord,
)
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
import beliefkv.policy.causal_frontier as frontier_module
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module


def runtime_source_path() -> Path:
    path = Path(runtime_module.__file__).resolve()
    if path != ROOT / "beliefkv/runtime/sglang_v0520_runtime.py":
        raise RuntimeError(f"benchmark imported a different worktree: {path}")
    return path


def baseline_classes(revision: str):
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/policy/causal_frontier.py",
        "beliefkv/runtime/sglang_v0520_admission.py",
    )
    sources = [
        subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True)
        for path in paths
    ]
    tree = ast.parse(sources[0])
    original = next(item for item in tree.body
                    if isinstance(item, ast.ClassDef) and item.name == "NativeAdmissionRuntime")
    methods = {
        "_causal_rank", "plan_native_prefill", "_local_frontier_features",
        "_prefill_causal_ranks", "on_batch_completed", "dispatch_execution_handoff",
        "_resident_prefill_scan",
        "_capture_semantic_text", "_poll_semantic_reports",
        "_executable_waiting_key", "terminal_waiting_request_ids",
        "scheduler_step",
    }
    original.name = "BaselineRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [item for item in original.body
                     if isinstance(item, ast.FunctionDef) and item.name in methods]
    for method in original.body:
        method.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[]))
    namespace = dict(vars(runtime_module))
    request_key = next(
        item for item in ast.parse(sources[2]).body
        if isinstance(item, ast.FunctionDef) and item.name == "_request_key"
    )
    identity_module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ),
        request_key,
    ], type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(identity_module), f"{revision}:{paths[2]}", "exec"),
        namespace,
    )
    exec(compile(module, f"{revision}:{paths[0]}", "exec"), namespace)
    frontier_namespace = dict(vars(frontier_module))
    exec(compile(sources[1], f"{revision}:{paths[1]}", "exec"), frontier_namespace)
    return (
        namespace["BaselineRuntime"], frontier_namespace["CausalFrontierScheduler"],
        dict(zip(paths, (hashlib.sha256(source.encode()).hexdigest() for source in sources))),
    )


def synthetic_runtime(runtime_class, frontier_class, workflows: int, rounds: int,
                      *, profiling: bool = False):
    runtime = runtime_class()
    graph = runtime.graph
    requests = []
    for workflow in range(workflows):
        wid = f"wf-{workflow}"
        graph.workflows[wid] = WorkflowRecord(wid, 0.)
        root_id = f"{wid}-root"
        for index in range(1 + rounds * 3):
            iid = root_id if index == 0 else f"{wid}-child-{index}"
            cid = f"ctx-{iid}"
            live = index in (0, rounds * 3)
            state = (
                InvocationState.WAIT_JOIN if index == 0
                else InvocationState.READY if live else InvocationState.DONE
            )
            graph.invocations[iid] = InvocationRecord(
                wid, iid, cid, "agent", iid, state, 0., 0.,
                parent_invocation_id=None if index == 0 else root_id,
            )
            graph.contexts[cid] = ContextRecord(wid, cid, 0, 0., 0., invocation_ids={iid})
            graph.workflows[wid].invocation_ids.add(iid)
            if index:
                graph.invocations[root_id].child_invocation_ids.add(iid)
            if live and index:
                request = NS(
                    rid=f"req-{iid}", session_id=f"session-{cid}", session_generation=0,
                    cache_request_handle=NS(attempt_id=0),
                    beliefkv_metadata={
                        "root_workflow_id": wid, "invocation_id": iid,
                        "context_id": cid, "context_epoch": 0,
                        "parent_invocation_id": root_id,
                    },
                    origin_input_ids=(), output_ids=(), finished=lambda: False,
                )
                runtime.register_visible_request(request)
                requests.append(request)
        for round_index in range(rounds):
            ids = {f"{wid}-child-{3 * round_index + child}" for child in (1, 2, 3)}
            completed = ids - {f"{wid}-child-{rounds * 3}"}
            jid = f"{wid}-join-{round_index}"
            graph.joins[jid] = JoinRecord(
                wid, jid, ids, waiter_invocation_ids={root_id},
                completed_member_ids=completed, satisfied=len(completed) == 3,
            )
    graph._graph_version = 1
    runtime.frontier = frontier_class(graph)
    runtime._hotpath_timing = HotpathTiming(enabled=profiling)
    return runtime, list(reversed(requests))


def distribution(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "count": len(samples), "mean_ms": mean(samples),
        "p50_ms": ordered[len(ordered) // 2],
        "p99_ms": ordered[min(len(ordered) - 1, int(.99 * len(ordered)))],
        "max_ms": max(samples),
    }


def benchmark(*, revision: str, workflows: int, rounds: int, iterations: int,
              planning_calls: int = 1, resident_first: bool = False,
              parent_handoffs: int = 0, semantic_progress: bool = False) -> dict:
    baseline, frontier, digests = baseline_classes(revision)
    variants = {
        "baseline": synthetic_runtime(baseline, frontier, workflows, rounds),
        "optimized": synthetic_runtime(NativeAdmissionRuntime, CausalFrontierScheduler,
                                       workflows, rounds),
        "optimized_profiled": synthetic_runtime(NativeAdmissionRuntime, CausalFrontierScheduler,
                                                workflows, rounds, profiling=True),
    }
    samples = {name: {"admission": [], "batch_completed": []} for name in variants}
    inspection_counts = {name: Counter() for name in variants}
    for runtime, queue in variants.values():
        for request in queue[:parent_handoffs]:
            child = runtime.visible[request.rid]
            parent_id = runtime.graph.invocations[child.invocation_id].parent_invocation_id
            parent = runtime.graph.invocations[parent_id]
            parent_key = replace(
                child, request_id=f"req-{parent_id}", invocation_id=parent_id,
                context_id=parent.context_id, session_id=f"session-{parent.context_id}",
            )
            runtime._parent_capacity_handoffs[parent.context_id] = (
                runtime_module._ParentCapacityHandoff(
                    parent_key, f"{parent_id.removesuffix('-root')}-join-{rounds - 1}", 0.,
                )
            )
        if semantic_progress:
            runtime._semantic_worker = NS(close=lambda: None)
            for request in queue[:48]:
                request.output_ids = []
    if resident_first:
        for name, (runtime, queue) in variants.items():
            positions = {request.rid: index for index, request in enumerate(queue)}

            def inspect(request, *, positions=positions, counts=inspection_counts[name]):
                counts[request.rid] += 1
                level = positions[request.rid] % 3
                return {
                    "missing_full_tokens": 0 if level == 0 else 256,
                    "missing_mamba_slots": int(level == 2),
                    "device_checkpoint_tokens": 0 if level == 2 else 1024,
                }

            runtime.enable_resident_first = True
            runtime._native_cache = NS(inspect_beliefkv_reentry=inspect)
    try:
        for iteration in range(iterations + 5):
            orders, batch_states = [], []
            # Rotate order, invalidate graph caches each iteration, retain all terminal history.
            names = list(variants)
            names = names[iteration % 3:] + names[:iteration % 3]
            for name in names:
                runtime, queue = variants[name]
                runtime.graph._graph_version += 1
                runtime._visible_since = {
                    request.rid: time.monotonic() for request in queue
                }
                runtime._prefill_cycle_active = planning_calls > 1
                runtime._prefill_causal_cache = None
                if resident_first:
                    runtime._reentry_observations.clear()
                if semantic_progress:
                    for request in queue[:48]:
                        request.output_ids.append(200_000)
                started = time.perf_counter_ns()
                cycle_orders = []
                for call in range(planning_calls):
                    plan = runtime.plan_native_prefill(
                        queue, running_batch=None, adder=None if call == 0 else NS(),
                    )
                    cycle_orders.append(plan.prioritized)
                if not resident_first and any(
                    order != cycle_orders[0] for order in cycle_orders[1:]
                ):
                    raise AssertionError("repeated planning changed the queue order")
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                orders.append(tuple(cycle_orders))
                if iteration >= 5:
                    samples[name]["admission"].append(elapsed)
                started = time.perf_counter_ns()
                runtime.on_batch_completed(NS(reqs=queue[:48]))
                if iteration >= 5:
                    samples[name]["batch_completed"].append(
                        (time.perf_counter_ns() - started) / 1_000_000
                    )
                batch_states.append((
                    dict(runtime._context_tokens),
                    dict(runtime._semantic_keys),
                    {
                        rid: tuple(tokens for _, tokens in history)
                        for rid, history in runtime._semantic_progress.items()
                    },
                    dict(runtime._decoded_scan_positions),
                    set(runtime._decoded_tool_requests),
                    set(runtime._parent_capacity_handoffs),
                    dict(runtime.counts),
                ))
            if any(order != orders[0] for order in orders[1:]):
                raise AssertionError(f"optimization changed the synthetic queue order at {iteration}")
            if any(state != batch_states[0] for state in batch_states[1:]):
                raise AssertionError(f"batch completion changed runtime state at {iteration}")
            if resident_first and any(
                counts != inspection_counts["baseline"]
                for counts in inspection_counts.values()
            ):
                raise AssertionError(f"changed native reentry observations at {iteration}")
        costs = {name: {phase: distribution(values) for phase, values in data.items()}
                 for name, data in samples.items()}
        return {
            "scope": "synthetic CPU benchmark; not live GPU overhead or an end-to-end speedup",
            "baseline_revision": revision, "baseline_source_sha256": digests,
            "optimized_runtime_source": str(runtime_source_path()),
            "optimized_runtime_source_sha256": hashlib.sha256(
                runtime_source_path().read_bytes(),
            ).hexdigest(),
            "workflows": workflows, "retained_rounds_per_workflow": rounds,
            "invocations": workflows * (1 + rounds * 3), "joins": workflows * rounds,
            "waiting_requests": workflows, "iterations": iterations,
            "planning_calls_per_cycle": planning_calls,
            "parent_handoff_contexts": parent_handoffs,
            "semantic_progress": semantic_progress,
            "batch_state_equal": True,
            "planning_scope": (
                "causal admission calls only; excludes physical handoff inspection and enqueue"
            ),
            "resident_first": resident_first,
            "residency_fixture": (
                "mixed resident/partial/Host-only observations from a stub native inspector"
                if resident_first else None
            ),
            "native_inspection_counts_equal": True if resident_first else None,
            "native_inspections_including_warmup": {
                name: sum(counts.values()) for name, counts in inspection_counts.items()
            } if resident_first else None,
            "invalidate_caches_each_iteration": True, "queue_order_equal": True,
            "costs": costs,
            "admission_mean_reduction_fraction": (
                1 - costs["optimized"]["admission"]["mean_ms"]
                / costs["baseline"]["admission"]["mean_ms"]
            ),
            "batch_completed_mean_reduction_fraction": (
                1 - costs["optimized"]["batch_completed"]["mean_ms"]
                / costs["baseline"]["batch_completed"]["mean_ms"]
            ),
            "profiled_timing": variants["optimized_profiled"][0]._hotpath_timing.snapshot(),
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def benchmark_handoff_frontier(
    *, revision: str, workflows: int, rounds: int, iterations: int,
) -> dict:
    baseline, frontier, digests = baseline_classes(revision)
    scenarios = {}
    headroom = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=800_000, device_mamba_free_slots=128,
    )
    for name, rows, empty_queue in (
        ("empty_queue", 8, True),
        ("zero_slots", 0, False),
        ("one_slot", 1, False),
        ("eight_slots", 8, False),
    ):
        variants = {
            "baseline": synthetic_runtime(baseline, frontier, workflows, rounds),
            "optimized": synthetic_runtime(
                NativeAdmissionRuntime, CausalFrontierScheduler, workflows, rounds,
            ),
        }
        samples = {variant: [] for variant in variants}
        inspections = {variant: 0 for variant in variants}
        running_batch = NS(reqs=[NS()] * 40)
        try:
            for variant, (runtime, _) in variants.items():
                def inspect(request, *, variant=variant):
                    inspections[variant] += 1
                    return {
                        "component_leaves": ((0, ((1, 2.),)), (2, ((1, 2.),))),
                        "reusable_input_tokens": 32, "checkpoint_tokens": 32,
                        "device_checkpoint_tokens": 0, "missing_full_tokens": 32,
                        "missing_mamba_slots": 1,
                    }

                runtime.enable_execution_handoff = True
                runtime.enable_resident_first = True
                runtime.attach_native_cache(NS(
                    inspect_beliefkv_reentry=inspect,
                    req_to_token_pool=NS(available_size=lambda: rows),
                    host_pool_group=NS(entry_map={
                        "kv": NS(host_pool=NS(size_per_token=20480)),
                        "mamba": NS(host_pool=NS(size_per_token=64389120)),
                    }),
                ))
                runtime._native_max_running = 48
                runtime._native_prefill_slots = 8
                runtime._native_page_size = 16
                runtime._native_input_reserve = 8192
            with patch.object(
                runtime_module, "observe_static_full_mamba_headroom", return_value=headroom,
            ), patch.object(
                runtime_module, "inspect_session_h2d_opportunity",
                return_value=NS(step=None, no_step_reason="benchmark_read_only"),
            ):
                for iteration in range(iterations + 5):
                    names = list(variants)
                    names = names[iteration % 2:] + names[:iteration % 2]
                    targets = []
                    for variant in names:
                        runtime, queue = variants[variant]
                        runtime.graph._graph_version += 1
                        runtime._visible_since = {
                            request.rid: time.monotonic() for request in queue
                        }
                        runtime._reentry_observations.clear()
                        runtime._execution_handoff_attempted.clear()
                        runtime._execution_handoff_next_ms = 0.
                        started = time.perf_counter_ns()
                        runtime.dispatch_execution_handoff(
                            [] if empty_queue else queue, running_batch=running_batch,
                        )
                        elapsed = (time.perf_counter_ns() - started) / 1_000_000
                        targets.append(tuple(sorted(
                            key.request_id for key in runtime._execution_handoff_attempted
                        )))
                        if iteration >= 5:
                            samples[variant].append(elapsed)
                        if runtime._execution_handoff is not None:
                            raise AssertionError("read-only fixture left a transfer ticket")
                    if targets[0] != targets[1]:
                        raise AssertionError(f"{name}: frontier selection changed")
            costs = {variant: distribution(values) for variant, values in samples.items()}
            scenarios[name] = {
                "available_request_rows": rows,
                "waiting_requests": 0 if empty_queue else workflows,
                "frontier_selection_equal": True, "costs": costs,
                "reentry_probe_calls_including_warmup": inspections,
                "mean_reduction_fraction": (
                    1 - costs["optimized"]["mean_ms"] / costs["baseline"]["mean_ms"]
                ),
            }
        finally:
            for runtime, _ in variants.values():
                runtime.close()
    return {
        "scope": (
            "synthetic CPU handoff frontier benchmark; real causal planning, residency "
            "budget and reentry cache; mocked read-only tree/physical opportunity; "
            "no DMA, enqueue, GPU service or end-to-end speedup measurement"
        ),
        "baseline_revision": revision, "baseline_source_sha256": digests,
        "optimized_runtime_source": str(runtime_source_path()),
        "optimized_runtime_source_sha256": hashlib.sha256(
            runtime_source_path().read_bytes(),
        ).hexdigest(),
        "workflows": workflows, "retained_rounds_per_workflow": rounds,
        "iterations": iterations, "running_requests": 40, "max_running_requests": 48,
        "invalidate_caches_each_iteration": True, "scenarios": scenarios,
    }


def benchmark_causal_reuse(
    *, revision: str, workflows: int, rounds: int, iterations: int,
    planning_calls: int, clock: list[float],
) -> dict:
    baseline, frontier, digests = baseline_classes(revision)
    for method, phase in (
        ("scheduler_step", "scheduler_maintenance"),
        ("plan_native_prefill", "admission_plan"),
    ):
        setattr(baseline, method, runtime_module.timed_runtime(phase)(getattr(baseline, method)))
    results = []
    for queue_size in sorted({48, workflows}):
        for retained_rounds in sorted({1, rounds}):
            for calls in sorted({1, planning_calls}):
                for graph_interval in (1, 4, 16):
                    variants = {
                        "baseline": synthetic_runtime(baseline, frontier, queue_size, retained_rounds),
                        "optimized": synthetic_runtime(
                            NativeAdmissionRuntime, CausalFrontierScheduler, queue_size, retained_rounds,
                        ),
                    }
                    samples = {name: {"wall_ms": [], "thread_cpu_ms": []} for name in variants}
                    inspections = {name: Counter() for name in variants}
                    try:
                        for name, (runtime, queue) in variants.items():
                            runtime.enable_resident_first = True
                            runtime._visible_since = {request.rid: clock[0] for request in queue}
                            positions = {request.rid: position for position, request in enumerate(queue)}

                            def inspect(request, *, name=name, positions=positions):
                                inspections[name][request.rid] += 1
                                resident = (positions[request.rid] + int(clock[0] * 20)) % 3 == 0
                                return {
                                    "missing_full_tokens": 0 if resident else 256,
                                    "missing_mamba_slots": int(not resident),
                                    "device_checkpoint_tokens": 1024 if resident else 0,
                                }

                            runtime.attach_native_cache(NS(inspect_beliefkv_reentry=inspect))
                        for iteration in range(iterations + 5):
                            clock[0] += .051
                            outputs = {}
                            order = list(variants)
                            if iteration % 2:
                                order.reverse()
                            for name in order:
                                runtime, queue = variants[name]
                                if iteration % graph_interval == 0:
                                    runtime.graph._graph_version += 1
                                started_wall, started_cpu = time.perf_counter_ns(), time.thread_time_ns()
                                runtime.scheduler_step(queue)
                                plans = tuple(runtime.plan_native_prefill(
                                    queue, running_batch=None, adder=None if call == 0 else NS(),
                                ) for call in range(calls))
                                costs = {
                                    "thread_cpu_ms": (time.thread_time_ns() - started_cpu) / 1_000_000,
                                    "wall_ms": (time.perf_counter_ns() - started_wall) / 1_000_000,
                                }
                                outputs[name] = (
                                    plans,
                                    {key: value for key, value in runtime.counts.items()
                                     if key != "admission_causal_cache_hits"},
                                    dict(runtime._reentry_observations),
                                    runtime._residency_scan_cursor,
                                )
                                if iteration >= 5:
                                    for metric, value in costs.items():
                                        samples[name][metric].append(value)
                            if outputs["baseline"] != outputs["optimized"]:
                                raise AssertionError((queue_size, retained_rounds, calls, graph_interval, iteration))
                            if inspections["baseline"] != inspections["optimized"]:
                                raise AssertionError("native residency inspection changed")
                        costs = {
                            name: {metric: distribution(values) for metric, values in measurements.items()}
                            for name, measurements in samples.items()
                        }
                        results.append({
                            "queue_size": queue_size, "retained_rounds": retained_rounds,
                            "planning_calls_per_cycle": calls, "graph_change_interval": graph_interval,
                            "plans_and_live_residency_equal": True,
                            "native_inspection_counts_equal": True,
                            "native_inspection_count": sum(inspections["baseline"].values()),
                            "causal_cache_hits": {
                                name: runtime.counts["admission_causal_cache_hits"]
                                for name, (runtime, _) in variants.items()
                            },
                            "costs": costs,
                            "mean_wall_reduction_fraction": (
                                1 - costs["optimized"]["wall_ms"]["mean_ms"]
                                / costs["baseline"]["wall_ms"]["mean_ms"]
                            ),
                        })
                    finally:
                        for runtime, _ in variants.values():
                            runtime.close()
    return {
        "scope": (
            "CPU scheduler maintenance plus complete causal/resident-first admission cycles; "
            "real versioned causal graph and request identities, alternating execution order, "
            "changing stub residency beyond its observation expiry and graph changes every "
            "1/4/16 cycles. Native inspection traces, live observations, plans and non-cache "
            "policy counters must agree. No physical allocator, DMA or GPU/JCT measurement."
        ),
        "baseline_revision": revision, "baseline_source_sha256": digests,
        "optimized_runtime_source": str(runtime_source_path()),
        "optimized_runtime_source_sha256": hashlib.sha256(runtime_source_path().read_bytes()).hexdigest(),
        "iterations_per_case": iterations, "warmup_iterations": 5,
        "logical_cycle_ms": 51., "cases": results,
    }


def benchmark_residency_scan_reuse(*, revision: str, rounds: int, iterations: int) -> dict:
    from benchmark_native_reentry_cpu import fixture, variant

    baseline, frontier, digests = baseline_classes(revision)
    native = variant(ROOT / "third_party/sglang-v0.5.20")
    cases = []
    clock = [1000.]
    with patch.object(runtime_module.time, "monotonic", new=lambda: clock[0]):
        for size, tokens, depth, leaves, aged in (
            (4, 32768, 8, 8, False),
            (156, 32768, 8, 8, False),
            (156, 98304, 24, 8, False),
            (156, 32768, 256, 8, False),
            (512, 32768, 8, 1, False),
            (156, 32768, 8, 8, True),
        ):
            variants = {
                "baseline": synthetic_runtime(baseline, frontier, size, rounds),
                "optimized": synthetic_runtime(
                    NativeAdmissionRuntime, CausalFrontierScheduler, size, rounds,
                ),
            }
            inspections = Counter()
            samples = {name: {"wall_ms": [], "thread_cpu_ms": []} for name in variants}
            try:
                for name, (runtime, queue) in variants.items():
                    cache, template = fixture(native, {
                        "tokens": tokens, "depth": depth, "leaves": leaves, "page_size": 1,
                    })
                    for node in cache.tree_core.node_by_id.__self__.values():
                        if node.parent is not None:
                            node.component_data[0].value = None
                            node.component_data[2].value = None

                    def inspect(request, *, cache=cache, name=name):
                        inspections[name] += 1
                        return native.inspect(cache, request)

                    cache.inspect_beliefkv_reentry = inspect
                    runtime.enable_resident_first = True
                    runtime.attach_native_cache(cache)
                    for request in queue:
                        request.origin_input_ids = template.origin_input_ids
                for iteration in range(iterations + 5):
                    clock[0] += .06
                    names = list(variants)
                    if iteration % 2:
                        names.reverse()
                    orders = []
                    for name in names:
                        runtime, queue = variants[name]
                        runtime._visible_since = {
                            request.rid: clock[0] - (11. if aged else 0.)
                            for request in queue
                        }
                        runtime.scheduler_step(queue)
                        started, cpu_started = time.perf_counter_ns(), time.thread_time_ns()
                        plans = tuple(
                            runtime.plan_native_prefill(queue, running_batch=None, adder=None)
                            for _ in range(2)
                        )
                        cpu_ms = (time.thread_time_ns() - cpu_started) / 1_000_000
                        wall_ms = (time.perf_counter_ns() - started) / 1_000_000
                        orders.append(tuple(plan.prioritized for plan in plans))
                        if iteration >= 5:
                            samples[name]["wall_ms"].append(wall_ms)
                            samples[name]["thread_cpu_ms"].append(cpu_ms)
                    if orders[0] != orders[1]:
                        raise AssertionError("Host-only or aged queue order changed")
                costs = {
                    name: {metric: distribution(values) for metric, values in data.items()}
                    for name, data in samples.items()
                }
                cases.append({
                    "queue_size": size, "retained_rounds": rounds,
                    "tokens": tokens, "depth": depth, "session_leaves": leaves,
                    "aged": aged, "iterations": iterations, "warmup_iterations": 5,
                    "planning_calls_per_cycle": 2, "clock_step_ms": 60,
                    "queue_order_equal": True,
                    "native_inspections_including_warmup": dict(inspections),
                    "optimized_scan_reuses": variants["optimized"][0].counts[
                        "admission_residency_scan_reused"
                    ],
                    "costs": costs,
                    "mean_wall_reduction_fraction": (
                        1 - costs["optimized"]["wall_ms"]["mean_ms"]
                        / costs["baseline"]["wall_ms"]["mean_ms"]
                    ),
                })
            finally:
                for runtime, _ in variants.values():
                    runtime.close()
    return {
        "scope": (
            "CPU-only two-call admission cycles with the actual native reentry inspector "
            "and RadixKey on synthetic Host-only radix fixtures; cache observations expire "
            "before each cycle. No native allocation, DMA, GPU or throughput is measured."
        ),
        "baseline_revision": revision, "baseline_source_sha256": digests,
        "optimized_runtime_source_sha256": hashlib.sha256(
            runtime_source_path().read_bytes(),
        ).hexdigest(),
        "native_source_sha256": native.source_sha256,
        "sampling_change": (
            "Rotate once for unchanged handoff/admission targets in a scheduler cycle; "
            "mixed-residency queues can differ because the second call no longer samples "
            "another eight requests. Host-only and aged fixture orders are checked."
        ),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="7055901")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--planning-calls", type=int, choices=(1, 2), default=1)
    parser.add_argument("--handoff-frontier", action="store_true")
    parser.add_argument("--causal-reuse", action="store_true")
    parser.add_argument("--residency-scan-reuse", action="store_true")
    parser.add_argument("--resident-first", action="store_true",
                        help="Use mixed residency observations and check identical native reads.")
    parser.add_argument("--parent-handoffs", type=int, default=0,
                        help="Waiting parent contexts tracked during unrelated child decode.")
    parser.add_argument("--semantic-progress", action="store_true",
                        help="Advance child token progress in each completed-batch callback.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime_source_path()
    if min(args.workflows, args.rounds, args.iterations) < 1 or args.workflows > 512:
        raise ValueError("positive sizes and at most 512 queued requests required")
    if not 0 <= args.parent_handoffs <= args.workflows:
        raise ValueError("parent handoffs must fit the synthetic workflow count")
    if args.handoff_frontier and (args.parent_handoffs or args.semantic_progress):
        raise ValueError("completed-batch tracking options do not apply to handoff frontier")
    if args.residency_scan_reuse:
        report = benchmark_residency_scan_reuse(
            revision=args.baseline_revision, rounds=args.rounds, iterations=args.iterations,
        )
    elif args.causal_reuse:
        if args.handoff_frontier or args.parent_handoffs or args.semantic_progress:
            raise ValueError("causal reuse measures scheduler/admission cycles only")
        clock = [1000.]
        with patch.object(runtime_module.time, "monotonic", new=lambda: clock[0]):
            report = benchmark_causal_reuse(
                revision=args.baseline_revision, workflows=args.workflows,
                rounds=args.rounds, iterations=args.iterations,
                planning_calls=args.planning_calls, clock=clock,
            )
    elif args.handoff_frontier:
        report = benchmark_handoff_frontier(
            revision=args.baseline_revision, workflows=args.workflows,
            rounds=args.rounds, iterations=args.iterations,
        )
    else:
        report = benchmark(revision=args.baseline_revision, workflows=args.workflows,
                           rounds=args.rounds, iterations=args.iterations,
                           planning_calls=args.planning_calls,
                           resident_first=args.resident_first,
                           parent_handoffs=args.parent_handoffs,
                           semantic_progress=args.semantic_progress)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if args.residency_scan_reuse:
        for case in report["cases"]:
            print(
                f"queue={case['queue_size']} tokens={case['tokens']} "
                f"depth={case['depth']} aged={case['aged']}: "
                f"{case['costs']['baseline']['wall_ms']['mean_ms']:.6f} -> "
                f"{case['costs']['optimized']['wall_ms']['mean_ms']:.6f} ms; "
                f"native_reads={case['native_inspections_including_warmup']}"
            )
    elif args.causal_reuse:
        for case in report["cases"]:
            print(
                f"queue={case['queue_size']} rounds={case['retained_rounds']} "
                f"plans={case['planning_calls_per_cycle']} "
                f"graph_interval={case['graph_change_interval']}: "
                f"{case['costs']['baseline']['wall_ms']['mean_ms']:.6f} -> "
                f"{case['costs']['optimized']['wall_ms']['mean_ms']:.6f} ms "
                f"({case['mean_wall_reduction_fraction'] * 100:.2f}% lower)"
            )
    elif args.handoff_frontier:
        print(json.dumps({"scope": report["scope"], "scenarios": report["scenarios"]}, indent=2))
    else:
        print(json.dumps({key: report[key] for key in (
            "scope", "queue_order_equal", "costs", "admission_mean_reduction_fraction",
            "batch_state_equal", "batch_completed_mean_reduction_fraction",
        )}, indent=2))


if __name__ == "__main__":
    main()
