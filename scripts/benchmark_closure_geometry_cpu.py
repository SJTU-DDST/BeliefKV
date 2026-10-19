#!/usr/bin/env python3
"""Compare complete live node observations on static and shared CPU pool fixtures."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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

from beliefkv.runtime import sglang_v0520_observer as observer
from scripts.benchmark_native_prepare_path import baseline_runtime
from tests.test_sglang_v0520_observer import UnifiedTreeCore, _cache, _node, _static_cache

SOURCE = "beliefkv/runtime/sglang_v0520_observer.py"


def baseline_observer(revision):
    _, digests = baseline_runtime(revision)
    module = sys.modules["beliefkv.runtime._prepare_baseline_observer"]
    return module.observe_unified_node_closure, digests


def fixture(layout, depth, storage):
    cache = _cache() if layout == "shared" else _static_cache()
    if layout == "paged_static":
        cache.token_to_kv_pool_allocator = type(
            "PagedTokenToKVPoolAllocator", (NS,), {},
        )(**vars(cache.token_to_kv_pool_allocator))
    if storage == "tensor":
        import torch
        indices = torch.arange(8, dtype=torch.int64)
    else:
        indices = range(8)
    nodes = {0: _node(0)}
    nodes[0].key = ()
    for node_id in range(1, depth + 1):
        nodes[node_id] = _node(
            node_id, nodes[node_id - 1],
            device=(1,) if node_id % 2 else (),
            host=(1,), mamba_host=True,
        )
        nodes[node_id].component_data[0].value = indices if node_id % 2 else None
        nodes[node_id].component_data[0].host_value = indices
        nodes[node_id].key = range(8)
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    return cache, nodes


def measure(methods, *, layout, depth, storage, samples):
    cache, nodes = fixture(layout, depth, storage)
    costs = {name: {"wall_us": [], "thread_cpu_us": []} for name in methods}
    calls = max(1, min(20, 256 // depth))
    canonical = None
    with patch.object(observer.time, "monotonic", lambda: 1000.):
        for sample in range(samples + 3):
            # Change residency and transfer state between calls, rather than
            # measuring an observation that could be reused across iterations.
            node = nodes[depth]
            node.component_data[0].value = range(2) if sample % 2 else None
            node.component_data[0].host_lock_ref = sample % 4
            node.component_data[2].session_ref = sample % 3
            node.component_data[2].session_ids = {"leaf"} if sample % 2 else None
            node.load_back_pending_id = 7 if sample % 3 == 0 else None
            expected = asdict(methods["baseline"](cache, depth))
            if not expected["observable"]:
                raise AssertionError((layout, depth, storage, expected))
            order = list(methods)
            if sample % 2:
                order.reverse()
            for name in order:
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                for _ in range(calls):
                    observed = methods[name](cache, depth)
                elapsed_wall = (time.perf_counter_ns() - wall) / 1000. / calls
                elapsed_cpu = (time.thread_time_ns() - cpu) / 1000. / calls
                if asdict(observed) != expected:
                    raise AssertionError((layout, depth, storage, sample, name))
                if sample >= 3:
                    costs[name]["wall_us"].append(elapsed_wall)
                    costs[name]["thread_cpu_us"].append(elapsed_cpu)
            canonical = expected
    summaries = {
        name: {clock: {"mean": mean(values), "p50": median(values), "samples": values}
               for clock, values in clocks.items()}
        for name, clocks in costs.items()
    }
    return {
        "layout": layout, "depth": depth, "index_storage": storage,
        "calls_per_sample": calls, "outputs_equal": True, "costs": summaries,
        "canonical_output_sha256": hashlib.sha256(
            json.dumps(canonical, sort_keys=True).encode()
        ).hexdigest(),
        "thread_cpu_reduction_fraction": (
            1. - summaries["optimized"]["thread_cpu_us"]["mean"]
            / summaries["baseline"]["thread_cpu_us"]["mean"]
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--samples", type=int, default=31)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        raise ValueError("positive samples and a new output path required")
    baseline, digests = baseline_observer(args.baseline_revision)
    methods = {"baseline": baseline, "optimized": observer.observe_unified_node_closure}
    started = time.time()
    cases = [
        measure(methods, layout=layout, depth=depth, storage=storage, samples=args.samples)
        for layout in ("static", "paged_static", "shared")
        for depth in (1, 4, 16, 64, 256)
        for storage in ("range", "tensor")
    ]
    report = {
        "scope": (
            "CPU complete ancestry observations with real CPU int64 index tensors "
            "and pool stand-ins; no GPU, DMA, live frequency or throughput claim."
        ),
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests[SOURCE],
        "baseline_source_sha256s": digests,
        "baseline_observer_helpers_and_types_frozen": True,
        "optimized_source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_ts_ms": started * 1000., "finished_ts_ms": time.time() * 1000.,
        "samples": args.samples, "outputs_equal": True, "cases": cases,
        "semantics": [
            "Every call validates the live layout, ancestry, residency and pending state.",
            "Both variants observe the same changing fixture; all scalar outputs match.",
            "Alternating measurement order; warmup samples and serialization are excluded.",
            "Raw wall and thread CPU samples retain small gains and regressions.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "layout", "depth", "index_storage", "thread_cpu_reduction_fraction",
        )} for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
