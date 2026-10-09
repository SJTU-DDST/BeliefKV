#!/usr/bin/env python3
"""Compare complete native reentry inspection on identical CPU radix fixtures."""

from __future__ import annotations

import argparse
import ast
from array import array
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import time
from types import SimpleNamespace as NS


def variant(engine_root):
    sources = {
        name: engine_root / "python/sglang/srt/mem_cache" / filename
        for name, filename in (
            ("key", "radix_cache.py"), ("cache", "unified_radix_cache.py"),
        )
    }
    trees = {name: ast.parse(path.read_text()) for name, path in sources.items()}
    key = next(
        node for node in trees["key"].body
        if isinstance(node, ast.ClassDef) and node.name == "RadixKey"
    )
    cache = next(
        node for node in trees["cache"].body
        if isinstance(node, ast.ClassDef) and node.name == "UnifiedRadixCache"
    )
    inspect = next(
        node for node in cache.body
        if isinstance(node, ast.FunctionDef) and node.name == "inspect_beliefkv_reentry"
    )
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ),
        key, inspect,
    ], type_ignores=[])
    namespace = {
        "array": array, "BASE_COMPONENT_TYPE": 0, "ComponentType": NS(MAMBA=2),
    }
    exec(compile(ast.fix_missing_locations(module), str(engine_root), "exec"), namespace)
    return NS(
        key=namespace["RadixKey"],
        inspect=namespace["inspect_beliefkv_reentry"],
        source_sha256={
            str(path.relative_to(engine_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources.values()
        },
    )


def fixture(implementation, case):
    tokens, depth, leaves = case["tokens"], case["depth"], case["leaves"]
    assert tokens % depth == 0
    segment_size = tokens // depth
    root = NS(id=0, creation_time=0., parent=None)
    nodes, common = {0: root}, root
    anchors = []
    raw = array("q", range(tokens + 1))

    def node(segment, parent, offset):
        node_id = len(nodes)
        on_device = offset < depth // 2
        full_value = range(len(segment))
        state = range(1) if (offset + 1) % 4 == 0 or offset == depth - 1 else None
        result = NS(
            id=node_id, creation_time=float(node_id), parent=parent,
            key=implementation.key(segment),
            component_data={
                0: NS(value=full_value if on_device else None, host_value=full_value),
                2: NS(value=state if on_device else None, host_value=state),
            },
            write_through_pending_id=None, load_back_pending_id=None,
        )
        nodes[node_id] = result
        return result

    for offset in range(depth - 1):
        start = offset * segment_size
        common = node(raw[start : start + segment_size], common, offset)
    for branch in range(leaves):
        segment = raw[(depth - 1) * segment_size : tokens]
        if branch:
            segment[0] += branch * 1_000_000
        leaf = node(segment, common, depth - 1)
        anchors.append((leaf.id, leaf.creation_time))
    request_ids = raw[:]
    if case.get("mismatch") is not None:
        request_ids[case["mismatch"]] = -1
    if case.get("short_request"):
        request_ids = request_ids[: tokens // 2 + 1]
    cache = NS(
        page_size=case["page_size"],
        tree_core=NS(root_node=root, is_eagle=False, node_by_id=nodes.__getitem__),
        session_refs=NS(
            session_id_for_req=lambda req: req.session_id,
            snapshot_latest_session_leaf_anchors=lambda *args, **kwargs: (
                (0, tuple(anchors)), (2, tuple(anchors)),
            ),
        ),
    )
    request = NS(origin_input_ids=request_ids, session_id="fixture", session_generation=1)
    return cache, request


def benchmark(baseline, optimized, *, samples, iterations):
    cases = []
    for tokens, depth in ((4096, 4), (32768, 8), (98304, 24)):
        for leaves in (1, 8):
            cases.append({
                "case": f"match-{tokens}-{leaves}-leaves",
                "tokens": tokens, "depth": depth, "leaves": leaves, "page_size": 1,
            })
    for label, extra in (
        ("first-divergence", {"mismatch": 0}),
        ("middle-divergence", {"mismatch": 16384}),
        ("last-divergence", {"mismatch": 32767}),
        ("short-request", {"short_request": True}),
        ("page-aligned", {"page_size": 16}),
    ):
        cases.append({
            "case": label, "tokens": 32768, "depth": 8,
            "leaves": 1, "page_size": 1, **extra,
        })
    results = []
    for case in cases:
        variants = {
            name: (implementation, *fixture(implementation, case))
            for name, implementation in (("baseline", baseline), ("optimized", optimized))
        }
        expected = baseline.inspect(*variants["baseline"][1:])
        for implementation, cache, request in variants.values():
            assert implementation.inspect(cache, request) == expected, case
            for _ in range(20):
                implementation.inspect(cache, request)
        timings = {name: [] for name in variants}
        for sample in range(samples):
            order = list(variants)
            if sample % 2:
                order.reverse()
            for name in order:
                implementation, cache, request = variants[name]
                start = time.perf_counter_ns()
                for _ in range(iterations):
                    result = implementation.inspect(cache, request)
                elapsed_us = (time.perf_counter_ns() - start) / 1000. / iterations
                assert result == expected, (case, name)
                timings[name].append(elapsed_us)
        costs = {
            name: {"p50_cpu_us": median(values), "mean_cpu_us": mean(values),
                   "sample_means_cpu_us": values}
            for name, values in timings.items()
        }
        results.append({
            **case, "outputs_equal": True, "costs": costs,
            "median_reduction_fraction": (
                1 - costs["optimized"]["p50_cpu_us"] / costs["baseline"]["p50_cpu_us"]
            ),
        })
    return {
        "scope": (
            "CPU-only whole inspect_beliefkv_reentry with actual RadixKey and "
            "synthetic shared radix ancestry; no DMA, native allocation or GPU throughput"
        ),
        "baseline_source_sha256": baseline.source_sha256,
        "optimized_source_sha256": optimized.source_sha256,
        "samples": samples, "iterations_per_sample": iterations,
        "outputs_equal": True, "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-engine-root", type=Path, required=True)
    parser.add_argument("--optimized-engine-root", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples <= 0 or args.iterations <= 0:
        parser.error("samples and iterations must be positive")
    report = benchmark(
        variant(args.baseline_engine_root), variant(args.optimized_engine_root),
        samples=args.samples, iterations=args.iterations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for result in report["results"]:
        baseline = result["costs"]["baseline"]["p50_cpu_us"]
        optimized = result["costs"]["optimized"]["p50_cpu_us"]
        reduction = result["median_reduction_fraction"] * 100.
        print(f"{result['case']}: {baseline:.3f} -> {optimized:.3f} us ({reduction:.1f}% lower)")


if __name__ == "__main__":
    main()
