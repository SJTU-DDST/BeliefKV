#!/usr/bin/env python3
"""Compare complete post-forward control cycles with fully backed waiters."""

from __future__ import annotations

import argparse
from collections import Counter
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
from scripts.benchmark_native_shared_path import distribution
from tests.test_prepare_publication_batch import publication_fixture
from tests.test_sglang_v0520_observer import UnifiedTreeCore


def fixture(runtime_class, *, contexts, depth, dirty):
    runtime, queue, cache, nodes, reads, actions, now = publication_fixture(
        runtime_class, contexts=contexts, depth=depth,
    )
    type(next(iter(nodes.values()))).__name__ = "UnifiedTreeNode"
    cache.tree_core = UnifiedTreeCore(**vars(cache.tree_core))
    cache.supports_beliefkv_resident_parent_prepare = lambda: True
    cache._beliefkv_unbacked_full = {987654321: object()} if dirty else {}
    runtime.enable_prepare_host = True
    runtime.enable_tool_prefetch = False
    runtime._prediction_maintenance_interval_ms = 100.
    runtime._opportunity_writer = None
    runtime.tool_wait_hints.clear()
    runtime._prepare_probe_after_ms.clear()
    runtime._parent_pressure_candidates.clear()
    for key in runtime.context_sessions.values():
        invocation = runtime.graph.invocations[key.invocation_id]
        invocation.state = (
            InvocationState.WAIT_JOIN if key.invocation_id.endswith("-root")
            else InvocationState.READY
        )
        invocation.active_tool_calls.clear()
    for index in range(contexts):
        key = runtime.context_sessions[f"ctx-wf-{index}-root"]
        candidate = runtime.capture_shadow_candidate(
            cache, context_id=key.context_id, context_epoch=key.context_epoch,
            include_non_actionable=True,
        )
        if (
            candidate is None or len(candidate.nodes) != depth
            or candidate.missing_full_host_tokens != 0
        ):
            raise ValueError("fixture does not exercise a complete backed closure")
    return runtime, queue, cache, reads, actions, now


def state(runtime, cache, actions):
    return {
        "actions": tuple(actions),
        "candidates": tuple(runtime._parent_pressure_candidates.items()),
        "publication": cache.beliefkv_join_pressure_candidates_by_component,
        "deadlines": (
            runtime._join_prepare_next_ms, runtime._tool_prepare_next_ms,
            tuple(runtime._prepare_probe_after_ms.items()),
        ),
        "cursor": runtime._join_prepare_cursor,
        "counts": {
            key: value for key, value in runtime.counts.items()
            if key != "prepare_fully_backed_capture_skipped"
        },
        "pending": runtime.physical_ledger.pending_count,
        "physical_disabled": runtime.physical_disabled,
    }


def measure(baseline, *, contexts, depth, dirty, iterations):
    variants = {
        name: fixture(cls, contexts=contexts, depth=depth, dirty=dirty)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    clock = [max(value[-1] for value in variants.values()) + 1.]
    timings = {name: [] for name in variants}
    read_totals = {name: Counter() for name in variants}
    try:
        with patch("time.monotonic", lambda: clock[0]):
            for iteration in range(iterations + 10):
                clock[0] += .06
                order = list(variants)
                if iteration % 2:
                    order.reverse()
                outputs = {}
                for name in order:
                    runtime, queue, cache, reads, actions, _ = variants[name]
                    before_reads = reads.copy()
                    actions.clear()
                    started = time.thread_time_ns()
                    runtime.dispatch_after_forward(queue)
                    elapsed = (time.thread_time_ns() - started) / 1e6
                    if iteration >= 10:
                        timings[name].append(elapsed)
                        read_totals[name].update(reads - before_reads)
                    outputs[name] = state(runtime, cache, actions)
                if outputs["baseline"] != outputs["candidate"]:
                    raise AssertionError((contexts, depth, dirty, iteration, outputs))
        costs = {name: distribution(values) for name, values in timings.items()}
        return {
            "contexts": contexts, "depth": depth, "dirty_elsewhere": dirty,
            "iterations": iterations, "clock_step_ms": 60.,
            "actions_candidates_publication_cursors_deadlines_and_old_counters_equal": True,
            "thread_cpu_ms": costs,
            "mean_thread_cpu_reduction": (
                1. - costs["candidate"]["mean_ms"] / costs["baseline"]["mean_ms"]
            ),
            "native_reads": {name: dict(values) for name, values in read_totals.items()},
            "skipped_complete_closures_including_warmup": variants["candidate"][0].counts[
                "prepare_fully_backed_capture_skipped"
            ],
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        parser.error("use positive iterations and a fresh output path")
    baseline, digests = baseline_runtime(args.baseline_revision)
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "scripts/benchmark_complete_prepare_shortcut.py",
    )
    result = {
        "status": "complete_cpu_control_comparison_not_gpu_or_jct_gain",
        "baseline_revision": args.baseline_revision, "baseline_sources": digests,
        "candidate_sources": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths
        },
        "cases": [
            measure(baseline, contexts=36, depth=depth, dirty=dirty, iterations=args.iterations)
            for depth in (16, 64) for dirty in (False, True)
        ],
        "scope": [
            "Alternating full dispatch cycles use pinned baseline runtime/observer/physical modules.",
            "Every root must expose a real validated backed closure before measurement.",
            "Native pool, tree and transfer APIs are CPU stand-ins; there is no DMA or GPU execution.",
            "All prior action, pressure publication, deadline, cursor and counter outputs must agree.",
            "The dirty-elsewhere case preserves the complete closure fallback.",
            "Synthetic frequencies and CPU reductions do not establish online throughput or JCT gains.",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([{
        key: row[key] for key in (
            "contexts", "depth", "dirty_elsewhere", "thread_cpu_ms",
            "mean_thread_cpu_reduction", "skipped_complete_closures_including_warmup",
        )
    } for row in result["cases"]], indent=2))


if __name__ == "__main__":
    main()
