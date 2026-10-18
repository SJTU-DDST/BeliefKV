#!/usr/bin/env python3
"""Compare pinned runtime control paths on CPU without launching SGLang."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _ChildFinalStage
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import synthetic_runtime
from tests.test_wait_prefetch_batch import batch_fixture


def distribution(values):
    ordered = sorted(values)
    return {
        "mean_us": mean(values), "p50_us": median(values),
        "p90_us": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
        "p99_us": ordered[min(len(ordered) - 1, int(.99 * len(ordered)))],
    }


def compare(callbacks, *, samples, calls=50):
    values = {name: [] for name in callbacks}
    for sample in range(samples + 20):
        order = list(callbacks)
        if sample % 2:
            order.reverse()
        for name in order:
            start = time.thread_time_ns()
            for _ in range(calls):
                callbacks[name]()
            elapsed = (time.thread_time_ns() - start) / calls / 1000.
            if sample >= 20:
                values[name].append(elapsed)
    return {name: distribution(observed) for name, observed in values.items()}


def idle_case(baseline, *, samples, profiling):
    variants = {
        name: cls() for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    try:
        for runtime in variants.values():
            runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
            runtime.enable_prepare_host = False
            runtime._prediction_maintenance_interval_ms = 100.
            runtime._hotpath_timing.enabled = profiling
            runtime.event_server = NS(drain=lambda **kwargs: (), close=lambda: None)
        costs = compare({
            name: lambda runtime=runtime: (
                runtime.scheduler_step(defer_observations=True),
                runtime.dispatch_after_forward(),
            ) for name, runtime in variants.items()
        }, samples=samples)
        if any(runtime.physical_disabled or runtime.physical_ledger.pending_count
               for runtime in variants.values()):
            raise AssertionError("empty candidates produced a physical action")
        return {
            "case": "empty_wait_sources_scheduler_and_post_forward",
            "profiling": profiling, "costs": costs,
            "physical_actions": 0, "live_state_equal": True,
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def pending_case(baseline, *, samples, pending):
    variants = {
        name: cls() for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    key = PrefillCandidateKey("r", "wf", "i", "ctx-2", 0, 0, "s-2", 1)
    budget = PrefetchResidencyBudget(2, 10_000)
    canonical = {}
    try:
        for name, runtime in variants.items():
            runtime._parallel_wait_prefetch = True
            runtime._wait_prefetch_contexts = 2
            runtime.attach_native_cache(NS(supports_beliefkv_overlap_prefetch=lambda: True))
            for index in range(pending):
                owner = index % 2
                runtime.physical_ledger.register(PhysicalActionExpectation(
                    f"cmd-{index}", "PREFETCH_GPU", f"ctx-{owner}", index % 3,
                    (PhysicalChildExpectation(index, (index,), (("kv", 20),), 20),),
                    (("kv", 10),), session_id=f"s-{owner}", session_generation=1,
                ))
            canonical[name] = [
                asdict(runtime.physical_ledger.pending_expectation(f"cmd-{index}"))
                for index in range(pending)
            ]

        def query(runtime):
            return (
                runtime._wait_prefetch_transfer_busy(key),
                runtime._prefetch_slot_available(key, budget=budget),
                runtime._prefetch_byte_allowance(budget),
            )

        outputs = {name: query(runtime) for name, runtime in variants.items()}
        if canonical["baseline"] != canonical["candidate"] or outputs["baseline"] != outputs["candidate"]:
            raise AssertionError("physical ownership or budgets changed")
        return {
            "case": "parallel_restore_budget_queries", "pending_commands": pending,
            "physical_state_and_query_results_equal": True,
            "costs": compare({
                name: lambda runtime=runtime: query(runtime)
                for name, runtime in variants.items()
            }, samples=samples),
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def repeated_plan_case(baseline, *, samples, depth):
    variants = {}
    clock = [1000.]
    try:
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
            runtime, key, opportunity, _, _ = batch_fixture("tool_wait", node_count=depth)
            runtime.enable_h2d_opportunity_sampling = False
            rows = []
            runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
            variants[name] = runtime, cls._record_wait_prefetch_plan, key, opportunity, rows
        costs = {name: [] for name in variants}
        calls = 10
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: clock[0]):
            for sample in range(samples + 20):
                order = list(variants)
                if sample % 2:
                    order.reverse()
                for name in order:
                    runtime, method, key, opportunity, _ = variants[name]
                    started = time.thread_time_ns()
                    for _ in range(calls):
                        method(runtime, "tool_wait", key, opportunity, reason="no_cold_capacity")
                    elapsed = (time.thread_time_ns() - started) / calls / 1000.
                    if sample >= 20:
                        costs[name].append(elapsed)
                clock[0] += .6
        evidence = {
            name: {key: value for key, value in variant[4][0].items() if key != "ts_ms"}
            for name, variant in variants.items()
        }
        if evidence["baseline"] != evidence["candidate"]:
            raise AssertionError("initial diagnostic snapshot changed")
        if len(variants["candidate"][4]) != 1:
            raise AssertionError("unchanged candidate plan was periodically emitted")
        for runtime, _, key, opportunity, _ in variants.values():
            runtime._record_wait_prefetch_plan(
                "tool_wait", key, replace(opportunity, required_full_tokens=1),
                reason="no_cold_capacity",
            )
        return {
            "case": "unchanged_plan_without_diagnostic_sampling", "closure_nodes": depth + 1,
            "initial_evidence_equal": True, "changes_still_recorded": True,
            "periodic_rows_before_changed_plan": {
                name: len(value[4]) - 1 for name, value in variants.items()
            },
            "costs": {name: distribution(observed) for name, observed in costs.items()},
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def join_scan_fixture(cls, *, workflows):
    runtime, queue = synthetic_runtime(cls, CausalFrontierScheduler, workflows, 4)
    runtime.enable_final_stage_prefetch = True
    runtime._h2d_samples.extend(((100, 10.),) * 3)
    reads = [0]

    class RunningRequest:
        def __init__(self, request_id):
            self.request_id = request_id

        @property
        def rid(self):
            reads[0] += 1
            return self.request_id

    runtime._native_running_batch = NS(reqs=[
        RunningRequest(request.rid) for request in queue[:48]
    ])
    for number in range(workflows):
        workflow = f"wf-{number}"
        parent, child_id = f"{workflow}-root", f"{workflow}-child-12"
        join_id = f"{workflow}-join-3"
        key = PrefillCandidateKey(
            f"root-{number}", workflow, parent, f"ctx-{parent}", 0, 0,
            f"root-session-{number}", 1,
        )
        runtime.context_sessions[key.context_id] = key
        runtime.graph.invocations[parent].join_id = join_id
        runtime._final_stages[join_id] = _ChildFinalStage(
            key, join_id, child_id, 0, 1000, time.monotonic() + 3600.,
            request_id=f"req-{child_id}", generated_tokens=100, tokens_per_second=40.,
        )
    if not all(runtime._live_final_stage(stage) for stage in runtime._final_stages.values()):
        raise AssertionError("JOIN CPU fixture has invalid dependencies")
    return runtime, reads


def join_scan_case(baseline, *, samples, workflows):
    variants = {
        name: join_scan_fixture(cls, workflows=workflows)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    try:
        membership_reads = {}
        for name, (runtime, reads) in variants.items():
            if runtime._roll_final_stage() is not None:
                raise AssertionError("long report phase unexpectedly produced a restore plan")
            membership_reads[name] = reads[0]
            runtime._native_running_batch = NS(reqs=[
                NS(rid=request.request_id) for request in runtime._native_running_batch.reqs
            ])
        costs = compare({
            name: runtime._roll_final_stage for name, (runtime, _) in variants.items()
        }, samples=samples, calls=10)
        if any(runtime._join_ticket is not None or runtime.physical_ledger.pending_count
               for runtime, _ in variants.values()):
            raise AssertionError("JOIN candidate scan issued a physical action")
        return {
            "case": "live_join_final_stage_scan", "workflows": workflows,
            "running_requests": min(48, workflows), "costs": costs,
            "running_membership_reads_per_scan": membership_reads,
            "membership_counts_measured_separately": True,
            "live_graph_dependencies_checked": True, "physical_actions": 0,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="31d6849")
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/hotpath_timing.py",
        "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_native_shared_path.py",
        "tests/test_wait_prefetch_batch.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    cases = [
        *(idle_case(baseline, samples=args.samples, profiling=enabled)
          for enabled in (False, True)),
        *(pending_case(baseline, samples=args.samples, pending=pending)
          for pending in (0, 16, 64)),
        *(repeated_plan_case(baseline, samples=args.samples, depth=depth)
          for depth in (16, 128)),
        *(join_scan_case(baseline, samples=args.samples, workflows=workflows)
          for workflows in (48, 156)),
    ]
    for case in cases:
        costs = case["costs"]
        case["mean_thread_cpu_reduction_fraction"] = (
            1. - costs["candidate"]["mean_us"] / costs["baseline"]["mean_us"]
        )
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("CPU benchmark sources changed")
    report = {
        "scope": "CPU fixtures; no SGLang server, CUDA, DMA, throughput or JCT measurement",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "candidate_source_sha256": sources,
        "samples": args.samples, "warmup_samples": 20,
        "cases": cases,
        "limitations": [
            "Fixture frequency and pending populations are controlled, not replayed online frequencies.",
            "Both runtimes use the same profiling setting in each timing case.",
            "Removing repeated unchanged diagnostic rows intentionally changes observation density.",
            "Required final causal drain, prefix recapture, native capacity checks and ACK credit remain.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(cases, indent=2), flush=True)


if __name__ == "__main__":
    main()
