#!/usr/bin/env python3
"""Compare observation-event admission cost on identical CPU causal graphs."""

from __future__ import annotations

import argparse
from collections import Counter
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
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def benchmark(*, revision: str, iterations: int, workflows: int, rounds: int,
              kind: str):
    baseline, sources = baseline_runtime(revision)
    variants = {
        name: synthetic_runtime(
            runtime_class, CausalFrontierScheduler, workflows, rounds, profiling=True,
        )
        for name, runtime_class in (
            ("baseline", baseline), ("candidate", NativeAdmissionRuntime),
        )
    }
    samples = {name: [] for name in variants}
    rank_calls = Counter()
    adder, running = NS(), NS(reqs=[])
    for name, (runtime, queue) in variants.items():
        runtime.enable_resident_first = False
        original = runtime.frontier.admission_rank

        def rank(invocation, *, name=name, original=original):
            rank_calls[name] += 1
            return original(invocation)

        runtime.frontier.admission_rank = rank
        runtime.scheduler_step(queue, defer_observations=True)
        runtime.plan_native_prefill(queue, running_batch=running, adder=adder)
    try:
        for iteration in range(iterations):
            names = list(variants)
            if iteration % 2:
                names.reverse()
            plans, snapshots = {}, {}
            for name in names:
                runtime, queue = variants[name]
                key = runtime.visible[queue[iteration % len(queue)].rid]
                if kind == "tool_round":
                    events = tuple(
                        RuntimeEvent(
                            f"{iteration}-{event_kind.value}", float(iteration),
                            event_kind, key.root_workflow_id,
                            invocation_id=key.invocation_id, context_id=key.context_id,
                            context_epoch=key.context_epoch,
                            attributes={"tool_run_id": f"run-{iteration}", "tool_name": "execute"},
                        )
                        for event_kind in (RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END)
                    )
                else:
                    events = (RuntimeEvent(
                        f"observation-{iteration}", float(iteration),
                        RuntimeEventKind(kind), key.root_workflow_id,
                        invocation_id=key.invocation_id, context_id=key.context_id,
                        context_epoch=key.context_epoch,
                    ),)
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                runtime.on_events(events)
                runtime.scheduler_step(queue, defer_observations=True)
                plan = runtime.plan_native_prefill(queue, running_batch=running, adder=adder)
                samples[name].append({
                    "thread_cpu_ms": (time.thread_time_ns() - cpu) / 1_000_000.,
                    "wall_ms": (time.perf_counter_ns() - wall) / 1_000_000.,
                })
                plans[name] = plan
                snapshots[name] = runtime.graph.snapshot()
                if runtime.physical_ledger.pending_count:
                    raise AssertionError("a classification fixture issued a physical action")
            if plans["baseline"] != plans["candidate"] or snapshots["baseline"] != snapshots["candidate"]:
                raise AssertionError("admission or causal graph state changed")
        costs = {
            name: {
                field: distribution([row[field] for row in values])
                for field in ("thread_cpu_ms", "wall_ms")
            } for name, values in samples.items()
        }
        return {
            "scope": "Identical event+maintenance+admission CPU fixture; no native page inspection, GPU or JCT proof.",
            "kind": kind, "workflows": workflows, "join_rounds_per_workflow": rounds,
            "iterations": iterations, "profiling_enabled_in_both_variants": True,
            "costs": costs, "rank_calls": dict(rank_calls),
            "classification_preserved": variants["candidate"][0].counts[
                "admission_causal_cache_preserved_observation"
            ],
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "baseline_revision": revision, "baseline_sources": sources,
            "request_order_and_complete_graph_snapshots_equal": True,
            "physical_actions": 0,
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
        raise ValueError("positive iterations and a new evidence path are required")
    cases = [
        benchmark(
            revision=args.baseline_revision, iterations=args.iterations,
            workflows=workflows, rounds=rounds, kind=kind,
        )
        for workflows, rounds in ((108, 1), (156, 2))
        for kind in ("structured_action", "tool_wait_observation", "call_censored", "tool_round")
    ]
    report = {
        "status": "cpu_observation_cache_comparison_not_online_acceptance",
        "created_at_local": datetime.now().astimezone().isoformat(),
        "cases": cases,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "beliefkv/policy/causal_frontier.py",
                "scripts/benchmark_observation_rank_cache.py",
            )
        },
        "limitations": [
            "Synthetic queue/graph sizes are scenarios, not captured V59 phase frequencies.",
            "Current and baseline receive the same events and use the same timing profiler.",
            "The tool-round negative control changes executable state and must retain full invalidation.",
            "Resident-first and physical cache probes are disabled in both CPU variants; live physical checks are unchanged by the candidate.",
            "No workload, timing model, weights, eligibility, eviction, priority or native dependency changes.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {
            "workflows": row["workflows"], "rounds": row["join_rounds_per_workflow"],
            "kind": row["kind"], "cpu_reduction": row["thread_cpu_mean_reduction_fraction"],
            "rank_calls": row["rank_calls"],
        } for row in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
