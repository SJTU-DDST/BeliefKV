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
import time
from types import SimpleNamespace as NS

from beliefkv.control.causal_graph import (
    ContextRecord, InvocationRecord, InvocationState, JoinRecord,
    WorkflowRecord,
)
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
import beliefkv.policy.causal_frontier as frontier_module
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module


ROOT = Path(__file__).resolve().parents[1]


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
        "on_batch_completed",
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


def benchmark(*, revision: str, workflows: int, rounds: int, iterations: int) -> dict:
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
                started = time.perf_counter_ns()
                plan = runtime.plan_native_prefill(queue, running_batch=None, adder=None)
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
            "workflows": workflows, "retained_rounds_per_workflow": rounds,
            "invocations": workflows * (1 + rounds * 3), "joins": workflows * rounds,
            "waiting_requests": workflows, "iterations": iterations,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="7055901")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.workflows, args.rounds, args.iterations) < 1 or args.workflows > 512:
        raise ValueError("positive sizes and at most 512 queued requests required")
    report = benchmark(revision=args.baseline_revision, workflows=args.workflows,
                       rounds=args.rounds, iterations=args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "scope", "queue_order_equal", "costs", "admission_mean_reduction_fraction",
    )}, indent=2))


if __name__ == "__main__":
    main()
