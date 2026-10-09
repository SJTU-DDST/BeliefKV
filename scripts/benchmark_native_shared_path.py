#!/usr/bin/env python3
"""Compare the pinned old and current native-admission CPU paths, without a GPU."""

from __future__ import annotations

import argparse
import ast
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
    }
    original.name = "BaselineRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [item for item in original.body
                     if isinstance(item, ast.FunctionDef) and item.name in methods]
    for method in original.body:
        method.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[]))
    namespace = dict(vars(runtime_module))
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
              planning_calls: int = 1) -> dict:
    baseline, frontier, digests = baseline_classes(revision)
    variants = {
        "baseline": synthetic_runtime(baseline, frontier, workflows, rounds),
        "optimized": synthetic_runtime(NativeAdmissionRuntime, CausalFrontierScheduler,
                                       workflows, rounds),
        "optimized_profiled": synthetic_runtime(NativeAdmissionRuntime, CausalFrontierScheduler,
                                                workflows, rounds, profiling=True),
    }
    samples = {name: {"admission": [], "batch_completed": []} for name in variants}
    try:
        for iteration in range(iterations + 5):
            orders = []
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
                started = time.perf_counter_ns()
                cycle_orders = []
                for call in range(planning_calls):
                    plan = runtime.plan_native_prefill(
                        queue, running_batch=None, adder=None if call == 0 else NS(),
                    )
                    cycle_orders.append(plan.prioritized)
                if any(order != cycle_orders[0] for order in cycle_orders[1:]):
                    raise AssertionError("repeated planning changed the queue order")
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                orders.append(plan.prioritized)
                if iteration >= 5:
                    samples[name]["admission"].append(elapsed)
                started = time.perf_counter_ns()
                runtime.on_batch_completed(NS(reqs=queue[:48]))
                if iteration >= 5:
                    samples[name]["batch_completed"].append(
                        (time.perf_counter_ns() - started) / 1_000_000
                    )
            if any(order != orders[0] for order in orders[1:]):
                raise AssertionError(f"optimization changed the synthetic queue order at {iteration}")
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
            "planning_scope": (
                "causal admission calls only; excludes physical handoff inspection and enqueue"
            ),
            "invalidate_caches_each_iteration": True, "queue_order_equal": True,
            "costs": costs,
            "admission_mean_reduction_fraction": (
                1 - costs["optimized"]["admission"]["mean_ms"]
                / costs["baseline"]["admission"]["mean_ms"]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="7055901")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--planning-calls", type=int, choices=(1, 2), default=1)
    parser.add_argument("--handoff-frontier", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime_source_path()
    if min(args.workflows, args.rounds, args.iterations) < 1 or args.workflows > 512:
        raise ValueError("positive sizes and at most 512 queued requests required")
    if args.handoff_frontier:
        report = benchmark_handoff_frontier(
            revision=args.baseline_revision, workflows=args.workflows,
            rounds=args.rounds, iterations=args.iterations,
        )
    else:
        report = benchmark(revision=args.baseline_revision, workflows=args.workflows,
                           rounds=args.rounds, iterations=args.iterations,
                           planning_calls=args.planning_calls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if args.handoff_frontier:
        print(json.dumps({"scope": report["scope"], "scenarios": report["scenarios"]}, indent=2))
    else:
        print(json.dumps({key: report[key] for key in (
            "scope", "queue_order_equal", "costs", "admission_mean_reduction_fraction",
        )}, indent=2))


if __name__ == "__main__":
    main()
