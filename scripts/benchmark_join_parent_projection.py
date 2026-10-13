#!/usr/bin/env python3
"""Compare JOIN identity projection and complete PREPARE on CPU fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import distribution
from tests.test_sglang_v0520_observer import UnifiedTreeNode


def indexed_fixture(cls, *, workflows, depth, backed):
    runtime, queue, reads, actions, observations = fixture(
        cls, workflows=workflows, depth=depth, backed=backed,
        host_full=1_000_000, leases=0,
    )
    runtime._hotpath_timing.enabled = False
    tree = runtime._native_cache.tree_core
    nodes = [tree.node_by_id(node_id) for node_id in range(workflows * depth)]
    for node in nodes:
        node.backuped = node.component_data[0].host_value is not None
    tree.evictable_device_leaves = {
        node for node in nodes if node.id % depth == depth - 1
    }
    tree.lru_lists = {2: NS(cache={
        node.id: node for node in nodes if node.component_data[2].value is not None
    })}
    return runtime, queue, reads, actions, observations


def case(baseline, *, workflows, depth, backed, reset_projection, iterations):
    variants = {
        name: indexed_fixture(cls, workflows=workflows, depth=depth, backed=backed)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    samples = {name: empty_samples() for name in variants}
    reads_total = {name: Counter() for name in variants}
    join_ids = tuple(f"wf-{index}-join-15" for index in range(workflows))
    try:
        for iteration in range(iterations + 8):
            names = ("baseline", "candidate") if iteration % 2 else ("candidate", "baseline")
            evidence = {}
            for name in names:
                runtime, queue, reads, actions, observations = variants[name]
                if reset_projection:
                    runtime._join_parent_cache = None
                start_wall, start_cpu = time.perf_counter_ns(), time.thread_time_ns()
                projected = tuple(runtime._join_parent_key(join_id) for join_id in join_ids)
                cpu_us = (time.thread_time_ns() - start_cpu) / 1000
                wall_us = (time.perf_counter_ns() - start_wall) / 1000
                if iteration >= 8:
                    samples[name]["projection_wall_ms"].append(wall_us / 1000)
                    samples[name]["projection_thread_cpu_ms"].append(cpu_us / 1000)
                if reset_projection:
                    runtime._join_parent_cache = None
                runtime._join_prepare_next_ms = 0.
                runtime._prepare_probe_after_ms.clear()
                actions.clear()
                observations.clear()
                before = reads.copy()
                start_wall, start_cpu = time.perf_counter_ns(), time.thread_time_ns()
                runtime.dispatch_join_prepare(queue, running_batch=NS(reqs=[None] * 48))
                cpu_us = (time.thread_time_ns() - start_cpu) / 1000
                wall_us = (time.perf_counter_ns() - start_wall) / 1000
                if iteration >= 8:
                    samples[name]["prepare_wall_ms"].append(wall_us / 1000)
                    samples[name]["prepare_thread_cpu_ms"].append(cpu_us / 1000)
                    reads_total[name].update(reads - before)
                evidence[name] = (
                    projected, tuple(actions),
                    tuple(runtime._parent_pressure_candidates.items()),
                    runtime._native_cache.beliefkv_join_pressure_candidates,
                    runtime._native_cache.beliefkv_join_pressure_candidates_by_component,
                    tuple(
                        {key: value for key, value in row.items()
                         if key not in ("ts_ms", "monotonic_ms")}
                        for row in observations
                    ),
                )
            if evidence["baseline"] != evidence["candidate"]:
                raise AssertionError(f"projection, selection or native publication changed at {iteration}")
        if reads_total["baseline"] != reads_total["candidate"]:
            raise AssertionError("physical observation counts changed")
        costs = {
            name: {key: distribution(values) for key, values in phases.items()}
            for name, phases in samples.items()
        }
        return {
            "workflows": workflows, "depth": depth, "backed": backed,
            "reset_projection_before_each_phase": reset_projection,
            "native_leaf_and_lru_indices_present": True,
            "force_existing_probe_timers_due": True, "iterations": iterations,
            "projection_selection_publication_and_records_equal": True,
            "native_reads_equal": True,
            "native_reads": {name: dict(counts) for name, counts in reads_total.items()},
            "costs": costs,
            "mean_reduction_fraction": {
                key: 1 - costs["candidate"][key]["mean_ms"] / costs["baseline"][key]["mean_ms"]
                for key in samples["baseline"]
            },
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def empty_samples():
    return {
        "projection_wall_ms": [], "projection_thread_cpu_ms": [],
        "prepare_wall_ms": [], "prepare_thread_cpu_ms": [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()
    if args.output.exists() or args.iterations < 20:
        raise ValueError("preserve prior evidence and use at least20 iterations")
    baseline, baseline_hashes = baseline_runtime(args.baseline)
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1000.), \
         patch.object(UnifiedTreeNode, "__hash__", new=object.__hash__):
        cases = [
            case(
                baseline, workflows=108, depth=depth, backed=backed,
                reset_projection=reset, iterations=args.iterations,
            )
            for depth, backed in ((16, True), (64, True), (16, False))
            for reset in (False, True)
        ]
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/control/causal_graph.py",
        "scripts/benchmark_join_parent_projection.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_native_shared_path.py",
        "tests/test_native_wait_candidates.py",
        "tests/test_sglang_v0520_observer.py",
    )
    report = {
        "status": "same_input_cpu_fixture_not_gpu_or_jct_evidence",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline,
        "candidate_base_revision": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
        ).strip(),
        "baseline_source_sha256": baseline_hashes,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths
        },
        "cases": cases,
        "scope": [
            "Complete PREPARE CPU method with native-shaped leaf and Mamba LRU indices; enqueue remains stubbed.",
            "Alternate variant order; verify JOIN projection, all pressure publications, actions and observation records.",
            "Reset cases discard projection between phases to expose rebuilding cost; no event workload is replayed.",
            "Existing cadence/probe timers are forced due only in these fixtures; runtime timers remain unchanged.",
            "Native physical observations and output identities remain equal; no stale physical snapshot cache is added.",
            "Real graph-change frequency, tree geometry, CUDA cost, queue consumption and throughput/JCT are unknown.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        key: row[key] for key in (
            "depth", "backed", "reset_projection_before_each_phase", "mean_reduction_fraction",
        )
    } for row in cases], indent=2), flush=True)


if __name__ == "__main__":
    main()
