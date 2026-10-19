#!/usr/bin/env python3
"""Compare complete historical workflow event handling with CPU timing only."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution


def source(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def trace_events(path):
    digest, events = hashlib.sha256(), []
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            events.append(RuntimeEvent.from_dict(json.loads(line)))
    if not events or events[-1].kind is not RuntimeEventKind.WORKFLOW_END:
        raise ValueError(f"workflow trace is not complete: {path}")
    return tuple(events), {
        "path": str(path.resolve()), "sha256": digest.hexdigest(),
        "events": len(events),
        "event_kinds": dict(Counter(event.kind.value for event in events)),
    }


def runtime_for(cls, *, mode, profiling):
    runtime = cls()
    runtime._hotpath_timing = HotpathTiming(enabled=profiling)
    if mode == "online":
        runtime._online_tool_timing = OnlineWorkloadToolTiming()
        runtime._tool_timing_only = True
        runtime.predictor_sha256 = "a" * 64
    else:
        runtime._online_tool_timing = None
        runtime._model_worker = NS(disabled=False, close=lambda: None)
    return runtime


def live_features(runtime):
    groups = {}
    for invocation_id, pending in runtime._tool_metadata_by_run.items():
        invocation = runtime.graph.invocations.get(invocation_id)
        workflow = runtime.graph.workflows.get(invocation.workflow_id) if invocation else None
        if pending and workflow is not None and workflow.end_ts_ms is None:
            groups[invocation_id] = pending
    return {
        "boundaries": {key: tuple(value) for key, value in runtime._boundary_history.items()},
        "metadata": dict(runtime._tool_metadata), "active_runs": groups,
    }


def online_state(model):
    return None if model is None else {
        "open": list(model.open.items()),
        "by_invocation": [
            (key, list(value.items())) for key, value in model._open_by_invocation.items()
        ],
        "samples": [(key, list(value)) for key, value in model.samples.items()],
        "revision": model.revision,
    }


def final_state(runtime):
    return {
        "graph": runtime.graph.snapshot(),
        "online": online_state(runtime._online_tool_timing),
        "counts": dict(runtime.counts), "revision": runtime.semantic_revision,
        "physical_disabled": runtime.physical_disabled,
        "pending": runtime.physical_ledger.pending_count,
        "visible": dict(runtime.visible), "contexts": dict(runtime.context_sessions),
    }


def validate(classes, events, *, mode, profiling, clock):
    runtimes = {
        name: runtime_for(cls, mode=mode, profiling=profiling) for name, cls in classes.items()
    }
    boundary_calls = Counter({name: 0 for name in classes})
    highwater, estimates = Counter(), Counter()
    original = {name: runtime.on_events.__wrapped__.__globals__["observed_boundary_action"]
                for name, runtime in runtimes.items()}
    for name, runtime in runtimes.items():
        def counted(event, *, name=name):
            boundary_calls[name] += 1
            return original[name](event)
        runtime.on_events.__wrapped__.__globals__["observed_boundary_action"] = counted
    try:
        for event in events:
            clock[0] = event.ts_ms / 1000.
            observed, predicted, features = {}, {}, {}
            for name, runtime in runtimes.items():
                runtime.on_events((event,))
                highwater[name] = max(highwater[name], len(runtime._tool_metadata_by_run))
                observed[name] = final_state(runtime)
                if mode == "worker":
                    features[name] = live_features(runtime)
                if mode == "online" and event.invocation_id is not None and event.kind in (
                    RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END,
                ):
                    estimate = runtime._online_tool_timing.estimate(
                        event.workflow_id, event.invocation_id, now_ms=event.ts_ms,
                    )
                    predicted[name] = asdict(estimate) if estimate is not None else None
                    estimates[name] += estimate is not None
            if observed["baseline"] != observed["candidate"] or (
                predicted and predicted["baseline"] != predicted["candidate"]
            ) or features and features["baseline"] != features["candidate"]:
                raise AssertionError(f"event state changed: {event.event_id}")
        if runtimes["candidate"].physical_ledger.pending_count:
            raise AssertionError("CPU event replay issued a physical command")
        if mode == "online" and any(
            (runtime._boundary_history or runtime._tool_metadata or runtime._tool_metadata_by_run)
            for runtime in (runtimes["candidate"],)
        ):
            raise AssertionError("online timing retained unused worker features")
        return {
            "every_event_state_and_online_estimate_equal": True,
            "live_worker_features_equal": mode == "worker",
            "boundary_classifier_calls": dict(boundary_calls),
            "metadata_group_highwater": dict(highwater),
            "supported_query_counts": dict(estimates),
        }
    finally:
        for name, runtime in runtimes.items():
            runtime.on_events.__wrapped__.__globals__["observed_boundary_action"] = original[name]
            runtime.close()


def measure(classes, events, *, mode, profiling, samples, clock):
    validation = validate(classes, events, mode=mode, profiling=profiling, clock=clock)
    costs = {name: {"thread_cpu_us": [], "wall_us": []} for name in classes}
    for sample in range(samples + 5):
        names = list(classes)
        if sample % 2:
            names.reverse()
        observed = {}
        for name in names:
            runtime = runtime_for(classes[name], mode=mode, profiling=profiling)
            cpu_total = wall_total = 0
            try:
                for event in events:
                    clock[0] = event.ts_ms / 1000.
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    runtime.on_events((event,))
                    cpu_total += time.thread_time_ns() - cpu
                    wall_total += time.perf_counter_ns() - wall
                observed[name] = final_state(runtime)
                if sample >= 5:
                    costs[name]["thread_cpu_us"].append(cpu_total / 1000. / len(events))
                    costs[name]["wall_us"].append(wall_total / 1000. / len(events))
            finally:
                runtime.close()
        if observed["baseline"] != observed["candidate"]:
            raise AssertionError("timed replay changed final causal/online/physical state")
    summaries = {
        name: {kind: {**distribution(values), "samples_us": values}
               for kind, values in clocks.items()}
        for name, clocks in costs.items()
    }
    return {
        "mode": mode, "profiling": profiling, **validation,
        "costs_per_event": summaries,
        "p50_thread_cpu_reduction_fraction": 1. - (
            summaries["candidate"]["thread_cpu_us"]["p50_us"]
            / summaries["baseline"]["thread_cpu_us"]["p50_us"]
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="52ccccb")
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.samples < 1:
        parser.error("positive samples and a fresh output are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/predictor/online_workload_timing.py",
        "beliefkv/predictor/tool_survival.py", "beliefkv/predictor/composer.py",
        "beliefkv/control/causal_graph.py", "beliefkv/core/events.py",
        "beliefkv/runtime/hotpath_timing.py",
        "scripts/benchmark_online_event_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py", "scripts/benchmark_prefetch_control_cpu.py",
    )
    sources = {path: source(ROOT / path)["sha256"] for path in paths}
    baseline, bindings = baseline_runtime(args.baseline)
    classes = {"baseline": baseline, "candidate": NativeAdmissionRuntime}
    traces = sorted(args.arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    if not traces:
        parser.error("no complete workflow traces found")
    cases, clock = [], [0.]
    with patch("time.monotonic", lambda: clock[0]):
        for path in traces:
            events, binding = trace_events(path)
            for mode in ("online", "worker"):
                for profiling in (False, True):
                    cases.append({
                        "trace": binding,
                        **measure(classes, events, mode=mode, profiling=profiling,
                                  samples=args.samples, clock=clock),
                    })
    if any(source(ROOT / path)["sha256"] != digest for path, digest in sources.items()):
        raise RuntimeError("a measured source changed")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": bindings,
        "candidate_source_sha256": sources, "samples": args.samples, "warmup_samples": 5,
        "cases": cases,
        "limitations": [
            "Each workflow is replayed with its own history; cross-workflow event order is not reconstructed.",
            "Each event is delivered individually; original wire batch boundaries are unavailable.",
            "Validation, state serialization, query checks and counted classifiers are outside timed handlers.",
            "No native requests/sessions are bound; online estimates are checked directly outside timing.",
            "Worker mode retains its feature bookkeeping using a stub; no model process is executed.",
            "Empty run groups and groups of ended workflows are intentionally retired, not prediction inputs.",
            "No socket, writer I/O, GPU, DMA, service consumption, prefetch coverage or JCT is measured.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "workflows": len(traces), "cases": len(cases),
        "p50_thread_cpu_reduction_ranges": {
            mode: (
                min(case["p50_thread_cpu_reduction_fraction"]
                    for case in cases if case["mode"] == mode),
                max(case["p50_thread_cpu_reduction_fraction"]
                    for case in cases if case["mode"] == mode),
            ) for mode in ("online", "worker")
        },
    }, indent=2))


if __name__ == "__main__":
    main()
