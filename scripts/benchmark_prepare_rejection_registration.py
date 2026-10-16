#!/usr/bin/env python3
"""Measure PREPARE rejection overhead with no reclaimable backed leaf."""

from __future__ import annotations

import argparse
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
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from tests.test_sglang_v0520_observer import UnifiedTreeNode as FixtureNode


class UnifiedTreeNode(FixtureNode):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


def indexed_fixture(runtime_class, *, workflows, depth):
    result = fixture(
        runtime_class, workflows=workflows, depth=depth,
        backed=False, host_full=0, leases=0,
    )
    cache = result[0]._native_cache
    nodes = [cache.tree_core.node_by_id(index) for index in range(workflows * depth)]
    for node in nodes:
        node.__class__ = UnifiedTreeNode
        node.backuped = (
            node.component_data[0].value is not None
            and node.component_data[0].host_value is not None
        )
    leaves = {nodes[index] for index in range(depth - 1, len(nodes), depth)}
    cache.tree_core.evictable_device_leaves = leaves
    cache.tree_core.lru_lists = {2: NS(cache={node.id: node for node in leaves})}
    return result


def measure(baseline, *, workflows, depth, iterations, force_probes):
    variants = {
        name: indexed_fixture(cls, workflows=workflows, depth=depth)
        for name, cls in (("baseline", baseline), ("development", NativeAdmissionRuntime))
    }
    samples = {name: [] for name in variants}
    start = time.monotonic()
    try:
        for index in range(iterations):
            order = tuple(variants) if index % 2 else tuple(reversed(variants))
            for name in order:
                runtime, queue, _, actions, _ = variants[name]
                if force_probes:
                    runtime._join_prepare_next_ms = 0.
                    runtime._prepare_probe_after_ms.clear()
                with patch("time.monotonic", return_value=start + index * .020):
                    before = time.thread_time_ns()
                    runtime.dispatch_join_prepare(queue)
                    elapsed = (time.thread_time_ns() - before) / 1e6
                if (
                    actions or runtime._parent_pressure_candidates
                    or runtime.physical_ledger.pending_count
                    or any(runtime._native_cache.beliefkv_join_pressure_candidates_by_component.values())
                ):
                    raise AssertionError("unbacked leaf fixture gained an action or cold candidate")
                if index >= iterations // 4:
                    samples[name].append(elapsed)
        evidence = {}
        for name, (runtime, _, reads, _, _) in variants.items():
            values = samples[name]
            evidence[name] = {
                "thread_cpu_mean_ms_per_iteration": mean(values),
                "thread_cpu_p50_ms_per_iteration": median(values),
                "measured_iterations": len(values),
                "native_reads_including_warmup": dict(reads),
                "budget_rejections": runtime.counts["prepare_prefix_budget_rejected_early"],
                "registered_nodes": len(runtime._parent_pressure_candidates),
            }
        if evidence["baseline"]["budget_rejections"] != evidence["development"]["budget_rejections"]:
            raise AssertionError("changed PREPARE cadence or rejection coverage")
        return {
            "iterations": iterations, "step_ms": 20, "force_probes": force_probes,
            "workflows": workflows, "depth": depth, "native_leaf_set": True,
            "native_mamba_index": True, "cold_candidates_and_actions_equal": True,
            "measurements": evidence,
            "thread_cpu_mean_delta_ms_per_iteration": (
                evidence["development"]["thread_cpu_mean_ms_per_iteration"]
                - evidence["baseline"]["thread_cpu_mean_ms_per_iteration"]
            ),
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="2e37ed2")
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--depth", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 8 or args.workflows < 1 or args.depth < 4:
        parser.error("require at least 8 iterations, 1 workflow and depth 4")
    if args.output.exists():
        raise FileExistsError(args.output)
    baseline, sources = baseline_runtime(args.baseline_revision)
    report = {
        "scope": (
            "CPU-only complete JOIN PREPARE checks with zero Host headroom and unbacked "
            "current leaves. Backed internal prefixes must not grow the candidate registry. "
            "Baseline source execution preserves timing decorators. Includes live-cadence "
            "and forced-probe stress controls; no socket, DMA, neural inference, GPU or JCT."
        ),
        "baseline_revision": args.baseline_revision, "baseline_source_sha256": sources,
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "beliefkv/runtime/sglang_v0520_physical.py",
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "scripts/benchmark_native_prepare_path.py",
                "scripts/benchmark_prepare_rejection_registration.py",
            )
        },
        "cases": [
            measure(
                baseline, workflows=args.workflows, depth=args.depth,
                iterations=args.iterations, force_probes=forced,
            ) for forced in (False, True)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
