#!/usr/bin/env python3
"""Compare exact reclaim methods with ordered leaves and native duplicate indexes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
import time
from types import SimpleNamespace as NS

from benchmark_known_wait_reclaim_cpu import digest, load_variants


def fixture(module, cls, *, leaves_count, kind):
    root = module.Node(id=0, parent=None, creation_time=0)
    nodes = [
        module.Node(
            id=index + 1, parent=root, creation_time=index + 1,
            component_data=[
                module.data(refs=0), module.data(False, False, 0), module.data(refs=0),
            ],
            write_through_pending_id=None, load_back_pending_id=None, backuped=True,
        )
        for index in range(leaves_count)
    ]
    iterations = {"leaves": 0, "items": 0}

    class Leaves(set):
        def __iter__(self):
            iterations["leaves"] += 1
            for node in nodes:
                iterations["items"] += 1
                yield node

    leaves = Leaves(nodes)
    duplicates = {node.id: node for node in nodes}
    victim = nodes[-1]
    if kind in ("sparse", "empty", "locked"):
        for node in nodes[:-1]:
            node.backuped = False
            node.component_data[0].host_value = None
        duplicates = {} if kind == "empty" else {victim.id: victim}
        if kind == "empty":
            victim.backuped = False
            victim.component_data[0].host_value = None
        if kind == "locked":
            victim.component_data[0].lock_ref = 1
    elif kind == "dense_interior":
        for index in range(leaves_count, leaves_count * 2):
            interior = module.Node(id=index + 1)
            duplicates[interior.id] = interior
    elif kind == "known_waiter":
        victim = nodes[0]
        victim.component_data[0].session_ref = 1
        victim.component_data[2].session_ref = 1

    tree = NS(
        root_node=root, node_by_id={node.id: node for node in nodes}.__getitem__,
        evictable_device_leaves=leaves,
    )
    if kind != "legacy":
        tree.full_host_duplicates = duplicates
    cache = cls()
    cache.tree_core = tree
    cache.is_write_back = True
    cache.ongoing_write_through = {}
    cache.beliefkv_join_pressure_candidates = (
        ((victim.id, victim.creation_time),) if kind == "known_waiter" else ()
    )
    cache.beliefkv_join_pressure_validator = lambda *_: kind == "known_waiter"
    free = {0: 0, 2: 0}
    cache._component_available_size = free.__getitem__

    def demote(node_id, tracker):
        node = tree.node_by_id(node_id)
        assert node.backuped and node.component_data[0].host_value is not None
        assert node.component_data[0].lock_ref == 0
        assert node.load_back_pending_id is None
        free[0] += 2
        free[2] += 1
        tracker[0] = tracker.get(0, 0) + 2
        tracker[2] = tracker.get(2, 0) + 1

    cache._demote = demote
    return cache, free, iterations


def benchmark(module, classes, *, leaves_count, kind, diagnostic, calls, repeats):
    fixtures = {
        name: fixture(module, cls, leaves_count=leaves_count, kind=kind)
        for name, cls in classes.items()
    }
    costs = {name: [] for name in classes}
    observations = {}

    def cycle(name):
        cache, free, iterations = fixtures[name]
        free[0] = free[2] = 0
        iterations["leaves"] = iterations["items"] = 0
        cache._beliefkv_handoff_full_scan = None
        result = cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=0 if kind == "already_enough" else 2, mamba_slots=0,
            collect_diagnostics=diagnostic,
        )
        expected_success = kind not in ("empty", "locked", "already_enough")
        if kind == "sparse" and name == "baseline" and leaves_count > 64:
            expected_success = False
        expected = {0: 2, 2: 1} if expected_success else {}
        if result != expected:
            raise AssertionError((name, kind, leaves_count, result, expected))
        return {
            "freed_units": result,
            "victims": [row["node_id"] for row in cache.beliefkv_handoff_last_victims],
            "leaf_iterations": dict(iterations),
            "scanned": (
                cache.beliefkv_handoff_last_capacity["native_full_scanned"]
                if diagnostic else None
            ),
        }

    for name in classes:
        for _ in range(10):
            cycle(name)
    for repeat in range(repeats):
        order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
        for name in order:
            started = time.thread_time_ns()
            for _ in range(calls):
                observation = cycle(name)
            costs[name].append((time.thread_time_ns() - started) / calls / 1000)
            observations[name] = observation
    if kind not in ("sparse",):
        for field in ("freed_units", "victims"):
            if observations["baseline"][field] != observations["candidate"][field]:
                raise AssertionError((kind, field, observations))
    return {
        "leaf_count": leaves_count, "kind": kind, "diagnostics": diagnostic,
        "calls_per_repeat": calls, "reset_scan_each_transaction": True,
        "thread_cpu_us": {
            name: {"mean": mean(values), "samples": values}
            for name, values in costs.items()
        },
        "thread_cpu_reduction_fraction": (
            1 - mean(costs["candidate"]) / mean(costs["baseline"])
        ),
        "outcomes": observations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calls", type=int, default=150)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.calls < 1 or args.repeats < 1:
        parser.error("calls and repeats must be positive")
    module, classes = load_variants(args.baseline, args.candidate)
    report = {
        "scope": (
            "Exact engine reclaim methods, ordered Python cache nodes, real live-eligibility "
            "checks and native duplicate-index shapes. Allocator/demotion counters are "
            "stand-ins; each call starts a fresh scan. Sparse cases intentionally compare "
            "missed capacity with reclaimed capacity, not identical work. No GPU, DMA, real "
            "allocator cost, trace weighting, JCT or throughput conclusion."
        ),
        "baseline_source_sha256": digest(
            args.baseline / "python/sglang/srt/mem_cache/unified_radix_cache.py"
        ),
        "candidate_source_sha256": digest(
            args.candidate / "python/sglang/srt/mem_cache/unified_radix_cache.py"
        ),
        "benchmark_sha256": digest(Path(__file__)),
        "cases": [
            benchmark(
                module, classes, leaves_count=count, kind=kind, diagnostic=diagnostic,
                calls=args.calls, repeats=args.repeats,
            )
            for count in (64, 256, 1024)
            for kind in (
                "sparse", "empty", "locked", "dense", "dense_interior",
                "known_waiter", "already_enough", "legacy",
            )
            for diagnostic in (False, True)
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "cases": len(report["cases"]),
        "range_by_kind": {
            kind: [
                min(row["thread_cpu_reduction_fraction"] for row in report["cases"] if row["kind"] == kind),
                max(row["thread_cpu_reduction_fraction"] for row in report["cases"] if row["kind"] == kind),
            ]
            for kind in sorted({row["kind"] for row in report["cases"]})
        },
    }))


if __name__ == "__main__":
    main()
