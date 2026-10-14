#!/usr/bin/env python3
"""Compare local rank invalidation with the frozen observation-only candidate."""

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
import beliefkv.policy.causal_frontier as frontier_module
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def benchmark(*, revision, iterations, workflows, rounds, kind):
    baseline, sources = baseline_runtime(revision)
    frontier_path = "beliefkv/policy/causal_frontier.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{frontier_path}"], cwd=ROOT, text=True,
    )
    namespace = dict(vars(frontier_module))
    exec(compile(source, f"{revision}:{frontier_path}", "exec"), namespace)
    sources[frontier_path] = hashlib.sha256(source.encode()).hexdigest()
    variants = {
        "baseline": synthetic_runtime(
            baseline, namespace["CausalFrontierScheduler"], workflows, rounds, profiling=True,
        ),
        "candidate": synthetic_runtime(
            NativeAdmissionRuntime, CausalFrontierScheduler, workflows, rounds, profiling=True,
        ),
    }
    samples = {name: [] for name in variants}
    classifications = Counter()
    adder, running = NS(), NS(reqs=[])
    try:
        for name, (runtime, queue) in variants.items():
            runtime.enable_resident_first = False
            original = runtime.frontier._classification

            def classify(*args, name=name, original=original, **kwargs):
                classifications[name] += 1
                return original(*args, **kwargs)

            runtime.frontier._classification = classify
            runtime.scheduler_step(queue, defer_observations=True)
            runtime.plan_native_prefill(queue, running_batch=running, adder=adder)
        for iteration in range(iterations):
            names = list(variants)
            if iteration % 2:
                names.reverse()
            plans, snapshots = {}, {}
            for name in names:
                runtime, queue = variants[name]
                key = runtime.visible[queue[iteration % len(queue)].rid]
                if kind == "llm_round":
                    kinds = (RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT)
                elif kind == "tool_round":
                    kinds = (RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END)
                elif kind == "context":
                    kinds = (RuntimeEventKind.CONTEXT_ADVANCE,)
                elif kind == "observation_control":
                    kinds = (RuntimeEventKind.TOOL_WAIT_OBSERVATION,)
                else:
                    kinds = (RuntimeEventKind.MESSAGE,)
                events = tuple(
                    RuntimeEvent(
                        f"{iteration}-{event_kind.value}", float(iteration),
                        event_kind, key.root_workflow_id,
                        invocation_id=key.invocation_id, context_id=key.context_id,
                        context_epoch=key.context_epoch,
                        target_invocation_id=(
                            key.invocation_id if event_kind is RuntimeEventKind.MESSAGE else None
                        ),
                        attributes={"tool_name": "execute", "tool_run_id": f"run-{iteration}"},
                    )
                    for event_kind in kinds
                )
                wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                runtime.on_events(events)
                runtime.scheduler_step(queue, defer_observations=True)
                plans[name] = runtime.plan_native_prefill(queue, running_batch=running, adder=adder)
                samples[name].append({
                    "thread_cpu_ms": (time.thread_time_ns() - cpu) / 1_000_000.,
                    "wall_ms": (time.perf_counter_ns() - wall) / 1_000_000.,
                })
                snapshots[name] = runtime.graph.snapshot()
                if runtime.physical_ledger.pending_count:
                    raise AssertionError("a CPU classification fixture issued a physical action")
            if plans["baseline"] != plans["candidate"] or snapshots["baseline"] != snapshots["candidate"]:
                raise AssertionError("request order or complete causal state changed")
        costs = {
            name: {
                field: distribution([row[field] for row in values])
                for field in ("thread_cpu_ms", "wall_ms")
            } for name, values in samples.items()
        }
        return {
            "kind": kind, "workflows": workflows, "join_rounds_per_workflow": rounds,
            "iterations": iterations, "costs": costs,
            "classification_calls": dict(classifications),
            "retained_dependency_batches": variants["candidate"][0].counts[
                "admission_rank_dependencies_preserved"
            ],
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "request_order_and_complete_graph_snapshots_equal": True,
            "physical_actions": 0, "baseline_revision": revision, "baseline_sources": sources,
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
        raise ValueError("positive iterations and a new report path are required")
    cases = [
        benchmark(
            revision=args.baseline_revision, iterations=args.iterations,
            workflows=workflows, rounds=rounds, kind=kind,
        )
        for workflows, rounds in ((108, 1), (156, 2))
        for kind in ("llm_round", "tool_round", "context", "observation_control", "message_control")
    ]
    report = {
        "status": "local_rank_dependency_cpu_comparison_not_online_acceptance",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "cases": cases,
        "candidate_sources": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "beliefkv/policy/causal_frontier.py",
                "scripts/benchmark_local_rank_dependencies.py",
            )
        },
        "limitations": [
            "These are identical synthetic CPU scenes, not real V59/V60 batch frequencies or GPU/JCT evidence.",
            "Both variants use their own frontier and runtime source; profiling is enabled in both.",
            "Only local event pairs retain unaffected ranks; message control must rebuild.",
            "Resident-first and native page inspection are disabled; no physical action is requested.",
            "Frozen V60 and all budgets, models, capacity and priority policies remain unchanged.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "kind", "workflows", "classification_calls", "thread_cpu_mean_reduction_fraction",
        )} for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
