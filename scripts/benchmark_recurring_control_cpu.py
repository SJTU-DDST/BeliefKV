#!/usr/bin/env python3
"""Compare recurring runtime control with pinned code and real empty Unix sockets."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import synthetic_runtime
from scripts.benchmark_prefetch_control_cpu import compare, registration_case


def physical_state(ledger):
    return {
        "pending": [asdict(value[0]) for value in ledger._pending.values()],
        "nodes": sorted(ledger._pending_nodes),
        "bytes": dict(ledger._pending_action_bytes),
        "counts": dict(ledger._pending_action_counts),
        "owners": sorted(ledger.pending_h2d_contexts),
    }


def command(index=0, *, children=1):
    return PhysicalActionExpectation(
        f"load-{index}", "PREFETCH_GPU", f"context-{index}", 0,
        tuple(PhysicalChildExpectation(
            index * 16 + child * 2, (index * 16 + child * 2,),
            (("kv", 20),), 20,
        ) for child in range(children)),
        (("kv", 10), ("mamba", 5)), session_id=f"session-{index}", session_generation=1,
    )


def validation_cases(baseline):
    baseline_ledger = baseline._roll_tool_prefetch.__globals__["PhysicalTransactionLedger"]
    variants = {"baseline": baseline_ledger, "candidate": PhysicalTransactionLedger}
    records = []
    amounts = (
        (("kv", 20),), (("mamba", 15),), (("kv", 20), ("mamba", 15)),
        (("mamba", 0), ("kv", 20)), (["kv", 20],), (),
        (("kv", True),), (("kv", -10),), (("kv", 20.0),), (("kv", 11),),
        (("mamba", 6),), (("kv", 20), ("kv", 0)),
        (("mamba", 5), ("mamba", 5)), (("swa", 20),), (("", 0),),
        (([], 10),), (("kv",),), ("kv",),
    )
    for children in (1, 2):
        for pools in amounts:
            original = command(children=children)
            part = replace(original.children[-1], pool_bytes=pools, num_bytes=35)
            expected = replace(original, children=(*original.children[:-1], part))
            outcomes = {}
            for name, cls in variants.items():
                ledger = cls()
                try:
                    ledger.register(expected)
                    accepted = True
                except Exception as error:
                    if type(error).__name__ != "PhysicalReceiptError":
                        raise
                    accepted = False
                outcomes[name] = {"accepted": accepted, "state": physical_state(ledger)}
            if outcomes["baseline"] != outcomes["candidate"]:
                raise AssertionError(f"registration validation changed: {expected}")
            records.append({
                "children": children, "pool_bytes": pools,
                "accepted": outcomes["candidate"]["accepted"],
                "state_and_validation_equal": True,
            })
    return records


def socket_case(baseline, *, samples, profiling):
    servers = {}
    with tempfile.TemporaryDirectory(prefix="bkv-empty-control-") as temporary:
        try:
            for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
                server_cls = cls._roll_tool_prefetch.__globals__["RuntimeEventDatagramServer"]
                servers[name] = server_cls(
                    Path(temporary) / f"{name}.sock", lambda events: None,
                    timing=HotpathTiming(enabled=profiling),
                )
            costs = compare(
                {name: server.drain for name, server in servers.items()}, samples=samples,
            )
            if any(server.drain() != () or server._acks for server in servers.values()):
                raise AssertionError("empty receive created a delivery or ACK")
            return {
                "case": "real_empty_socket_drain", "profiling": profiling,
                "costs": costs, "no_delivery_or_ack": True,
            }
        finally:
            for server in servers.values():
                server.close()


def recurring_case(baseline, *, samples, workflows, running, pending, profiling, due):
    variants = {}
    with tempfile.TemporaryDirectory(prefix="bkv-recurring-control-") as temporary:
        try:
            for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
                runtime, queue = synthetic_runtime(
                    cls, CausalFrontierScheduler, workflows, 16, profiling=profiling,
                )
                variants[name] = runtime, NS(reqs=queue[:running])
                runtime._tool_timing_only = True
                runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
                runtime.enable_prepare_host = False
                runtime._prediction_maintenance_interval_ms = 100.
                runtime._prediction_maintenance_graph_version = runtime.graph.graph_version
                runtime._prediction_maintenance_next_ms = 1e100
                runtime._tool_refresh_next_ms = 1e100
                for invocation in runtime.graph.invocations.values():
                    if invocation.state is InvocationState.WAIT_JOIN:
                        invocation.state = InvocationState.READY
                names = cls._roll_tool_prefetch.__globals__
                runtime.event_server = names["RuntimeEventDatagramServer"](
                    Path(temporary) / f"{name}.sock", runtime.on_events,
                    timing=runtime._hotpath_timing,
                )
                for index in range(pending):
                    runtime.physical_ledger.register(command(index))
            before = {
                name: physical_state(runtime.physical_ledger)
                for name, (runtime, _) in variants.items()
            }

            def cycle(runtime, batch):
                if due:
                    runtime._prediction_maintenance_next_ms = 0.
                runtime.scheduler_step(defer_observations=True)
                runtime.dispatch_after_forward(running_batch=batch)
                runtime.on_batch_completed(batch)

            costs = compare({
                name: lambda runtime=runtime, batch=batch: cycle(runtime, batch)
                for name, (runtime, batch) in variants.items()
            }, samples=samples)
            states, counters = {}, {}
            for name, (runtime, _) in variants.items():
                current = physical_state(runtime.physical_ledger)
                if current != before[name] or runtime.physical_disabled or (
                    runtime.completed_physical_actions
                ):
                    raise AssertionError("recurring control changed a physical reservation")
                states[name] = {
                    "physical": current, "visible": sorted(runtime.visible),
                    "tokens": dict(runtime._context_tokens),
                    "semantic_revision": runtime.semantic_revision,
                    "positive_action_counts": {
                        key: value for key, value in runtime.counts.items()
                        if value and not key.startswith("prediction_maintenance_")
                    },
                }
                counters[name] = {
                    key: runtime.counts[key] for key in (
                        "prediction_maintenance_sweeps", "prediction_maintenance_deferred",
                        "physical_expired",
                    )
                }
            if states["baseline"] != states["candidate"]:
                raise AssertionError("recurring control changed visible or physical state")
            return {
                "case": "scheduler_dispatch_and_batch_completion", "workflows": workflows,
                "running_requests": running, "pending_commands": pending,
                "profiling": profiling, "maintenance_forced_due": due,
                "configured_maintenance_interval_ms": 100.,
                "costs": costs, "state_and_actions_equal": True,
                "maintenance_diagnostic_counts": counters,
            }
        finally:
            for runtime, _ in variants.values():
                runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="fc9fea7")
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output")
    paths = (
        "beliefkv/runtime/event_channel.py", "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py", "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/hotpath_timing.py", "scripts/benchmark_recurring_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py", "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_native_shared_path.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    validation = validation_cases(baseline)
    cases = [
        *(socket_case(baseline, samples=args.samples, profiling=profiling)
          for profiling in (False, True)),
        *(recurring_case(
            baseline, samples=args.samples, workflows=workflows, running=running,
            pending=pending, profiling=profiling, due=due,
        ) for workflows, running in ((12, 8), (24, 8), (156, 48))
          for pending in (0, 16) for profiling in (False, True) for due in (False, True)),
        *(registration_case(
            baseline, samples=args.samples, commands=commands, children=children,
            pools=pools, published_nodes=nodes,
        ) for commands, children, nodes in ((1, 1, 1), (64, 1, 1), (16, 4, 1), (16, 1, 2))
          for pools in ("full", "mamba", "hybrid")),
    ]
    for case in cases:
        case["p50_thread_cpu_reduction_fraction"] = (
            1. - case["costs"]["candidate"]["p50_us"] / case["costs"]["baseline"]["p50_us"]
        )
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("benchmark sources changed")
    report = {
        "scope": "CPU runtime/ledger with real Unix sockets; no serving, CUDA, DMA or JCT",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "candidate_source_sha256": sources, "samples": args.samples,
        "warmup_samples": 20, "cases": cases, "validation_cases": validation,
        "limitations": [
            "Empty prediction inventories and physical pending populations are controlled scenes.",
            "Forced-due and deferred scenes are measured separately, not their online mixture.",
            "Unexpired physical reservations do not represent native DMA or active service leases.",
            "Empty sweeps/counters are intentionally removed; action/state comparisons exclude those diagnostics.",
            "Microseconds and controlled fractions are not throughput, JCT or prediction-accuracy gains.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "timing_cases": len(cases),
        "validation_cases": len(validation),
        "p50_reduction_range": (
            min(case["p50_thread_cpu_reduction_fraction"] for case in cases),
            max(case["p50_thread_cpu_reduction_fraction"] for case in cases),
        ),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
