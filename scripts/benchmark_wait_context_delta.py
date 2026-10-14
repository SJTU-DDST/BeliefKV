#!/usr/bin/env python3
"""Measure complete event/binding updates plus unchanged ordered wait queries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def measure_case(baseline, *, workflows, mode, iterations):
    variants = {
        name: synthetic_runtime(cls, CausalFrontierScheduler, workflows, 16)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    samples = {
        name: {"thread_cpu_ms": [], "wall_ms": []} for name in variants
    }
    for runtime, _ in variants.values():
        for workflow in range(workflows):
            wid, invocation = f"wf-{workflow}", f"wf-{workflow}-root"
            if not runtime.register_visible_request(NS(
                rid=f"req-{invocation}", session_id=f"session-ctx-{invocation}",
                session_generation=0, cache_request_handle=NS(attempt_id=0),
                beliefkv_metadata={
                    "root_workflow_id": wid, "invocation_id": invocation,
                    "context_id": f"ctx-{invocation}", "context_epoch": 0,
                },
            )):
                raise AssertionError("waiting parent must have a valid native binding")
        if len(runtime._waiting_context_keys(InvocationState.WAIT_JOIN)) != workflows:
            raise AssertionError("empty JOIN candidates cannot measure this fixture")
        runtime._local_frontier_features(
            runtime.graph.invocations["wf-0-child-48"], 0, now_ms=1000.,
        )
    clock = [1000.]
    first_child = "wf-0-child-48"
    first_context = f"ctx-{first_child}"
    states = (InvocationState.WAIT_JOIN, InvocationState.WAIT_TOOL)
    try:
        with patch("time.monotonic", lambda: clock[0]):
            for iteration in range(iterations + 10):
                clock[0] = 1000. + iteration * .01
                order = list(variants)
                if iteration % 2:
                    order.reverse()
                observed = {}
                for name in order:
                    runtime, requests = variants[name]
                    event = None
                    if mode in ("unrelated_llm", "waiting_change", "external_graph_change"):
                        if mode == "unrelated_llm":
                            kind = RuntimeEventKind.LLM_SUBMIT if iteration % 2 == 0 else RuntimeEventKind.LLM_RESULT
                        else:
                            kind = RuntimeEventKind.TOOL_START if iteration % 2 == 0 else RuntimeEventKind.TOOL_END
                        event = RuntimeEvent(
                            event_id=f"bench:{mode}:{iteration}", ts_ms=clock[0] * 1000.,
                            workflow_id="wf-0", invocation_id=first_child,
                            context_id=first_context, context_epoch=0, kind=kind,
                        )
                    elif mode == "observation":
                        event = RuntimeEvent(
                            event_id=f"bench:{mode}:{iteration}", ts_ms=clock[0] * 1000.,
                            workflow_id="wf-0", invocation_id=first_child,
                            kind=RuntimeEventKind.TOOL_WAIT_OBSERVATION,
                        )
                    request = next(
                        item for item in requests
                        if item.beliefkv_metadata["invocation_id"] == first_child
                    ) if mode == "unrelated_rebind" else None
                    if request is not None:
                        request.session_generation = iteration + 2
                    cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                    if event is not None:
                        if mode == "external_graph_change":
                            runtime.graph.apply(event)
                        else:
                            runtime.on_events((event,))
                    elif request is not None:
                        runtime.on_requests_requeued((request,), is_retracted=True)
                    keys = tuple(runtime._waiting_context_keys(state) for state in states)
                    wall_ms = (time.perf_counter_ns() - wall) / 1_000_000.
                    cpu_ms = (time.thread_time_ns() - cpu) / 1_000_000.
                    observed[name] = keys
                    if iteration >= 10:
                        samples[name]["thread_cpu_ms"].append(cpu_ms)
                        samples[name]["wall_ms"].append(wall_ms)
                if observed["baseline"] != observed["candidate"]:
                    raise AssertionError(f"membership/order changed in {mode}/{iteration}")
        costs = {
            name: {clock: distribution(values) for clock, values in measurements.items()}
            for name, measurements in samples.items()
        }
        return {
            "workflows": workflows, "mode": mode, "iterations": iterations,
            "native_proxy_contexts": len(variants["candidate"][0].context_sessions),
            "ordered_waiting_keys_equal": True,
            "combined_update_and_query_costs": costs,
            "thread_cpu_mean_reduction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "candidate_cache_counts": {
                key: value for key, value in variants["candidate"][0].counts.items()
                if key.startswith("wait_context_")
            },
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=301)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        raise ValueError("positive repetitions and a new output path required")
    source_paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_wait_context_delta.py",
        "tests/test_native_wait_candidates.py",
    )
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths
    }
    baseline, sources = baseline_runtime(args.baseline_revision)
    cases = [
        measure_case(baseline, workflows=workflows, mode=mode, iterations=args.iterations)
        for workflows in (24, 156, 512)
        for mode in (
            "cached", "observation", "unrelated_llm", "waiting_change",
            "unrelated_rebind", "external_graph_change",
        )
    ]
    if any(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != wanted
        for name, wanted in source_hashes.items()
    ):
        raise ValueError("benchmark source changed during execution")
    report = {
        "status": "cpu_update_and_membership_fixture_not_online_frequency_or_gpu_gain",
        "baseline_revision": args.baseline_revision, "baseline_sources": sources,
        "candidate_sources": source_hashes,
        "cases": cases,
        "limitations": [
            "Both event/binding update and wait queries are timed; update overhead is not excluded.",
            "Identical CPU fixtures with16 prior rounds, one bound waiting parent and one bound live child per workflow; not sampled workload frequency.",
            "Alternating unpinned calls with a plain replay clock, no model worker, event transport or GPU.",
            "External graph changes exercise the full-scan fallback instead of relying on an incomplete delta.",
            "No physical prefix/capacity checks, allocation, prefetch consumption or JCT gains are inferred.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        field: case[field] for field in (
            "workflows", "mode", "thread_cpu_mean_reduction",
        )
    } for case in cases]), flush=True)


if __name__ == "__main__":
    main()
