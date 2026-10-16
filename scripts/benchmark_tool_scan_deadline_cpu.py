#!/usr/bin/env python3
"""Compare complete CPU maintenance/dispatch cycles for tool-window deadlines."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import beliefkv.runtime.sglang_v0520_runtime as runtime_module
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime


SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"


def baseline_class(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{SOURCE}"], cwd=ROOT, text=True,
    )
    original = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "NativeAdmissionRuntime"
    )
    original.name = "BaselineToolScanRuntime"
    original.bases = [ast.Name(id="NativeAdmissionRuntime", ctx=ast.Load())]
    original.body = [
        node for node in original.body
        if isinstance(node, ast.FunctionDef) and node.name == "_roll_tool_prefetch"
    ]
    if len(original.body) != 1:
        raise ValueError("missing baseline tool scanner")
    namespace = dict(vars(runtime_module))
    tree = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        original,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), f"{revision}:{SOURCE}", "exec"), namespace)
    return namespace[original.name], hashlib.sha256(source.encode()).hexdigest()


def fixture(cls, workflows, profiling, issued_ms, eligible):
    runtime, requests = synthetic_runtime(
        cls, CausalFrontierScheduler, workflows, 16, profiling=profiling,
    )
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_tool_prefetch = True
    runtime.prefetch_lead_ms = 500.
    runtime._model_worker = NS(poll=lambda: (), disabled=False, close=lambda: None)
    probes = []
    for request in requests:
        key = runtime.visible[request.rid]
        invocation = runtime.graph.invocations[key.invocation_id]
        invocation.state = InvocationState.WAIT_TOOL
        runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
            key, 100., 300. if eligible else 30_000., 60_000.,
            issued_ms, issued_ms + 300_000., runtime.predictor_sha256,
            invocation.updated_ts_ms,
            ((100., .1), (500., .9)) if eligible
            else ((1000., .1), (60_000., .9)),
        )
    runtime._wait_prefetch_opportunity = lambda key: probes.append(key.request_id)
    return runtime, probes


def measure(baseline, *, workflows, mode, profiling, iterations):
    clock = [time.monotonic() + 1.]
    variants = {
        name: fixture(cls, workflows, profiling, clock[0] * 1000., mode == "eligible")
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    costs = {name: {"wall": [], "thread_cpu": []} for name in variants}
    allowed = {
        "tool_prefetch_scans", "tool_prefetch_scan_deferred",
        "tool_prefetch_not_in_time_window", "tool_prefetch_survival_deferred",
        "tool_prefetch_scan_deadline_coalesced", "tool_prefetch_scan_deadline_advanced",
    }
    try:
        with patch.object(runtime_module.time, "monotonic", lambda: clock[0]):
            next_refresh = clock[0] + .1
            for iteration in range(iterations + 20):
                clock[0] += .003
                if mode != "static_far" and clock[0] >= next_refresh:
                    for runtime, _ in variants.values():
                        for hint in tuple(runtime.tool_wait_hints.values()):
                            runtime._accept_tool_wait(replace(
                                hint, issued_monotonic_ms=clock[0] * 1000.,
                                expires_monotonic_ms=clock[0] * 1000. + 300_000.,
                            ))
                    next_refresh = clock[0] + .1
                outputs = {}
                names = list(variants)
                if iteration % 2:
                    names.reverse()
                for name in names:
                    runtime, probes = variants[name]
                    probes.clear()
                    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
                    runtime.scheduler_step(defer_observations=True)
                    runtime.dispatch_after_forward()
                    elapsed = {
                        "thread_cpu": (time.thread_time_ns() - cpu) / 1e6,
                        "wall": (time.perf_counter_ns() - wall) / 1e6,
                    }
                    if iteration >= 20:
                        for field, value in elapsed.items():
                            costs[name][field].append(value)
                    outputs[name] = {
                        "probes": tuple(probes),
                        "pending": runtime.physical_ledger.pending_count,
                        "graph_version": runtime.graph.graph_version,
                        "counters": {
                            key: value for key, value in runtime.counts.items()
                            if key not in allowed
                        },
                    }
                if outputs["baseline"] != outputs["candidate"]:
                    raise AssertionError((mode, iteration, outputs))
        summaries = {
            name: {field: distribution(values) for field, values in fields.items()}
            for name, fields in costs.items()
        }
        return {
            "workflows": workflows, "mode": mode, "profiling": profiling,
            "iterations": iterations, "decision_clock_step_ms": 3.,
            "complete_maintenance_dispatch_cycles_compared": True,
            "eligible_native_probe_sequences_and_non_scan_counters_equal": True,
            "tool_scans": {
                name: runtime.counts["tool_prefetch_scans"]
                for name, (runtime, _) in variants.items()
            },
            "costs": summaries,
            "thread_cpu_reduction_fraction": 1 - (
                summaries["candidate"]["thread_cpu"]["mean_ms"]
                / summaries["baseline"]["thread_cpu"]["mean_ms"]
            ),
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    baseline, digest = baseline_class(args.baseline_revision)
    cases = [
        measure(baseline, workflows=count, mode=mode, profiling=profiling,
                iterations=args.iterations)
        for count in (24, 156)
        for mode in ("static_far", "refreshed_far", "eligible")
        for profiling in (False, True)
    ]
    report = {
        "scope": (
            "Complete maintenance/dispatch CPU fixture cycles with 16 rounds of graph "
            "history. Native inspection is a recorded no-target stub; model inference, "
            "writer I/O, native allocation, DMA, GPU and JCT are not measured. "
            "Fresh forecasts are accepted between measured cycles every 102ms. "
            "Scan/defer counters intentionally differ; candidate native-probe sequences "
            "and all other counters are compared on every tick."
        ),
        "baseline_revision": args.baseline_revision, "baseline_runtime_sha256": digest,
        "candidate_runtime_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": cases,
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps([
        {field: case[field] for field in (
            "workflows", "mode", "profiling", "tool_scans", "thread_cpu_reduction_fraction",
        )} for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
