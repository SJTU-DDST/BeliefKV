#!/usr/bin/env python3
"""Measure native FULL-only prefix queueing on CPU, including real receipt merging."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import torch


def load_fixture(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def distribution(values):
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * .9))],
        "max": ordered[-1],
    }


def measure(shadow, receipts, *, extents: int, burst: bool, width: int):
    cache, root, parent, leaf, group, _, _ = shadow.fixture()
    nodes = cache.tree_core.node_by_id.side_effect.__self__
    nodes.clear()
    nodes[root.id] = root
    previous = root
    for nid in range(1, extents + 1):
        current = shadow.node(nid, previous, device=torch.arange(width))
        current.component_data[shadow.ComponentType.MAMBA].value = torch.arange(1)
        nodes[nid] = current
        previous = current
    previous.component_data[shadow.ComponentType.FULL].session_ids.add("s")
    group.free_sizes[shadow.PoolName.KV] = extents * width
    group.resolve_host_transfers = lambda transfers, **_: transfers
    controller = receipts.hybrid_controller()
    controller.mem_pool_host = group
    controller.write_policy = "write_back"
    controller.l2_transfer_engine.submit_device_to_host.return_value = NS(
        start_event=object(), finish_event=NS(synchronize=Mock()),
        timing_enabled=False,
    )
    cache.cache_controller = controller
    cache.tree_core.node_by_id.side_effect = nodes.__getitem__
    cache.tree_core.is_write_back = True
    cache.components[shadow.ComponentType.FULL]._session_leaves["s"] = {previous}

    def finish(ids, ack_id):
        for nid in ids:
            assert nodes[nid].write_through_pending_id == ack_id
            nodes[nid].write_through_pending_id = None

    cache.tree_core.finish_write_through.side_effect = finish
    commits = []
    cache.on_hicache_transfer_commit = commits.append
    before_wall, before_cpu = time.perf_counter_ns(), time.thread_time_ns()
    opportunities = 0
    size = 8 if burst else 1
    for start in range(1, extents + 1, size):
        selected = tuple(
            (nid, nodes[nid].creation_time, f"prepare-{nid}")
            for nid in range(start, min(extents + 1, start + size))
        )
        common = dict(
            session_id="s", session_generation=7,
            leaf_node_id=previous.id, leaf_creation_time=previous.creation_time,
            beliefkv_before_enqueue=lambda _: True,
        )
        if burst and len(selected) > 1:
            outcomes = cache.prepare_host_session_nodes(**common, nodes=selected)
        else:
            nid, created, command = selected[0]
            outcomes = (cache.prepare_host_shadow(
                **common, node_id=nid, node_creation_time=created,
                beliefkv_command_id=command, beliefkv_include_mamba=False,
            ),)
        assert len(outcomes) == len(selected) and all(item.issued for item in outcomes)
        cache.writing_check(finish_count=len(controller.ack_write_queue))
        opportunities += 1
    wall_ms = (time.perf_counter_ns() - before_wall) / 1e6
    cpu_ms = (time.thread_time_ns() - before_cpu) / 1e6
    child_receipts = [child for event in commits for child in event.child_commits]
    assert len(child_receipts) == extents
    assert all(nodes[nid].component_data[shadow.ComponentType.MAMBA].host_value is None
               for nid in range(1, extents + 1))
    assert all(nodes[nid].component_data[shadow.ComponentType.FULL].value is not None
               for nid in range(1, extents + 1))
    assert cache.ongoing_write_through == {}
    cache.evict_host.assert_not_called()
    signature = sorted(
        (child.command_id, child.num_bytes, tuple(child.num_tokens_by_pool))
        for child in child_receipts
    )
    evidence = {
        "controller_submissions": controller.l2_transfer_engine.submit_device_to_host.call_count,
        "ack_groups": len(commits),
        "caller_opportunities": opportunities,
        "full_bytes": extents * width * group.anchor_entry.host_pool.size_per_token,
        "mamba_allocations": sum(pool == shadow.PoolName.MAMBA for pool, _ in group.allocations),
    }
    return wall_ms, cpu_ms, signature, evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=120)
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    engine = args.engine_root.resolve()
    sys.path.insert(0, str(engine / "python"))
    shadow = load_fixture(engine / "test/srt/test_beliefkv_shadow_dispatch.py", "_prepare_shadow")
    receipts = load_fixture(engine / "test/srt/test_beliefkv_transfer_receipts.py", "_prepare_receipts")
    results = []
    for extents in (1, 4, 8, 16):
        samples = {name: {"wall_ms": [], "thread_cpu_ms": []}
                   for name in ("single_extent", "prefix_burst")}
        evidence = {}
        for iteration in range(args.iterations + 5):
            variants = [("single_extent", False), ("prefix_burst", True)]
            if iteration % 2:
                variants.reverse()
            signatures = []
            for name, burst in variants:
                wall, cpu, signature, observed = measure(
                    shadow, receipts, extents=extents, burst=burst, width=128,
                )
                signatures.append(signature)
                evidence.setdefault(name, observed)
                assert evidence[name] == observed
                if iteration >= 5:
                    samples[name]["wall_ms"].append(wall)
                    samples[name]["thread_cpu_ms"].append(cpu)
            assert signatures[0] == signatures[1]
        results.append({
            "missing_full_extents": extents,
            "full_tokens_per_extent": 128,
            "per_node_receipts_equal": True,
            "measurements": {
                name: {metric: distribution(values) for metric, values in observations.items()}
                for name, observations in samples.items()
            },
            "native_evidence": evidence,
        })
    source_paths = (
        "python/sglang/srt/mem_cache/unified_radix_cache.py",
        "python/sglang/srt/mem_cache/hybrid_cache/hybrid_cache_controller.py",
        "python/sglang/srt/managers/cache_controller.py",
    )
    print(json.dumps({
        "schema_version": 1,
        "engine_root": str(engine),
        "iterations_per_case": args.iterations,
        "warmup_iterations": 5,
        "source_sha256": {
            path: hashlib.sha256((engine / path).read_bytes()).hexdigest()
            for path in source_paths
        },
        "cases": results,
        "scope": (
            "CPU native queue/commit/ACK methods with real controller write and "
            "receipt merging. CPU tensors and fake transfer completion exclude "
            "CUDA, DMA, scheduler polling and workload throughput. Earlier "
            "single-extent method is unchanged and invoked with FULL only. "
            "The singleton burst uses the same direct primitive as runtime. "
            "Caller opportunities are counted calls, not observed wall-clock "
            "polling delays. Each fixture starts without Host copies."
        ),
        "deployed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
