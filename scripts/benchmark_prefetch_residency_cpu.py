#!/usr/bin/env python3
"""Compare restore protection on identical CPU tree and native-lock fixtures."""

from __future__ import annotations

import argparse
from array import array
import ast
from collections import Counter, OrderedDict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep, PhysicalActionCompleted
from beliefkv.runtime import sglang_v0520_observer as observer
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from tests.test_sglang_v0520_observer import _static_cache

SOURCE = Path("beliefkv/runtime/sglang_v0520_runtime.py")


def load_method(source: str, label: str):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_register_prefetch_service_lease")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    namespace = dict(vars(runtime_module))
    exec(compile(ast.fix_missing_locations(module), label, "exec"), namespace)
    return namespace[method.name]


def fixture(depth: int, mode: str, *, native_budget: bool = False):
    runtime = runtime_module.NativeAdmissionRuntime()
    cache = _static_cache()
    cache.token_to_kv_pool_allocator.size = 32768
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 32768
    width = 32768 // depth
    node_type, component_type, tree_type = (
        type(name, (NS,), {}) for name in ("UnifiedTreeNode", "ComponentData", "UnifiedTreeCore")
    )
    nodes = {}
    for index in range(depth + 1):
        def component(present):
            return component_type(
                value=range(width) if present else None, host_value=None,
                lock_ref=0, host_lock_ref=0, session_ref=0, session_ids=None,
            )
        nodes[index] = node_type(
            id=index, creation_time=float(index), parent=nodes.get(index - 1),
            key=array("q", range(width)) if index else array("q"),
            component_data={0: component(bool(index)), 2: component(index == depth)},
            write_through_pending_id=None, load_back_pending_id=None,
        )
    locks, records = [], []

    def acquire(node_id):
        locks.append(("acquire", node_id))
        return NS(to_dec_params=lambda: NS(node_id=node_id))

    cache.tree_core = tree_type(
        node_by_id=nodes.__getitem__, inc_lock_ref=acquire,
        dec_lock_ref=lambda node_id, receipt: locks.append(("release", node_id)),
    )
    key = PrefillCandidateKey("next", "workflow", "agent", "context", 0, 0, "session", 1)
    target = depth // 2 if mode == "ancestor_covered" else depth
    existing = depth // 2 if mode == "extend_lock" else depth
    runtime._native_cache = cache
    runtime._prefetch_steps = {}
    runtime._prefetch_issue_budgets = {}
    runtime._prefetch_first_services = {}
    runtime._reentry_observations = {}
    runtime._prefetch_service_leases = OrderedDict()
    runtime._prefetch_lock_release_failures = set()
    runtime._residency_budget = PrefetchResidencyBudget(4, 1024 ** 3)
    runtime._native_max_running = None
    runtime._native_prefill_slots = None
    if native_budget:
        cache.token_to_kv_pool_allocator.size = 65536
        cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 65536
        cache.token_to_kv_pool_allocator.available_size = lambda: 32768
        cache.req_to_token_pool.size = 48
        cache.req_to_token_pool.available_size = lambda: 2
        cache.full_evictable_size = lambda: 32768
        cache.mamba_evictable_size = lambda: 1
        runtime._native_max_running = 48
        runtime._native_prefill_slots = 2
        runtime._native_running_batch = NS(reqs=[NS()] * 46)
        runtime._native_page_size = 64
        runtime._native_input_reserve = 512
    runtime._opportunity_writer = NS(record=records.append)
    runtime._prefetch_lease_invalid_reason = lambda lease: None
    runtime.prefetch_lead_ms = 500.
    runtime.physical_disabled = False
    runtime.counts = Counter()
    if mode != "new_lock":
        runtime._prefetch_service_leases["prior"] = runtime_module._PrefetchServiceLease(
            key, "prior", existing, float(existing), "execution_handoff", None,
            0., 10., (("kv", width * 20),), lock_params=NS(node_id=existing),
            protected_bytes=existing * width * 20 + (80 if existing == depth else 0),
            demand_ready=True,
        )
    action = PhysicalActionCompleted(
        "restore", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (target,), (("kv", width * 20),), width * 20,
    )
    step = PrefetchLoadStep(key, target, float(target), target, float(target))
    runtime._prefetch_steps[action.command_id] = (step, "execution_handoff", None)
    return runtime, action, records, locks


