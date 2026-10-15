#!/usr/bin/env python3
"""Compare complete CPU admission with identical live graph transitions."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import (
    baseline_classes, distribution, synthetic_runtime,
)


def measure(baseline, baseline_frontier, *, workflows, profiling, scenario, iterations):
    variants = {
        "baseline": synthetic_runtime(
            baseline, baseline_frontier, workflows, 2, profiling=profiling,
        ),
        "candidate": synthetic_runtime(
            NativeAdmissionRuntime, CausalFrontierScheduler, workflows, 2,
            profiling=profiling,
        ),
    }
    samples = {name: [] for name in variants}
    running, adder = NS(reqs=[]), NS()
    outputs = {}
    warmup = 20
    try:
        for runtime, _ in variants.values():
            runtime.enable_resident_first = False
            runtime.enable_final_stage_priority = True
            runtime._final_priority_normal_admissions = 4
            runtime._prefetch_priority_normal_admissions = 4
        for iteration in range(iterations + warmup):
            order = list(variants)
            if iteration % 2:
                order.reverse()
            for name in order:
                runtime, queue = variants[name]
                key = runtime.visible[queue[iteration % len(queue)].rid]
                if scenario == "stable":
                    kinds = ()
                elif scenario == "local_round":
                    kinds = (RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT)
                else:
                    kinds = (RuntimeEventKind.MESSAGE,)
                events = tuple(
                    RuntimeEvent(
                        f"{iteration}-{kind.value}", float(iteration), kind,
                        key.root_workflow_id, invocation_id=key.invocation_id,
                        context_id=key.context_id, context_epoch=key.context_epoch,
                        target_invocation_id=(
                            key.invocation_id if kind is RuntimeEventKind.MESSAGE else None
                        ),
                    )
                    for kind in kinds
                )
                now = time.monotonic()
                runtime._visible_since = {
                    request_id: now for request_id in runtime.visible
                }
                wall_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                if events:
                    runtime.on_events(events)
                runtime.scheduler_step(queue, defer_observations=True)
                plan = runtime.plan_native_prefill(
                    queue, running_batch=running, adder=adder,
                )
                selection = select_native_prefill_candidates(
                    queue, plan=plan,
                    current_semantic_revision=runtime.semantic_revision,
                )
                cpu_ms = (time.thread_time_ns() - cpu_start) / 1_000_000.
                wall_ms = (time.perf_counter_ns() - wall_start) / 1_000_000.
                if iteration >= warmup:
                    samples[name].append({"thread_cpu_ms": cpu_ms, "wall_ms": wall_ms})
                outputs[name] = (
                    plan, tuple(request.rid for request in selection.candidates),
                    selection.rejected, runtime.graph.snapshot(), dict(runtime.counts),
                )
                if runtime.physical_ledger.pending_count or runtime.completed_physical_actions:
                    raise AssertionError("CPU admission fixture issued a physical action")
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError("plan, selection, complete graph or counters changed")
        costs = {
            name: {
                field: distribution([sample[field] for sample in values])
                for field in ("thread_cpu_ms", "wall_ms")
            }
            for name, values in samples.items()
        }
        return {
            "workflows": workflows, "scenario": scenario, "profiling_enabled": profiling,
            "iterations": iterations, "warmup_iterations": warmup, "costs": costs,
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "plan_selection_graph_and_counters_equal": True, "physical_actions": 0,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.output.exists():
        raise ValueError("positive iterations and a fresh output path are required")
    baseline, frontier, sources = baseline_classes(args.baseline_revision)
    cases = [
        measure(
            baseline, frontier, workflows=workflows, profiling=profiling,
            scenario=scenario, iterations=args.iterations,
        )
        for workflows in (108, 156)
        for profiling in (False, True)
        for scenario in ("stable", "local_round", "message_control")
    ]
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_admission.py",
        "beliefkv/runtime/hotpath_timing.py",
        "beliefkv/policy/causal_frontier.py",
        "scripts/benchmark_prefill_live_checks.py",
    )
    report = {
        "status": "prefill_live_check_cpu_comparison_not_gpu_or_jct_acceptance",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision, "baseline_sources": sources,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
        "cases": cases,
        "limits": [
            "Synthetic same-input CPU queues do not establish live frequency, GPU or JCT gains.",
            "Timing includes causal events, scheduler maintenance, complete planning and live selection.",
            "Resident-first is disabled, final priority is enabled with no final-stage candidates.",
            "Message control invalidates causal ranks; stable cases retain the prior causal cache.",
            "All identity parsing and final terminal checks remain; GC and timing stay enabled.",
            "Native allocator, neural workers, socket IPC and DMA are outside this fixture.",
            "V60, the qualified d925278 candidate and the reservation waiter remain unchanged.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "workflows", "scenario", "profiling_enabled",
            "thread_cpu_mean_reduction_fraction",
        )}
        for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
