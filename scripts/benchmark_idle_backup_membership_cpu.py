#!/usr/bin/env python3
"""Compare complete idle-backup dispatch with live queued-owner validation."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import MethodType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime import sglang_v0520_runtime as module
from tests.test_idle_queued_full_backup import fixture


def baseline_method(revision):
    source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{revision}:beliefkv/runtime/sglang_v0520_runtime.py"],
        text=True,
    )
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
               and node.name == "NativeAdmissionRuntime")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_idle_backup_queued_owner")
    namespace = dict(vars(module))
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[],
    )), "<baseline queued membership>", "exec"), namespace)
    return namespace[method.name], hashlib.sha256(source.encode()).hexdigest()


def setup(method, *, size, kind):
    runtime, _, key, request, cache = fixture()
    if method is not None:
        runtime._idle_backup_queued_owner = MethodType(method, runtime)
    queue = []
    for index in range(size - 1):
        rid = f"other-{index}"
        queue.append(NS(rid=rid))
        runtime.visible[rid] = replace(key, request_id=rid)
    runtime._capacity_waiting_queue = [*queue, request]
    if kind == "waiting":
        runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_TOOL
    elif kind == "allocated":
        request.kv.req_pool_idx = 0
    owners = ((key.session_id, key.session_generation),)
    outcomes = []

    def prepare():
        outcomes.extend(runtime._live_backup_full_owners(owners) for _ in range(32))
        return ((71, 1.0, 4),) if any(outcomes) else ()

    cache.prepare_beliefkv_idle_full = prepare
    return runtime, outcomes


def measure(baseline, *, size, kind, samples):
    states = {
        "baseline": setup(baseline, size=size, kind=kind),
        "candidate": setup(None, size=size, kind=kind),
    }
    readings = {name: {"thread_cpu_us": [], "wall_us": []} for name in states}
    try:
        for iteration in range(samples + 20):
            order = list(states)
            if iteration % 2:
                order.reverse()
            snapshots = {}
            for name in order:
                runtime, outcomes = states[name]
                runtime._idle_prepare_next_ms = 0
                runtime.counts.clear()
                runtime._cold_full_backups.clear()
                outcomes.clear()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                runtime.dispatch_idle_prepare()
                costs = {
                    "wall_us": (time.perf_counter_ns() - wall) / 1000,
                    "thread_cpu_us": (time.thread_time_ns() - cpu) / 1000,
                }
                if iteration >= 20:
                    for clock, cost in costs.items():
                        readings[name][clock].append(cost)
                snapshots[name] = (
                    tuple(outcomes), dict(runtime.counts), dict(runtime._cold_full_backups),
                    runtime._idle_backup_queue_scope, runtime._idle_backup_queued_requests,
                    runtime.physical_ledger.pending_count,
                )
            if snapshots["baseline"] != snapshots["candidate"]:
                raise AssertionError((size, kind, snapshots))
            if snapshots["candidate"][0] != (kind != "allocated",) * 32:
                raise AssertionError("native eligibility changed")
            if snapshots["candidate"][3:] != (False, None, 0):
                raise AssertionError("backup proof escaped its synchronous scope")
        costs = {
            name: {
                clock: {"mean": mean(values), "p50": median(values), "samples": values}
                for clock, values in clocks.items()
            } for name, clocks in readings.items()
        }
        before = costs["baseline"]["thread_cpu_us"]["p50"]
        after = costs["candidate"]["thread_cpu_us"]["p50"]
        return {
            "queue_size": size, "kind": kind, "owner_checks": 32, "costs": costs,
            "thread_cpu_p50_reduction_percent": 100 * (1 - after / before),
            "outcomes_counters_backups_scope_and_pending_equal": True,
        }
    finally:
        for runtime, _ in states.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="4339be2c")
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 3 or args.output.exists():
        parser.error("positive samples and a fresh report are required")
    method, baseline_hash = baseline_method(args.baseline)
    cases = []
    for size in (4, 8, 24, 64, 128, 512):
        for kind in ("queued", "waiting", "allocated"):
            case = measure(method, size=size, kind=kind, samples=args.samples)
            cases.append(case)
            print(
                f"queue={size} {kind}: CPU {case['thread_cpu_p50_reduction_percent']:.2f}% lower",
                flush=True,
            )
    report = {
        "baseline_revision": args.baseline, "baseline_runtime_sha256": baseline_hash,
        "samples": args.samples, "cases": cases,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "scripts/benchmark_idle_backup_membership_cpu.py",
                "beliefkv/runtime/sglang_v0520_runtime.py",
                "tests/test_idle_queued_full_backup.py", "tests/test_queued_full_donor.py",
            )
        },
        "scope": [
            "Alternating complete runtime idle dispatch with 32 real owner/native-request validations.",
            "Native copy selection is a scalar CPU stand-in. No transfer, allocator or GPU/JCT/throughput benefit is measured.",
            "Native list membership checks the saved position by object identity, with the original bounded scan on a changed queue.",
            "Waiting-owner and allocated-request controls retain outcomes; queue scope, backups, counters and pending actions match each iteration.",
            "Queue sizes and repeated-owner frequency are controlled fixtures, not measured online workload frequencies.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
