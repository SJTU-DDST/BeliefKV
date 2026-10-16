#!/usr/bin/env python3
"""Compare cold-capacity reclaim using identical ordered CPU cache fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean, median
import time
from types import SimpleNamespace as NS


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_variants(baseline: Path, candidate: Path):
    test_path = candidate / "test/srt/test_beliefkv_execution_handoff.py"
    spec = importlib.util.spec_from_file_location("known_wait_reclaim_fixture", test_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    classes = {}
    for name, checkout in (("baseline", baseline), ("candidate", candidate)):
        module.SOURCE = checkout / "python/sglang/srt/mem_cache/unified_radix_cache.py"
        classes[name] = module.cache_class()
    return module, classes


def fixture(module, classes, *, leaf_count: int, kind: str):
    root = module.Node(id=0, parent=None, creation_time=0)
    nodes = [
        module.Node(
            id=index + 1, parent=root, creation_time=index + 1,
            component_data=[module.data(), module.data(False, False, 0), module.data()],
            write_through_pending_id=None, load_back_pending_id=None, backuped=True,
        )
        for index in range(leaf_count)
    ]

    class Leaves(set):
        def __iter__(self):
            return iter(nodes)

    leaves = Leaves(nodes)
    by_id = {node.id: node for node in nodes}
    tree = NS(root_node=root, node_by_id=by_id.__getitem__, evictable_device_leaves=leaves)
    victim = nodes[0]
    caches = {}
    for name, cls in classes.items():
        cache = cls()
        cache.tree_core = tree
        cache.is_write_back = True
        cache.ongoing_write_through = {}
        cache.beliefkv_join_pressure_candidates = (
            () if kind in ("no_waiter", "already_enough")
            else ((victim.id, victim.creation_time),)
        )
        cache.beliefkv_join_pressure_validator = lambda *_: kind != "invalid_waiter"
        free = {0: 0, 2: 0}
        cache._component_available_size = free.__getitem__

        def demote(node_id, tracker, *, free=free):
            assert node_id == victim.id
            free[0] += 2
            free[2] += 1
            tracker[0] = tracker.get(0, 0) + 2
            tracker[2] = tracker.get(2, 0) + 1

        cache._demote = demote
        caches[name] = cache, free
    return caches


def benchmark(module, classes, *, leaf_count, kind, diagnostic, cold_scan, calls, repeats):
    caches = fixture(module, classes, leaf_count=leaf_count, kind=kind)
    expected = {0: 2, 2: 1} if kind in ("known_waiter", "partial_waiter") else {}
    target = 0 if kind == "already_enough" else 4 if kind == "partial_waiter" else 2
    states = {}
    costs = {name: [] for name in classes}
    wall = {name: [] for name in classes}

    def cycle(name):
        cache, free = caches[name]
        free[0] = free[2] = 0
        if cold_scan:
            cache._beliefkv_handoff_full_scan = None
        result = cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=target, mamba_slots=0 if target == 0 else 1,
            collect_diagnostics=diagnostic,
        )
        if result != expected:
            raise AssertionError((name, kind, result, expected))
        return cache, free

    for name in classes:
        for _ in range(10):
            cycle(name)
    for repeat in range(repeats):
        order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
        for name in order:
            cpu, started = time.thread_time_ns(), time.perf_counter_ns()
            for _ in range(calls):
                cache, free = cycle(name)
            wall[name].append((time.perf_counter_ns() - started) / calls / 1000.)
            costs[name].append((time.thread_time_ns() - cpu) / calls / 1000.)
            states[name] = {
                "freed": dict(free),
                "victims": cache.beliefkv_handoff_last_victims,
                "native_scanned": (
                    cache.beliefkv_handoff_last_capacity["native_full_scanned"]
                    if diagnostic else None
                ),
            }
    for field in ("freed", "victims"):
        if states["baseline"][field] != states["candidate"][field]:
            raise AssertionError((kind, field, states))
    return {
        "leaf_count": leaf_count, "kind": kind, "diagnostics": diagnostic,
        "reset_scan_each_call": cold_scan, "calls_per_repeat": calls,
        "thread_cpu_us": {
            name: {"mean": mean(values), "p50": median(values), "samples": values}
            for name, values in costs.items()
        },
        "wall_us": {
            name: {"mean": mean(values), "p50": median(values), "samples": values}
            for name, values in wall.items()
        },
        "thread_cpu_reduction_fraction": 1 - mean(costs["candidate"]) / mean(costs["baseline"]),
        "final_states": states,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    module, classes = load_variants(args.baseline, args.candidate)
    report = {
        "scope": (
            "Exact engine methods on shared ordered Python cache fixtures, with identical "
            "allocator counters, causal validation and victims. No GPU, DMA, real allocator "
            "latency, trace-frequency weighting, JCT or throughput claim."
        ),
        "baseline_source_sha256": digest(args.baseline / "python/sglang/srt/mem_cache/unified_radix_cache.py"),
        "candidate_source_sha256": digest(args.candidate / "python/sglang/srt/mem_cache/unified_radix_cache.py"),
        "benchmark_sha256": digest(Path(__file__)),
        "cases": [
            benchmark(
                module, classes, leaf_count=leaves, kind=kind,
                diagnostic=diagnostic, cold_scan=cold_scan,
                calls=args.calls, repeats=args.repeats,
            )
            for leaves in (64, 256, 1024)
            for kind in ("known_waiter", "partial_waiter", "invalid_waiter", "no_waiter", "already_enough")
            for diagnostic in (False, True)
            for cold_scan in (False, True)
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "cases": len(report["cases"]),
        "known_waiter_reduction_range": [
            min(row["thread_cpu_reduction_fraction"] for row in report["cases"] if row["kind"] == "known_waiter"),
            max(row["thread_cpu_reduction_fraction"] for row in report["cases"] if row["kind"] == "known_waiter"),
        ],
    }))


if __name__ == "__main__":
    main()
