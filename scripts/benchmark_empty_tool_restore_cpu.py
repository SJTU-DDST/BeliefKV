#!/usr/bin/env python3
"""Compare tool scans with resident, shared and independent CPU cache paths."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def empty_tool_fixture(cls, *, contexts, depth, sharing=1, diagnostics=False):
    values = dispatch_fixture(
        cls, source="tool_wait", depth=depth, diagnostics=diagnostics,
        native_budget=True, workflows=contexts, running=8,
    )
    runtime, key, cache, _, _, _, _, _, _ = values
    prototype = runtime.tool_wait_hints[key.context_id]
    for index in range(contexts):
        current = runtime.context_sessions[f"ctx-wf-{index}-root"]
        invocation = runtime.graph.invocations[current.invocation_id]
        invocation.state = InvocationState.WAIT_TOOL
        invocation.active_tool_calls = {"long": ("shell", invocation.updated_ts_ms)}
        runtime.tool_wait_hints[current.context_id] = replace(
            prototype, key=current, invocation_revision_ts_ms=invocation.updated_ts_ms,
        )
    for node_id in range(contexts * (depth + 1)):
        node = cache.tree_core.node_by_id(node_id)
        for data in node.component_data.values():
            if data.host_value is not None:
                data.value = data.host_value
    for first in range(0, contexts, sharing):
        owners = {
            runtime.context_sessions[f"ctx-wf-{index}-root"].session_id
            for index in range(first, min(first + sharing, contexts))
        }
        common = cache.tree_core.node_by_id(first * (depth + 1) + depth - 1)
        for index in range(first, min(first + sharing, contexts)):
            leaf = cache.tree_core.node_by_id(index * (depth + 1) + depth)
            leaf.parent = common
        node = common
        while node is not None:
            node.component_data[0].session_ids = owners
            node.component_data[0].session_ref = len(owners)
            node = node.parent
    return values


def measure(baseline, *, contexts, depth, sharing, diagnostics, samples):
    variants = {
        name: empty_tool_fixture(
            cls, contexts=contexts, depth=depth, sharing=sharing, diagnostics=diagnostics,
        )
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    costs = {name: [] for name in variants}
    reads_by_arm = {}
    try:
        for iteration in range(samples + 10):
            states = {}
            for name in list(variants)[::1 if iteration % 2 else -1]:
                runtime, _, _, reads, actions, rows, reset, dispatch, now = variants[name]
                reset()
                with patch("time.monotonic", lambda: now), patch("time.time", lambda: 1700000000.):
                    started = time.thread_time_ns()
                    dispatch()
                    elapsed = (time.thread_time_ns() - started) / 1000.
                assert not actions and runtime.physical_ledger.pending_count == 0
                assert runtime.counts["tool_prefetch_no_restore_target"] == contexts
                states[name] = {
                    "counts": dict(runtime.counts), "rows": rows,
                    "budget": asdict(runtime._residency_budget),
                    "ticket": runtime._tool_ticket, "physical_disabled": runtime.physical_disabled,
                }
                if iteration >= 10:
                    costs[name].append(elapsed)
                reads_by_arm[name] = dict(reads)
            if states["baseline"] != states["candidate"]:
                raise AssertionError("empty-target scan changed decisions or observations")
        return {
            "contexts": contexts, "depth": depth, "sharing_group": sharing,
            "diagnostic_writer_fixture": diagnostics, "samples": samples,
            "decisions_records_budget_and_physical_state_equal": True,
            "native_reads_per_scan": reads_by_arm,
            "costs_thread_cpu_us": {name: distribution(values) for name, values in costs.items()},
            "raw_thread_cpu_us": costs,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=61)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a fresh output are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        "scripts/benchmark_empty_tool_restore_cpu.py",
    )
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    cases = [
        measure(
            baseline, contexts=contexts, depth=depth, sharing=sharing,
            diagnostics=diagnostics, samples=args.samples,
        )
        for contexts in (1, 8, 36) for depth in (64, 200)
        for sharing in ((1,) if contexts == 1 else (1, 4))
        for diagnostics in (False, True)
    ]
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value
           for name, value in sources.items()):
        raise RuntimeError("measured sources changed")
    result = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_sources": baseline_sources,
        "candidate_sources": sources, "cases": cases, "performance_accepted": False,
        "limits": [
            "Synthetic simultaneously eligible tool waits; cold opportunity caches in every sample.",
            "Shared groups have separate session identities and private leaves; prefixes share actual node objects.",
            "Four-way sharing models the workload's project grouping, not measured live tool-wait frequencies.",
            "Independent paths and one-context cases retain negative or neutral results.",
            "Native allocator, DMA, writer I/O, background contention and workflow JCT are not measured.",
            "Construction, reset, verification and clock patch setup are outside thread CPU timings.",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([{
        **{key: case[key] for key in ("contexts", "depth", "sharing_group", "diagnostic_writer_fixture")},
        "baseline_p50_us": case["costs_thread_cpu_us"]["baseline"]["p50_us"],
        "candidate_p50_us": case["costs_thread_cpu_us"]["candidate"]["p50_us"],
        "reads": case["native_reads_per_scan"],
    } for case in cases], indent=2), flush=True)


if __name__ == "__main__":
    main()
