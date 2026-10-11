#!/usr/bin/env python3
"""Compare native prefetch, load-back and transfer construction on CPU fixtures."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import time
from types import SimpleNamespace as NS
from typing import NamedTuple, Optional

import torch


class ComponentType(IntEnum):
    FULL = 0
    MAMBA = 2


@dataclass
class PoolTransfer:
    name: str
    host_indices: object = None
    device_indices: object = None
    nodes_to_load: object = None


def variant(root):
    directory = root / "python/sglang/srt/mem_cache"
    sources = {
        "cache": directory / "unified_radix_cache.py",
        "tree": directory / "unified_cache/unified_tree_core.py",
        "full": directory / "unified_cache/components/full.py",
        "mamba": directory / "unified_cache/components/mamba.py",
    }
    namespace = {
        "torch": torch, "NamedTuple": NamedTuple, "Optional": Optional,
        "ComponentType": ComponentType, "BASE_COMPONENT_TYPE": ComponentType.FULL,
        "PoolTransfer": PoolTransfer, "PoolName": NS(KV="kv", MAMBA="mamba"),
        "CacheTransferPhase": NS(
            BACKUP_HOST="backup", LOAD_BACK="load", BACKUP_STORAGE="storage",
            PREFETCH="prefetch",
        ),
    }
    selections = (
        ("cache", "UnifiedRadixCache", "Cache", {
            "prefetch_gpu_session_node", "prefetch_gpu_session_nodes",
            "_start_beliefkv_prefetch_loads", "load_back", "_load_back_transfers",
        }),
        ("tree", "UnifiedTreeCore", "Tree", {"build_load_back_spec"}),
        ("full", "FullComponent", "Full", {"build_hicache_transfers"}),
        ("mamba", "MambaComponent", "Mamba", {"build_hicache_transfers"}),
    )
    trees = {key: ast.parse(path.read_text()) for key, path in sources.items()}
    definitions = [
        node for node in trees["cache"].body
        if isinstance(node, ast.ClassDef)
        and node.name in ("PrefetchGpuNodeResult", "_OngoingLoadBack")
    ]
    for source, original, name, methods in selections:
        cls = next(node for node in trees[source].body
                   if isinstance(node, ast.ClassDef) and node.name == original)
        definitions.append(ast.ClassDef(
            name=name, bases=[], keywords=[], decorator_list=[],
            body=[node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name in methods],
        ))
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), *definitions,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(root), "exec"), namespace)
    return NS(**{name: namespace[name] for name in ("Cache", "Tree", "Full", "Mamba")},
              source_sha256={
                  str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sources.values()
              })


class Node:
    @property
    def evicted(self):
        return self.component_data[0].value is None


def fixture(implementation, case):
    size, depth, burst = case["tokens"], case["depth"], case["burst"]
    empty = NS(value=None, host_value=None, session_ids=None)
    root = Node()
    root.id, root.creation_time, root.parent = 0, 0., None
    root.component_data = [NS(value=torch.empty(0), host_value=None), empty, empty]
    nodes = {0: root}
    parent = root
    for index in range(1, depth + 1):
        node = Node()
        node.id, node.creation_time, node.parent = index, float(index), parent
        full = torch.arange(index * size, (index + 1) * size)
        state = torch.tensor([index])
        node.component_data = [
            NS(value=full, host_value=full, session_ids={"fixture"}),
            empty, NS(value=state, host_value=state, session_ids={"fixture"}),
        ]
        node.write_through_pending_id = node.load_back_pending_id = None
        nodes[index] = parent = node
    targets = [nodes[index] for index in range(depth - burst + 1, depth + 1)]
    leaf = nodes[depth]
    full, mamba = implementation.Full(), implementation.Mamba()
    full.component_type, mamba.component_type = ComponentType.FULL, ComponentType.MAMBA
    full._session_leaves = {"fixture": {leaf}}
    for comp in (full, mamba):
        comp.prepare_load_back = lambda node_id, **kwargs: None
        comp.finalize_load_back = lambda *args: None
    tree = implementation.Tree()
    tree.root_node, tree.is_write_back = root, True
    tree.components = (full, mamba)
    tree.components_by_type = {comp.component_type: comp for comp in tree.components}
    tree.node_by_id = nodes.__getitem__
    cache = implementation.Cache()
    cache.tree_core, cache.components = tree, tree.components_by_type
    cache._components_tuple = tree.components
    cache.host_memory_mode, cache.buffer_pipeline = "cache", None
    cache.enable_session_radix_cache = True
    cache.session_refs = NS(
        _session_generations={"fixture": 1}, _closed_session_ids=set(),
    )
    cache.ongoing_load_back, cache.ongoing_write_through = {}, {}
    cache.load_back_threshold = 32
    cache.token_to_kv_pool_allocator = NS(c128_attn_allocator=None)
    cache._component_available_size = lambda ct: depth * size
    cache._build_sidecar_transfers = lambda *args: []
    cache._apply_cache_actions = lambda actions: None
    lock = NS(delta=0, to_dec_params=lambda: "lock")
    cache.lock_counts = dict(device_acquire=0, device_release=0, host_acquire=0, host_release=0)

    def counted_lock(kind):
        def call(*args):
            cache.lock_counts[kind] += 1
            return lock
        return call

    cache.inc_lock_ref = counted_lock("device_acquire")
    cache.dec_lock_ref = counted_lock("device_release")
    cache.inc_host_lock_ref = counted_lock("host_acquire")
    cache.dec_host_lock_ref = counted_lock("host_release")
    cache.spec_builds = 0
    native_build = tree.build_load_back_spec

    def build(*args, **kwargs):
        cache.spec_builds += 1
        return native_build(*args, **kwargs)

    tree.build_load_back_spec = build
    controller = cache.cache_controller = NS(write_policy="write_back", load_queue=[])
    cache.callbacks = []

    def before_enqueue(operation):
        cache.callbacks.append(operation.beliefkv_command_id)
        return True

    def load(host_indices, *, node_id, extra_pools, beliefkv_command_id, **kwargs):
        operation = NS(
            host_indices=host_indices, node_id=node_id, extra_pools=extra_pools or (),
            beliefkv_command_id=beliefkv_command_id,
        )
        if not kwargs["beliefkv_before_enqueue"](operation):
            return None
        controller.load_queue.append(operation)
        return nodes[node_id].component_data[0].host_value

    def commit(node_id, device_indices, kv, extra):
        node = nodes[node_id]
        if len(device_indices):
            node.component_data[0].value = device_indices
        for ct, transfers in extra.items():
            node.component_data[ct].value = transfers[0].host_indices
        node.load_back_pending_id = node_id
        return []

    controller.load, controller.start_loading = load, lambda: 1
    tree.commit_load_back = commit
    entries = tuple(
        (node.id, node.creation_time, case["mamba"] and node is leaf, f"command-{node.id}")
        for node in targets
    )
    return NS(cache=cache, targets=targets, leaf=leaf, entries=entries, callback=before_enqueue)


def run_once(state, case):
    cache = state.cache
    for node in state.targets:
        node.component_data[0].value = None
        node.component_data[2].value = None
        node.load_back_pending_id = None
    cache.ongoing_load_back.clear()
    cache.cache_controller.load_queue.clear()
    cache.callbacks.clear()
    cache.spec_builds = 0
    for kind in cache.lock_counts:
        cache.lock_counts[kind] = 0
    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
    outcomes = cache.prefetch_gpu_session_nodes(
        session_id="fixture", session_generation=1,
        leaf_node_id=state.leaf.id, leaf_creation_time=state.leaf.creation_time,
        nodes=state.entries, beliefkv_before_enqueue=state.callback,
    )
    costs = {
        "wall_us": (time.perf_counter_ns() - wall) / 1000.,
        "thread_cpu_us": (time.thread_time_ns() - cpu) / 1000.,
    }
    assert len(outcomes) == case["burst"]
    signature = (
        tuple(outcomes), tuple(cache.callbacks), dict(cache.lock_counts),
        tuple((op.node_id, op.host_indices,
               tuple((item.name, item.host_indices) for item in op.extra_pools))
              for op in cache.cache_controller.load_queue),
    )
    return costs, signature, cache.spec_builds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-engine-root", type=Path, required=True)
    parser.add_argument("--optimized-engine-root", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples <= 0 or args.iterations <= 0:
        parser.error("samples and iterations must be positive")
    implementations = {
        name: variant(path) for name, path in (
            ("baseline", args.baseline_engine_root), ("optimized", args.optimized_engine_root),
        )
    }
    cases = [
        dict(depth=depth, burst=burst, tokens=tokens, mamba=mamba)
        for depth in (8, 64, 256) for burst in (1, min(16, depth))
        for tokens in (128, 4096) for mamba in (False, True)
    ]
    results = []
    for case in cases:
        states = {name: fixture(implementation, case)
                  for name, implementation in implementations.items()}
        readings = {name: dict(wall_us=[], thread_cpu_us=[]) for name in states}
        for sample in range(args.samples + 2):
            sample_costs = {name: dict(wall_us=[], thread_cpu_us=[]) for name in states}
            for iteration in range(args.iterations):
                order = list(states)
                if (sample + iteration) % 2:
                    order.reverse()
                signatures = {}
                for name in order:
                    costs, signatures[name], builds = run_once(states[name], case)
                    assert builds == case["burst"] * (2 if name == "baseline" else 1)
                    for metric, value in costs.items():
                        sample_costs[name][metric].append(value)
                left, right = signatures["baseline"], signatures["optimized"]
                assert left[:3] == right[:3], case
                assert len(left[3]) == len(right[3]), case
                for first, second in zip(left[3], right[3]):
                    assert first[0] == second[0] and torch.equal(first[1], second[1]), case
                    assert len(first[2]) == len(second[2]), case
                    for a, b in zip(first[2], second[2]):
                        assert a[0] == b[0] and torch.equal(a[1], b[1]), case
            if sample >= 2:
                for name in states:
                    for metric, values in sample_costs[name].items():
                        readings[name][metric].append(mean(values))
        measurements = {
            name: {metric: dict(mean=mean(values), p50=median(values), samples=values)
                   for metric, values in metrics.items()}
            for name, metrics in readings.items()
        }
        results.append(dict(
            **case, measurements=measurements, outcomes_transfers_callbacks_locks_equal=True,
            spec_builds_per_burst=dict(baseline=case["burst"] * 2, optimized=case["burst"]),
        ))
        before = measurements["baseline"]["thread_cpu_us"]["p50"]
        after = measurements["optimized"]["thread_cpu_us"]["p50"]
        print(f"{case}: {before:.3f} -> {after:.3f} CPU us ({100 * (1 - after / before):.1f}% lower)",
              flush=True)
    report = dict(
        schema_version=1, source_sha256={
            name: implementation.source_sha256 for name, implementation in implementations.items()
        }, samples=args.samples, iterations_per_sample=args.iterations, cases=results,
        scope=(
            "Whole native prefetch burst, load_back and exact FULL/Mamba/tree transfer builders. "
            "Native allocator, lock accounting, components' per-request preparation, controller "
            "enqueue and commit are deterministic CPU stubs. Transfer indices, outcomes, "
            "callbacks and lock counts must match. No DMA, GPU or throughput benefit is measured."
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
