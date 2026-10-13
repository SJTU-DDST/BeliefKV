#!/usr/bin/env python3
"""Compare post-reclaim planning with complete CPU checkpoint observations."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime import sglang_v0520_runtime as current
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import ContextSessionAnchors
from tests.test_sglang_v0520_observer import UnifiedTreeCore, _node, _static_cache
from tests.test_wait_prefetch_batch import batch_fixture

SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"
METHOD = "_reclaim_wait_prefetch_capacity"


def baseline_method(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    cls = next(node for node in ast.parse(source).body
               if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == METHOD)
    namespace = dict(vars(current))
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace[METHOD], hashlib.sha256(source.encode()).hexdigest()


def fixture(source, depth, case):
    runtime, key, _, _, _ = batch_fixture(source)
    cache = _static_cache()
    full_size = depth * 4 + 128
    cache.token_to_kv_pool_allocator.size = full_size
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = full_size
    cache.host_pool_group.entry_map["kv"].host_pool.size = full_size
    cache.req_to_token_pool.mamba_pool.size = 8
    cache.req_to_token_pool.mamba_allocator.size = 8
    cache.host_pool_group.entry_map["mamba"].host_pool.size = 8
    free = {"full": 1, "mamba": 1}
    cache.token_to_kv_pool_allocator.available_size = lambda: free["full"]
    cache.req_to_token_pool.mamba_allocator.available_size = lambda: free["mamba"]
    cache.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 128
    nodes = {0: _node(0)}
    nodes[0].key = ()
    for node_id in range(1, depth + 1):
        nodes[node_id] = _node(
            node_id, nodes[node_id - 1], host=range(4),
            mamba_host=node_id == depth,
        )
        nodes[node_id].key = range(4)
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    anchors = ContextSessionAnchors(
        key, tuple((component, ((depth, 10 + depth),)) for component in (0, 2)),
        1., reusable_input_tokens=depth * 4,
    )
    excluded = tuple(range(depth + 1))
    transfer_bytes = depth * 4 * 20 + 80
    initial_budget = PrefetchResidencyBudget(
        1, transfer_bytes, reserve_full_tokens=1, reserve_mamba_slots=1,
        evictable_full_tokens=depth * 4, evictable_mamba_slots=1,
    )
    runtime.attach_native_cache(cache)
    runtime._residency_budget = initial_budget
    runtime.snapshot_session_anchors = lambda *_args, **_kwargs: anchors
    runtime._publish_parent_pressure_candidates = lambda **_kwargs: None
    cache.prefetch_gpu_session_nodes = lambda **_kwargs: ()
    opportunity = runtime._wait_prefetch_opportunity(key)
    if opportunity is None or len(opportunity.steps) != depth:
        raise AssertionError("complete CPU checkpoint plan was not observed")
    if case == "partial":
        opportunity = replace(
            opportunity, partial_full_step=opportunity.steps[0], partial_full_tokens=2,
        )
    elif case == "probe":
        opportunity = replace(opportunity, capacity_reclaim_probe=True)

    def reclaim(**kwargs):
        if kwargs["exclude_node_ids"] != excluded:
            raise AssertionError("the target closure is not excluded from reclaim")
        free["full"], free["mamba"] = depth * 4 + 1, 2
        if case == "full_shortfall":
            free["full"] -= 1
        elif case == "mamba_shortfall":
            free["mamba"] -= 1
        elif case == "unobservable":
            free["full"] = None
        elif case == "nothing_freed":
            free["full"], free["mamba"] = 1, 1
            return {}
        return {0: depth * 4, 2: 1}

    cache.reclaim_beliefkv_handoff_capacity = reclaim

    def reset():
        free["full"], free["mamba"] = 1, 1
        runtime._residency_budget = replace(
            initial_budget,
            byte_limit=transfer_bytes - int(case == "byte_shortfall"),
        )
        runtime.counts.clear()

    return runtime, key, opportunity, reset


def signature(opportunity):
    # Diagnostic refresh metadata may differ; actionable plans and evidence must match.
    result = asdict(opportunity)
    result.pop("capacity_budget")
    result.pop("capacity_reclaim")
    return result


def measure(methods, *, source, depth, case, samples):
    runtime, key, opportunity, reset = fixture(source, depth, case)
    costs = {name: {"wall_us": [], "thread_cpu_us": []} for name in methods}
    calls = max(1, min(16, 128 // depth))
    canonical = None
    try:
        for sample in range(samples + 3):
            outputs = {}
            order = list(methods)
            if sample % 2:
                order.reverse()
            for name in order:
                wall_total = cpu_total = 0
                for _ in range(calls):
                    reset()
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    observed = methods[name](runtime, key, opportunity, source=source)
                    cpu_total += time.thread_time_ns() - cpu
                    wall_total += time.perf_counter_ns() - wall
                outputs[name] = signature(observed)
                if sample >= 3:
                    costs[name]["wall_us"].append(wall_total / 1000. / calls)
                    costs[name]["thread_cpu_us"].append(cpu_total / 1000. / calls)
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError((source, depth, case, outputs))
            canonical = outputs["baseline"]
        summaries = {
            name: {clock: {"mean": mean(values), "p50": median(values), "samples": values}
                   for clock, values in clocks.items()}
            for name, clocks in costs.items()
        }
        return {
            "source": source, "depth": depth, "case": case,
            "calls_per_sample": calls, "actionable_outputs_equal": True,
            "costs": summaries,
            "canonical_output_sha256": hashlib.sha256(
                json.dumps(canonical, sort_keys=True).encode(),
            ).hexdigest(),
            "thread_cpu_reduction_fraction": (
                1. - summaries["optimized"]["thread_cpu_us"]["mean"]
                / summaries["baseline"]["thread_cpu_us"]["mean"]
            ),
        }
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        raise ValueError("positive samples and a new output path required")
    baseline, digest = baseline_method(args.baseline_revision)
    methods = {"baseline": baseline, "optimized": getattr(current.NativeAdmissionRuntime, METHOD)}
    started = time.time()
    cases = [
        measure(methods, source=source, depth=depth, case=case, samples=args.samples)
        for source in ("join_ticket", "tool_wait")
        for depth in (4, 16, 64)
        for case in (
            "fit", "full_shortfall", "mamba_shortfall", "byte_shortfall",
            "unobservable", "nothing_freed", "partial", "probe",
        )
    ]
    report = {
        "scope": (
            "CPU live closure, headroom and burst planning with static pool stand-ins "
            "and a synthetic capacity reclaim; no DMA, real allocator or GPU gain."
        ),
        "baseline_revision": args.baseline_revision, "baseline_source_sha256": digest,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_ts_ms": started * 1000., "finished_ts_ms": time.time() * 1000.,
        "samples": args.samples, "actionable_outputs_equal": True, "cases": cases,
        "semantics": [
            "Target closure is excluded from the synthetic reclaim.",
            "Real closure/headroom/selection code runs on every fallback.",
            "Only input anchors, cold-victim publication and allocator mutation are stand-ins.",
            "Both variants receive identical free-list resets; actionable scalar evidence matches.",
            "Alternating measurement order; reset, warmup and serialization excluded.",
            "All regressions and raw wall/thread CPU samples retained.",
            "Final live target recapture before native enqueue remains outside this CPU benchmark.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "source", "depth", "case", "thread_cpu_reduction_fraction",
        )} for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
