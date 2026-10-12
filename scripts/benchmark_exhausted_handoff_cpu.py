#!/usr/bin/env python3
"""Measure repeated exhausted handoff probes using the real native inspector."""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime import sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_reentry_cpu import variant
from scripts.benchmark_native_shared_path import baseline_classes, synthetic_runtime
from tests.test_sglang_v0520_observer import UnifiedTreeCore, _node, _static_cache


def fixture(runtime_class, frontier_class, inspector, *, queue_size, depth, clock):
    runtime, requests = synthetic_runtime(runtime_class, frontier_class, queue_size, 4)
    runtime.enable_execution_handoff = True
    cache = _static_cache()
    cache.token_to_kv_pool_allocator.size = 2_000_000
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 2_000_000
    cache.host_pool_group.entry_map["kv"].host_pool.size = 4_000_000
    cache.req_to_token_pool.available_size = lambda: 128
    root = _node(0)
    root.key = inspector.key(array("q"))
    nodes, sessions = {0: root}, {}
    raw = array("q", range(32769))
    width = 32768 // depth
    for request in requests:
        parent = root
        for offset in range(depth):
            node_id = len(nodes)
            resident = offset < depth // 2
            node = _node(
                node_id, parent,
                device=range(width) if resident else (),
                host=range(width), mamba_host=offset == depth - 1,
            )
            node.key = inspector.key(raw[offset * width:(offset + 1) * width])
            if offset != depth - 1:
                node.component_data[2].value = None
                node.component_data[2].host_value = None
            elif not resident:
                node.component_data[2].value = None
            nodes[node_id] = node
            parent = node
        sessions[request.session_id] = parent
        request.origin_input_ids = raw
    cache.tree_core = UnifiedTreeCore(
        root_node=root, node_by_id=nodes.__getitem__, is_eagle=False,
    )
    cache.page_size = 1
    cache.session_refs = NS(
        session_id_for_req=lambda request: request.session_id,
        snapshot_latest_session_leaf_anchors=lambda session, generation, max_leaves: (
            (0, ((sessions[session].id, sessions[session].creation_time),)),
            (2, ((sessions[session].id, sessions[session].creation_time),)),
        ),
    )
    reads = Counter()

    def inspect(request):
        reads["native_reentry"] += 1
        return inspector.inspect(cache, request)

    cache.inspect_beliefkv_reentry = inspect
    runtime.attach_native_cache(cache)
    runtime._native_max_running, runtime._native_prefill_slots = 48, 4
    runtime._native_page_size, runtime._native_input_reserve = 1, 4096
    runtime._prefill_cycle_active = True
    runtime._execution_handoff_attempted.update(runtime.visible.values())
    runtime._visible_since = {request.rid: clock[0] for request in requests}
    return runtime, requests, reads


def state(runtime):
    return {
        "ticket": runtime._execution_handoff,
        "pending_transfers": runtime.physical_ledger.pending_count,
        "leases": len(runtime._prefetch_service_leases),
        "physical_disabled": runtime.physical_disabled,
        "attempted": sorted(key.request_id for key in runtime._execution_handoff_attempted),
        "visible": sorted(runtime.visible),
    }


def measure(classes, inspector, *, queue_size, depth, iterations, clock):
    variants = {
        name: fixture(cls, frontier, inspector, queue_size=queue_size, depth=depth, clock=clock)
        for name, (cls, frontier) in classes.items()
    }
    timings = {name: {"wall_us": [], "thread_cpu_us": []} for name in variants}
    running = NS(reqs=[NS(rid=f"running-{index}") for index in range(48)])
    try:
        for iteration in range(iterations + 10):
            clock[0] += .051
            names = list(variants)
            if iteration % 2:
                names.reverse()
            outputs = {}
            for name in names:
                runtime, requests, _ = variants[name]
                runtime._prefill_residency_scan = None
                runtime._visible_since = {request.rid: clock[0] for request in requests}
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                runtime.dispatch_execution_handoff(requests, running_batch=running)
                elapsed = {
                    "thread_cpu_us": (time.thread_time_ns() - cpu) / 1000.,
                    "wall_us": (time.perf_counter_ns() - wall) / 1000.,
                }
                outputs[name] = state(runtime)
                assert runtime._native_running_batch is running
                if iteration >= 10:
                    for kind, amount in elapsed.items():
                        timings[name][kind].append(amount)
            assert outputs["baseline"] == outputs["optimized"]
        costs = {
            name: {
                kind: {"mean": mean(values), "p50": median(values), "samples": values}
                for kind, values in clocks.items()
            } for name, clocks in timings.items()
        }
        return {
            "queue_size": queue_size, "radix_depth": depth,
            "request_input_tokens": 32769,
            "handoff_actions_and_bindings_equal": True,
            "costs": costs,
            "reads_including_warmup": {
                name: dict(reads) for name, (_, _, reads) in variants.items()
            },
            "reduction_fraction": {
                kind: 1. - costs["optimized"][kind]["mean"] / costs["baseline"][kind]["mean"]
                for kind in ("wall_us", "thread_cpu_us")
            },
        }
    finally:
        for runtime, _, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    if args.output.exists():
        raise ValueError("preserve the existing benchmark")
    baseline, frontier, digests = baseline_classes(args.baseline_commit)
    inspector = variant(args.engine_root)
    clock = [1000.]
    before_gc = gc.get_count()
    gc.collect()
    gc.freeze()
    try:
        with patch.object(runtime_module.time, "monotonic", side_effect=lambda: clock[0]):
            results = [
                measure(
                    {"baseline": (baseline, frontier),
                     "optimized": (runtime_module.NativeAdmissionRuntime, CausalFrontierScheduler)},
                    inspector, queue_size=size, depth=depth, iterations=args.iterations, clock=clock,
                )
                for size in (1, 48, 156)
                for depth in (8, 64)
            ]
    finally:
        gc.unfreeze()
    report = {
        "scope": (
            "Repeated probes of queues whose tagged requests were already attempted. "
            "Same real native read-only inspector, RadixKey and CPU trees; "
            "no allocator mutation, DMA, GPU throughput or JCT measurement. "
            "This fixture selects the exhausted case and does not estimate its live frequency."
        ),
        "baseline_commit": args.baseline_commit, "baseline_source_sha256": digests,
        "candidate_runtime_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/sglang_v0520_runtime.py").read_bytes(),
        ).hexdigest(),
        "native_inspector_sources": inspector.source_sha256,
        "iterations": args.iterations,
        "gc": {"collected_and_frozen_like_server": True, "pre_collect_counts": before_gc},
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for row in results:
        print(
            f'queue={row["queue_size"]} depth={row["radix_depth"]}: '
            f'{row["costs"]["baseline"]["thread_cpu_us"]["mean"]:.1f} -> '
            f'{row["costs"]["optimized"]["thread_cpu_us"]["mean"]:.1f} us'
        )


if __name__ == "__main__":
    main()
