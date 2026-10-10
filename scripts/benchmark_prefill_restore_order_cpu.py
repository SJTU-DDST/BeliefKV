#!/usr/bin/env python3
"""Compare per-plan restore ordering on identical CPU causal/request fixtures."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType, SimpleNamespace as NS
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime import sglang_v0520_runtime as runtime_module


SOURCE = Path("beliefkv/runtime/sglang_v0520_runtime.py")


def load_plan(source: str, label: str):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "plan_native_prefill")
    method.decorator_list = []
    namespace = dict(vars(runtime_module))
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return namespace["plan_native_prefill"]


def fixture(plan, case, clock):
    runtime = runtime_module.NativeAdmissionRuntime()
    runtime.enable_final_stage_priority = False
    runtime.enable_resident_first = True
    runtime._prefill_cycle_active = True
    runtime._residency_budget = PrefetchResidencyBudget(
        8, 1024 ** 3, source="native_next_prefill",
    )
    runtime.plan_native_prefill = MethodType(plan, runtime)
    events = [RuntimeEvent("start", 0., RuntimeEventKind.WORKFLOW_START, "fixture")]
    requests = []
    for index in range(case["queue"]):
        rid = f"request-{index}"
        request = NS(
            rid=rid, cache_request_handle=NS(attempt_id=0),
            session_id=f"session-{index}", session_generation=1,
            beliefkv_metadata={
                "root_workflow_id": "fixture", "invocation_id": rid,
                "context_id": f"context-{index}", "context_epoch": 0,
            },
        )
        requests.append(request)
        runtime.register_visible_request(request)
        events.append(RuntimeEvent(
            f"create-{index}", float(index + 1), RuntimeEventKind.INVOCATION_CREATE,
            "fixture", invocation_id=rid, context_id=f"context-{index}",
            agent_definition_id="agent", agent_instance_id=rid,
        ))
    runtime.on_events(tuple(events))
    for index, request in enumerate(requests):
        runtime.graph.invocations[request.rid].pending_messages = int(index % 13 == 0)
    cache = NS(read_count=0)

    def inspect(request):
        cache.read_count += 1
        index = int(request.rid.rsplit("-", 1)[1])
        resident = (index + int(clock[0] * 20)) % 3 == 0
        return {
            "checkpoint_tokens": 32768,
            "device_checkpoint_tokens": 32768 if resident else 8192,
            "missing_full_tokens": 0 if resident else 24576,
            "missing_mamba_slots": int(not resident),
        }

    cache.inspect_beliefkv_reentry = inspect
    runtime.attach_native_cache(cache)
    for index in range(case["leases"]):
        target = requests[(index * 7 + 11) % len(requests)]
        key = runtime.visible[target.rid]
        source = ("execution_handoff", "tool_wait", "join_ticket")[index % 3]
        if source != "execution_handoff":
            key = replace(key, request_id=f"earlier-{target.rid}")
        if index % 11 == 10:
            key = replace(key, session_generation=2)
        command = f"restore-{index}"
        runtime._prefetch_service_leases[command] = runtime_module._PrefetchServiceLease(
            key, command, index + 1, float(index + 1), source, None,
            clock[0], 1_000_000. + index % 4, (("kv", 1024),),
            demand_ready=index % 5 != 4,
        )
    return runtime, requests, cache


def run_pair(variants, *, iteration, clock, case):
    clock[0] = 1000. + iteration * .051
    results, costs = {}, {}
    names = list(variants)
    if iteration % 2:
        names.reverse()
    for name in names:
        runtime, requests, cache = variants[name]
        runtime._prefill_causal_cache = None
        runtime._visible_since = {request.rid: clock[0] for request in requests}
        if case.get("aged_head"):
            runtime._visible_since[requests[0].rid] -= 11.
        runtime._prefetch_priority_normal_admissions = case.get("quota", 4)
        started_wall, started_cpu = time.perf_counter_ns(), time.thread_time_ns()
        plans = tuple(runtime.plan_native_prefill(
            requests, running_batch=None, adder=None,
        ) for _ in range(2))
        costs[name] = {
            "wall_ms": (time.perf_counter_ns() - started_wall) / 1e6,
            "thread_cpu_ms": (time.thread_time_ns() - started_cpu) / 1e6,
        }
        results[name] = (
            plans, dict(runtime.counts), cache.read_count,
            runtime._prefetch_priority_promoted,
            runtime._prefetch_priority_native_rank,
            runtime._prefetch_priority_aged_head_bypassed,
        )
    assert results["baseline"] == results["optimized"], (case, iteration)
    return costs


def distribution(values):
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values), "p50": statistics.median(values),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * .9))],
        "max": max(values), "samples": values,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    assert Path(runtime_module.__file__).resolve() == ROOT / SOURCE
    baseline_source = subprocess.run(
        ["git", "show", f"{args.baseline_commit}:{SOURCE.as_posix()}"],
        cwd=ROOT, check=True, text=True, capture_output=True,
    ).stdout
    optimized_source = (ROOT / SOURCE).read_text()
    implementations = {
        "baseline": load_plan(baseline_source, args.baseline_commit),
        "optimized": load_plan(optimized_source, str(ROOT / SOURCE)),
    }
    cases = [
        {"queue": queue, "leases": leases}
        for queue in (8, 48, 156) for leases in (0, 4, 16)
    ] + [
        {"queue": 156, "leases": 48},
        {"queue": 156, "leases": 16, "aged_head": True, "quota": 3},
    ]
    clean_environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("BELIEFKV_")
    }
    results, clock = [], [1000.]
    with patch.dict(os.environ, clean_environment, clear=True), \
         patch.object(runtime_module.time, "monotonic", side_effect=lambda: clock[0]):
        for case in cases:
            clock[0] = 1000.
            variants = {
                name: fixture(plan, case, clock) for name, plan in implementations.items()
            }
            samples = {
                name: {"wall_ms": [], "thread_cpu_ms": []} for name in variants
            }
            for iteration in range(args.iterations + 10):
                costs = run_pair(variants, iteration=iteration, clock=clock, case=case)
                if iteration >= 10:
                    for name, observations in costs.items():
                        for metric, value in observations.items():
                            samples[name][metric].append(value)
            measurements = {
                name: {metric: distribution(values) for metric, values in observations.items()}
                for name, observations in samples.items()
            }
            results.append({
                **case, "ordering_and_counts_equal": True,
                "native_inspection_count": variants["baseline"][2].read_count,
                "measurements": measurements,
            })
            for runtime, _, _ in variants.values():
                runtime.close()
    report = {
        "schema_version": 1, "baseline_commit": args.baseline_commit,
        "baseline_source_sha256": hashlib.sha256(baseline_source.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(optimized_source.encode()).hexdigest(),
        "imported_source": str(Path(runtime_module.__file__).resolve()),
        "iterations_per_case": args.iterations, "warmup_iterations": 10,
        "planning_calls_per_iteration": 2, "deployed": False, "cases": results,
        "scope": (
            "CPU plan_native_prefill with real causal/request identity handling, "
            "mixed restoration sources and deterministic stub-native residency. "
            "Logical time advances past the 50ms observation cache each iteration. "
            "Alternating variants must agree on both plans, cumulative counters, "
            "native read counts and promotion metadata. No real allocator, "
            "DMA, CUDA or end-to-end throughput is measured."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for result in results:
        before = result["measurements"]["baseline"]["wall_ms"]["mean"]
        after = result["measurements"]["optimized"]["wall_ms"]["mean"]
        print(f"queue={result['queue']} leases={result['leases']} "
              f"quota={result.get('quota', 4)}: {before:.6f} -> {after:.6f} ms "
              f"({100 * (1 - after / before):.2f}% lower)")


if __name__ == "__main__":
    main()
