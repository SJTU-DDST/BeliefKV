#!/usr/bin/env python3
"""Measure immutable-key equality in complete CPU prefill planning/selection."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import (
    PrefillCandidateKey,
    select_native_prefill_candidates,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


def baseline_equality(revision):
    path = "beliefkv/runtime/sglang_v0520_admission.py"
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
    )
    module = ModuleType("_beliefkv_prefill_equality_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{revision}:{path}", "exec"), module.__dict__)
    return module.PrefillCandidateKey.__eq__, {path: hashlib.sha256(source.encode()).hexdigest()}


def benchmark(*, baseline, iterations, workflows, profiling, rebuilt):
    implementations = {"baseline": baseline, "candidate": PrefillCandidateKey.__eq__}
    variants = {
        name: synthetic_runtime(
            NativeAdmissionRuntime, CausalFrontierScheduler, workflows, 2, profiling=profiling,
        )
        for name in implementations
    }
    samples = {name: [] for name in variants}
    adder, running = NS(), NS(reqs=[])
    live_request_key = runtime_module._request_key
    key_reader = (
        (lambda request, *, known_keys=None: live_request_key(request))
        if rebuilt else live_request_key
    )
    warmup = 20
    try:
        with patch.object(runtime_module, "_request_key", key_reader):
            for iteration in range(iterations + warmup):
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                plans, selections, graphs = {}, {}, {}
                for name in names:
                    runtime, queue = variants[name]
                    runtime.enable_resident_first = False
                    key = runtime.visible[queue[iteration % len(queue)].rid]
                    events = tuple(
                        RuntimeEvent(
                            f"{iteration}-{kind.value}", float(iteration), kind,
                            key.root_workflow_id, invocation_id=key.invocation_id,
                            context_id=key.context_id, context_epoch=key.context_epoch,
                        )
                        for kind in (RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT)
                    )
                    now = time.monotonic()
                    runtime._visible_since = {
                        request_id: now for request_id in runtime.visible
                    }
                    with patch.object(PrefillCandidateKey, "__eq__", implementations[name]):
                        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                        runtime.on_events(events)
                        runtime.scheduler_step(queue, defer_observations=True)
                        plan = runtime.plan_native_prefill(
                            queue, running_batch=running, adder=adder,
                        )
                        selection = select_native_prefill_candidates(
                            queue, plan=plan,
                            current_semantic_revision=runtime.semantic_revision,
                        )
                        elapsed_cpu = (time.thread_time_ns() - cpu) / 1_000_000.
                        elapsed_wall = (time.perf_counter_ns() - wall) / 1_000_000.
                    if iteration >= warmup:
                        samples[name].append({
                            "thread_cpu_ms": elapsed_cpu, "wall_ms": elapsed_wall,
                        })
                    plans[name] = plan
                    selections[name] = (
                        tuple(request.rid for request in selection.candidates),
                        selection.rejected,
                    )
                    graphs[name] = runtime.graph.snapshot()
                    if (
                        runtime.physical_ledger.pending_count
                        or runtime.completed_physical_actions
                    ):
                        raise AssertionError("CPU equality fixture issued a physical action")
                if (
                    plans["baseline"] != plans["candidate"]
                    or selections["baseline"] != selections["candidate"]
                    or graphs["baseline"] != graphs["candidate"]
                ):
                    raise AssertionError("planning, live selection or full causal state changed")
        costs = {
            name: {
                metric: distribution([row[metric] for row in rows])
                for metric in ("thread_cpu_ms", "wall_ms")
            }
            for name, rows in samples.items()
        }
        return {
            "workflows": workflows, "join_rounds_per_workflow": 2,
            "profiling_enabled": profiling, "forced_rebuilt_keys": rebuilt,
            "iterations": iterations, "warmup_iterations": warmup, "costs": costs,
            "thread_cpu_mean_reduction_fraction": 1. - (
                costs["candidate"]["thread_cpu_ms"]["mean_ms"]
                / costs["baseline"]["thread_cpu_ms"]["mean_ms"]
            ),
            "plans_live_selection_and_complete_graph_equal": True,
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
    if args.iterations <= 0 or args.output.exists():
        raise ValueError("positive iterations and a fresh report path required")
    baseline, baseline_sources = baseline_equality(args.baseline_revision)
    cases = [
        benchmark(
            baseline=baseline, iterations=args.iterations, workflows=workflows,
            profiling=profiling, rebuilt=rebuilt,
        )
        for workflows in (108, 156)
        for profiling in (False, True)
        for rebuilt in (False, True)
    ]
    paths = (
        "beliefkv/runtime/sglang_v0520_admission.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/hotpath_timing.py",
        "beliefkv/policy/causal_frontier.py",
        "scripts/benchmark_native_shared_path.py",
        "scripts/benchmark_prefill_key_equality.py",
    )
    report = {
        "status": "prefill_key_equality_cpu_evidence_not_gpu_acceptance",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline_revision,
        "baseline_sources": baseline_sources,
        "candidate_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
        "cases": cases,
        "limitations": [
            "Only the baseline equality method is substituted; both variants otherwise use the current complete runtime and key hash.",
            "Timed calls include events, maintenance, full prefill planning and live native selection, with profiling enabled and disabled.",
            "Rebuilt-key controls force a distinct parsed identity during planning; live selection still validates all request/session fields.",
            "The same-order graph/plan/selection assertions run outside timing; GC stays enabled and fixtures do not accumulate graph histories.",
            "These queues are CPU fixtures, not observed V59 frequencies, native allocator timing or JCT/GPU evidence.",
            "The existing frozen V60 and qualified d925278 candidate are untouched.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([
        {key: case[key] for key in (
            "workflows", "profiling_enabled", "forced_rebuilt_keys",
            "thread_cpu_mean_reduction_fraction",
        )}
        for case in cases
    ], indent=2), flush=True)


if __name__ == "__main__":
    main()
