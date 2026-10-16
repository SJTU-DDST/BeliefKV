#!/usr/bin/env python3
"""Compare complete PREPARE dispatch including transaction setup on CPU fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import distribution


ROOT = Path(__file__).resolve().parents[1]


def setup(cls, *, depth, backed, control, decline):
    runtime, queue, reads, _, _ = fixture(
        cls, workflows=108, depth=depth, backed=backed,
        host_full=1_000_000, leases=0,
    )
    cache = runtime._native_cache
    controller = cache.cache_controller
    controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    controller._transfer_num_bytes = lambda op: 20_480 * len(op.device_indices)
    actions = []

    def enqueue(**kwargs):
        node_id, command = kwargs["node_id"], kwargs["beliefkv_command_id"]
        if decline:
            return NS(issued=False)
        op = NS(
            beliefkv_command_id=command, node_ids=[node_id],
            device_indices=range(128), host_indices=range(128),
        )
        if not kwargs["beliefkv_before_enqueue"](op):
            return NS(issued=False)
        actions.append((kwargs["session_id"], node_id, False))
        return NS(issued=True, node_id=node_id)

    def burst(**kwargs):
        outcomes = []
        for node_id, created, command in kwargs.pop("nodes"):
            outcome = enqueue(
                **kwargs, node_id=node_id, node_creation_time=created,
                beliefkv_command_id=command,
            )
            if not outcome.issued:
                break
            outcomes.append(outcome)
        return tuple(outcomes)

    cache.prepare_host_shadow = enqueue
    cache.prepare_host_session_nodes = burst
    runtime.event_server = NS(
        drain=lambda **kwargs: (object(),) if control else (),
        close=lambda: None,
    )
    runtime._hotpath_timing.enabled = False
    return runtime, queue, reads, actions


def run_case(baseline, *, depth, backed, control, decline, samples):
    variants = {
        name: setup(cls, depth=depth, backed=backed, control=control, decline=decline)
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    costs = {name: [] for name in variants}
    native_reads = {name: Counter() for name in variants}
    try:
        with patch("time.monotonic", return_value=1000.):
            for iteration in range(samples + 5):
                selections, publications = {}, {}
                order = ("baseline", "optimized") if iteration % 2 else ("optimized", "baseline")
                for name in order:
                    runtime, queue, reads, actions = variants[name]
                    runtime.physical_ledger = type(runtime.physical_ledger)()
                    runtime._join_prepare_next_ms = 0.
                    runtime._join_prepare_cursor = 0
                    runtime._join_prepare_commands.clear()
                    runtime._prepare_probe_after_ms.clear()
                    actions.clear()
                    before = reads.copy()
                    start = time.thread_time_ns()
                    runtime.dispatch_join_prepare(queue, running_batch=NS(reqs=[None] * 48))
                    elapsed_ms = (time.thread_time_ns() - start) / 1e6
                    selections[name] = tuple(actions)
                    publications[name] = runtime._native_cache.beliefkv_join_pressure_candidates
                    if iteration >= 5:
                        costs[name].append(elapsed_ms)
                        native_reads[name].update(reads - before)
                if selections["baseline"] != selections["optimized"]:
                    raise AssertionError("native selected extents changed")
                if publications["baseline"] != publications["optimized"]:
                    raise AssertionError("published reclaim candidates changed")
                if not backed and not decline and not selections["optimized"]:
                    raise AssertionError("actionable fixture failed to submit")
        return {
            "depth": depth, "backed": backed, "control_delivered": control,
            "native_decline": decline, "samples": samples,
            "selection_and_publication_equal": True,
            "thread_cpu_ms": {name: distribution(values) for name, values in costs.items()},
            "native_reads": {name: dict(counts) for name, counts in native_reads.items()},
            "mean_cpu_reduction_ms": (
                sum(costs["baseline"]) - sum(costs["optimized"])
            ) / samples,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists() or args.samples <= 0:
        parser.error("use a fresh output and positive sample count")
    baseline, baseline_sources = baseline_runtime(args.baseline)
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_native_shared_path.py",
        "scripts/benchmark_prepare_selection_reuse_cpu.py",
    )
    digests = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
    }
    cases = [
        run_case(baseline, depth=depth, backed=backed, control=control,
                 decline=decline, samples=args.samples)
        for depth in (16, 64, 256)
        for backed, control, decline in (
            (False, False, False), (False, True, False),
            (False, False, True), (True, False, False),
        )
    ]
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in digests.items()):
        raise ValueError("CPU benchmark sources changed")
    report = {
        "status": "complete_prepare_dispatch_cpu_fixture_not_gpu_or_jct_gain",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "working_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "source_sha256": digests, "cases": cases,
        "limitations": [
            "Complete dispatch, actual closure observation, selection, publication and ledger setup.",
            "Synthetic 108-workflow cache ancestry; native enqueue/controller are CPU stand-ins.",
            "No allocator/DMA locks, real transfer, engine execution, GPU or JCT benefit is measured.",
            "Probes/cursors/ledger are reset between iterations only to measure comparable cases.",
            "Declined attempts force fresh observation on later candidates.",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([{
        key: case[key] for key in (
            "depth", "backed", "control_delivered", "native_decline",
            "mean_cpu_reduction_ms", "native_reads",
        )
    } for case in cases], indent=2))


if __name__ == "__main__":
    main()