def output(runtime, records, locks):
    records = [dict(row) for row in records]
    for row in records:
        if row.get("protection_reason") == "covered_by_existing_lock":
            # The optimized path explicitly omits a new allocation budget.
            row.pop("budget", None)
            row.pop("protection_request_slots", None)
            row.pop("protection_slot_role", None)
    return {
        "records": records, "locks": locks, "counts": dict(runtime.counts),
        "physical_disabled": runtime.physical_disabled,
        "pending_steps": sorted(runtime._prefetch_steps),
        "leases": {
            command: {
                "key": asdict(lease.key), "node_id": lease.node_id,
                "protected_bytes": lease.protected_bytes,
                "lock_node_id": getattr(lease.lock_params, "node_id", None),
                "expires_at": lease.expires_at,
            }
            for command, lease in runtime._prefetch_service_leases.items()
        },
    }


def distribution(values):
    return {"samples": len(values), "mean_ms": mean(values), "p50_ms": median(values),
            "p90_ms": sorted(values)[min(len(values) - 1, int(.9 * len(values)))]}


def measure(methods, *, depth: int, mode: str, native_budget: bool, iterations: int):
    costs = {name: {"wall": [], "thread_cpu": []} for name in methods}
    captures = {name: [] for name in methods}
    budget_queries = {}
    canonical = None
    real_capture = observer.observe_unified_node_closure
    with patch.object(runtime_module.time, "time", new=lambda: 1.), \
         patch.object(runtime_module.time, "monotonic", new=lambda: 1.):
        for iteration in range(iterations + 10):
            outputs = {}
            names = list(methods)
            if iteration % 2:
                names.reverse()
            for name in names:
                runtime, action, records, locks = fixture(
                    depth, mode, native_budget=native_budget,
                )
                captured = []

                def capture(cache, node_id, **kwargs):
                    captured.append(node_id)
                    return real_capture(cache, node_id, **kwargs)

                if iteration == 0:
                    with patch.object(
                        observer, "observe_unified_node_closure", side_effect=capture,
                    ), patch.object(
                        runtime, "_current_residency_budget",
                        wraps=runtime._current_residency_budget,
                    ) as budget:
                        methods[name](runtime, action)
                    budget_queries[name] = budget.call_count
                    captures[name].append(len(captured))
                else:
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    methods[name](runtime, action)
                    elapsed = {
                        "wall": (time.perf_counter_ns() - wall) / 1e6,
                        "thread_cpu": (time.thread_time_ns() - cpu) / 1e6,
                    }
                outputs[name] = output(runtime, records, locks)
                expected_locks = (
                    [("acquire", depth), ("release", depth // 2)]
                    if mode == "extend_lock" else
                    [("acquire", depth)] if mode == "new_lock" else []
                )
                if locks != expected_locks:
                    raise AssertionError(f"fixture did not exercise native protection: {mode}, {locks}")
                if iteration >= 10:
                    for clock, value in elapsed.items():
                        costs[name][clock].append(value)
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError(f"protection, lifecycle or records changed: {depth}, {mode}")
            canonical = json.dumps(outputs["optimized"], sort_keys=True)
    return {
        "depth": depth, "mode": mode, "native_budget": native_budget,
        "physical_outputs_and_locks_equal": True,
        "budget_refreshes_per_call": budget_queries,
        "covered_budget_metadata": "omitted; existing native lock requires no new reservation",
        "output_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "closure_captures_per_call": {name: sorted(set(values)) for name, values in captures.items()},
        "costs": {name: {clock: distribution(values) for clock, values in clocks.items()}
                  for name, clocks in costs.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline_revision}:{SOURCE.as_posix()}"], cwd=ROOT, text=True,
    )
    current = (ROOT / SOURCE).read_text()
    report = {
        "scope": (
            "CPU native-tree protection fixtures with the real scalar closure observer. "
            "Context validation is held constant. Constructor state is complete and "
            "new/extended locks must actually be acquired. Covered-lock budget metadata "
            "is excluded from equality, all lifecycle/lock outputs remain equal. "
            "Closure/budget counters run in a separate first reference pass, excluded "
            "from timed samples. Fixed clocks are plain callables, not timed mocks. "
            "No allocator, DMA, CUDA, admission or end-to-end "
            "performance is measured. Preserve slower paths."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(current.encode()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations_per_case": args.iterations,
        "cases": [
            measure({"baseline": load_method(baseline, args.baseline_revision),
                     "optimized": load_method(current, str(ROOT / SOURCE))},
                    depth=depth, mode=mode, native_budget=native_budget,
                    iterations=args.iterations)
            for depth in (8, 64, 512)
            for mode in ("same_node_covered", "ancestor_covered", "new_lock", "extend_lock")
            for native_budget in (False, True)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for case in report["cases"]:
        before = case["costs"]["baseline"]["wall"]["mean_ms"]
        after = case["costs"]["optimized"]["wall"]["mean_ms"]
        print(
            f"depth={case['depth']} mode={case['mode']} native={case['native_budget']}: "
            f"{before:.4f} -> {after:.4f} ms"
        )


if __name__ == "__main__":
    main()
